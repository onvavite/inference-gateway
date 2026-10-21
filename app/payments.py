"""Recharges en crypto — pas de Stripe, pas de chargeback possible.

Flux MVP :
1. Le client crée un ordre de recharge (montant USD voulu, chaîne, token).
2. Le serveur répond : adresse trésorerie + montant EXACT à envoyer
   (montant + suffixe aléatoire unique => identification sans adresse dédiée).
3. Le client paie puis soumet son tx_hash.
4. Le serveur vérifie la transaction on-chain via RPC public (receipt + log
   Transfer ERC-20 : bon contrat, bon destinataire, montant >= attendu).
5. Vérification OK => ordre 'paid' + crédits ajoutés au compte (atomique).

Chaînes supportées : Base (USDC), BNB Chain (USDT, USDC).
"""
import os
import secrets
import time
import uuid

import httpx

from . import db

CHAINS = {
    "base": {
        "rpc": os.environ.get("BASE_RPC", "https://mainnet.base.org"),
        "treasury_env": "TREASURY_BASE",
        "tokens": {"USDC": ("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 6)},
    },
    "bnb": {
        "rpc": os.environ.get("BNB_RPC", "https://bsc-dataseed.binance.org"),
        "treasury_env": "TREASURY_BNB",
        "tokens": {
            "USDT": ("0x55d398326f99059fF775485246999027B3197955", 18),
            "USDC": ("0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", 18),
        },
    },
}

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def create_order(account_id: str, usd_amount: float, chain: str, token: str) -> dict:
    cfg = CHAINS[chain]
    treasury = os.environ.get(cfg["treasury_env"])
    if not treasury:
        raise RuntimeError(f"Adresse trésorerie non configurée pour {chain}")
    if token not in cfg["tokens"]:
        raise ValueError("Token non supporté sur cette chaîne")
    # suffixe unique 0.01..0.99 pour identifier le paiement sur une adresse partagée
    suffix = secrets.randbelow(99) + 1
    expected = round(usd_amount + suffix / 1000.0, 6)
    oid = uuid.uuid4().hex
    now = int(time.time())
    c = db.conn()
    c.execute(
        "INSERT INTO topup_orders (id, account_id, chain, token, expected_amount, address, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (oid, account_id, chain, token, str(expected), treasury, now, now + 3600),
    )
    c.close()
    return {
        "order_id": oid,
        "chain": chain,
        "token": token,
        "send_to": treasury,
        "send_amount": expected,
        "expires_in_seconds": 3600,
    }


def _rpc(rpc_url: str, method: str, params: list):
    with httpx.Client(timeout=20) as client:
        r = client.post(rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    r.raise_for_status()
    out = r.json()
    if "error" in out:
        raise RuntimeError(out["error"])
    return out["result"]


def verify_and_credit(order_id: str, tx_hash: str) -> dict:
    c = db.conn()
    order = c.execute("SELECT * FROM topup_orders WHERE id = ?", (order_id,)).fetchone()
    c.close()
    if not order:
        raise ValueError("Ordre introuvable")
    if order["status"] == "paid":
        return {"status": "already_paid"}
    if order["status"] != "pending":
        raise ValueError("Ordre non payable (expiré ou déjà traité)")

    cfg = CHAINS[order["chain"]]
    token_addr, decimals = cfg["tokens"][order["token"]]
    receipt = _rpc(cfg["rpc"], "eth_getTransactionReceipt", [tx_hash])
    if not receipt or receipt.get("status") != "0x1":
        raise ValueError("Transaction introuvable ou échouée")

    treasury = order["address"].lower()
    expected_units = int(float(order["expected_amount"]) * (10 ** decimals))
    for log in receipt.get("logs", []):
        if log["address"].lower() != token_addr.lower():
            continue
        topics = log.get("topics", [])
        if len(topics) < 3 or topics[0].lower() != TRANSFER_TOPIC:
            continue
        to_addr = "0x" + topics[2][-40:]
        if to_addr.lower() != treasury:
            continue
        value = int(log["data"], 16)
        if value < expected_units:
            raise ValueError("Montant reçu inférieur au montant attendu")
        # Paiement confirmé : marquer + créditer (1 $ = 100 crédits)
        credits_micro = int(float(order["expected_amount"]) * 100) * db_micro_per_credit()
        c = db.conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            cur = c.execute(
                "UPDATE topup_orders SET status = 'paid', tx_hash = ? WHERE id = ? AND status = 'pending'",
                (tx_hash, order_id),
            )
            if cur.rowcount != 1:
                c.execute("ROLLBACK")
                return {"status": "already_paid"}
            c.execute("COMMIT")
        finally:
            c.close()
        db.credit_topup(order["account_id"], credits_micro, f"topup:{order_id}")
        return {"status": "paid", "credits_added": credits_micro / db_micro_per_credit()}
    raise ValueError("Aucun transfert correspondant trouvé dans cette transaction")


def db_micro_per_credit() -> int:
    return 1_000_000
