"""Gateway d'inférence — endpoint compatible OpenAI, crédits maison, paiement crypto.

Lancer : uvicorn app.main:app --port 8080
Env requises : ADMIN_TOKEN, au moins une clé amont (DEEPSEEK_API_KEY / OPENROUTER_API_KEY),
TREASURY_BASE et/ou TREASURY_BNB pour les recharges.
"""
import hashlib
import os
import secrets

from fastapi import FastAPI, Header, HTTPException, Request

from . import db, payments, pricing, upstreams

app = FastAPI(title="Inference Gateway", version="0.1.0")
db.init()


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _auth(authorization: str | None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Clé API manquante")
    acct = db.account_for_key(_hash(authorization[7:]))
    if not acct:
        raise HTTPException(401, "Clé API invalide")
    return acct


def _admin(x_admin_token: str | None):
    expected = os.environ.get("ADMIN_TOKEN", "")
    if not expected or not x_admin_token or not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(403, "Admin requis")


@app.get("/health")
def health():
    return {"ok": True, "upstreams": [u["name"] for u in upstreams.available()]}


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": m, "object": "model"} for m in pricing.MODEL_PRICES]}


@app.post("/accounts")
def create_account(payload: dict, x_admin_token: str | None = Header(None)):
    _admin(x_admin_token)
    email = (payload or {}).get("email")
    if not email:
        raise HTTPException(400, "email requis")
    aid = db.create_account(email)
    raw_key = "sk-gw-" + secrets.token_urlsafe(32)
    db.add_key(aid, _hash(raw_key), "default")
    return {"account_id": aid, "api_key": raw_key, "balance_credits": 0}


@app.get("/balance")
def get_balance(authorization: str | None = Header(None)):
    acct = _auth(authorization)
    return {"balance_credits": acct["balance_micro"] / pricing.MICRO}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, authorization: str | None = Header(None)):
    acct = _auth(authorization)
    payload = await request.json()
    model = payload.get("model")
    if model not in pricing.MODEL_PRICES:
        raise HTTPException(400, f"Modèle inconnu: {model}")
    if payload.get("stream"):
        raise HTTPException(400, "streaming non supporté pour le moment")
    max_tokens = int(payload.get("max_tokens") or 4096)
    reserved = pricing.max_reserve_micro(model, max_tokens)
    if not db.reserve(acct["id"], reserved, f"chat:{model}"):
        raise HTTPException(402, "Solde insuffisant — rechargez vos crédits")
    try:
        result, upstream_name = await upstreams.chat_completion(model, payload)
    except Exception as e:
        db.settle(acct["id"], reserved, 0, "refund:upstream-fail")
        raise HTTPException(502, str(e))
    usage = result.get("usage") or {}
    actual = pricing.cost_micro(
        model,
        int(usage.get("prompt_tokens", 0)),
        int(usage.get("completion_tokens", 0)),
    )
    db.settle(acct["id"], reserved, actual, f"chat:{upstream_name}")
    result["model"] = model
    return result


@app.post("/topup/orders")
def topup_order(payload: dict, authorization: str | None = Header(None)):
    acct = _auth(authorization)
    try:
        return payments.create_order(
            acct["id"],
            float(payload["usd_amount"]),
            payload["chain"],
            payload["token"],
        )
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(503, str(e))


@app.post("/topup/{order_id}/submit-tx")
def topup_submit(order_id: str, payload: dict, authorization: str | None = Header(None)):
    _auth(authorization)
    try:
        return payments.verify_and_credit(order_id, payload["tx_hash"])
    except KeyError:
        raise HTTPException(400, "tx_hash requis")
    except ValueError as e:
        raise HTTPException(400, str(e))
