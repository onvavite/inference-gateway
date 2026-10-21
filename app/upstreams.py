"""Routage multi-amonts avec failover.

Si un amont tombe (429, 5xx, timeout) ou est banni, le trafic bascule sur le
suivant sans que le client voie rien. Les clés amont vivent dans l'environnement,
jamais dans le code ni dans les logs.
"""
import os
import httpx

UPSTREAMS = [
    {
        "name": "deepseek",
        "base": os.environ.get("DEEPSEEK_BASE", "https://api.deepseek.com"),
        "key_env": "DEEPSEEK_API_KEY",
        # notre nom public -> nom chez l'amont
        "models": {"deepseek-v4.1-flash": "deepseek-flash", "deepseek-v4.1-pro": "deepseek-pro"},
    },
    {
        "name": "openrouter",
        "base": os.environ.get("OPENROUTER_BASE", "https://openrouter.ai/api/v1"),
        "key_env": "OPENROUTER_API_KEY",
        "models": {
            "deepseek-v4.1-flash": "deepseek/deepseek-v4.1-flash-1017",
            "deepseek-v4.1-pro": "deepseek/deepseek-v4.1-pro",
        },
    },
]

TIMEOUT = httpx.Timeout(120.0, connect=10.0)


def available():
    return [u for u in UPSTREAMS if os.environ.get(u["key_env"])]


async def chat_completion(model: str, payload: dict):
    """Essaie chaque amont dans l'ordre. Retourne (réponse_json, nom_amont)."""
    errors = []
    for u in available():
        upstream_model = u["models"].get(model)
        if not upstream_model:
            continue
        body = dict(payload)
        body["model"] = upstream_model
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, trust_env=False) as client:
                r = await client.post(
                    f"{u['base']}/chat/completions",
                    headers={"Authorization": f"Bearer {os.environ[u['key_env']]}"},
                    json=body,
                )
            if r.status_code == 429 or r.status_code >= 500:
                errors.append(f"{u['name']}: HTTP {r.status_code}")
                continue
            r.raise_for_status()
            return r.json(), u["name"]
        except Exception as e:  # réseau, timeout, 4xx dur -> on tente le suivant
            errors.append(f"{u['name']}: {type(e).__name__}")
            continue
    raise RuntimeError("Tous les amonts ont échoué: " + "; ".join(errors or ["aucun amont configuré"]))
