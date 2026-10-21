"""Tarification en crédits maison.

1 crédit = 0,01 $ de valeur faciale pour le client.
Le client achète des crédits (crypto), il ne voit jamais les prix amont.
La marge = écart entre le prix de vente du crédit-token et le coût amont réel.

Coûts amont de référence (vérifiés 2026-10-19) :
- DeepSeek officiel deepseek-flash (checkpoint 1017) : 0,15 $/M in, 0,60 $/M out
Prix de vente cible : x3 sur le coût amont (marge brute ~66 %).
"""
# prix client en crédits par million de tokens (in / out)
MODEL_PRICES = {
    "deepseek-v4.1-flash": {"in": 45, "out": 180},   # coût 0,15/0,60 $ -> vente 0,45/1,80 $
    "deepseek-v4.1-pro": {"in": 150, "out": 600},    # placeholder, à caler sur le coût réel
}

CREDIT_USD = 0.01  # 1 crédit = 0,01 $
MICRO = 1_000_000  # micro-crédits par crédit


def cost_micro(model: str, prompt_tokens: int, completion_tokens: int) -> int:
    p = MODEL_PRICES.get(model)
    if not p:
        return 0
    credits = (prompt_tokens * p["in"] + completion_tokens * p["out"]) / 1_000_000
    return int(credits * MICRO)


def max_reserve_micro(model: str, max_tokens: int) -> int:
    """Réserve avant appel : borne haute basée sur max_tokens demandé (défaut 4096)."""
    p = MODEL_PRICES.get(model)
    if not p:
        return 0
    credits = (max_tokens * max(p["in"], p["out"])) / 1_000_000
    # plancher 1 crédit pour couvrir le prompt
    return max(int(credits * MICRO), MICRO)
