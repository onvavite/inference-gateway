# Inference Gateway

An OpenAI-compatible AI inference gateway with multi-provider failover,
atomic prepaid credit metering, and crypto top-ups.

Built for indie developers and small teams who want **one API key and one
prepaid balance** across several inference providers, with hard budget
control: a request is only forwarded once its cost has been reserved, so a
balance can never go negative — even under parallel load.

## Features

- `POST /v1/chat/completions` — OpenAI-compatible chat completions endpoint
- `GET /v1/models` — public model catalogue with credit pricing
- **Multi-provider routing with failover** — if the primary upstream fails,
  the request is retried on the next configured provider
- **Atomic credit ledger (SQLite)** — reserve-before-call, settle-after-call;
  failed upstream calls are refunded automatically. Concurrency-tested:
  30 parallel reservations, never a negative balance
- **Client accounts & API keys** — keys are stored SHA-256 hashed, never in
  plain text
- **Prepaid top-ups in crypto** — each order gets a unique exact amount;
  payment is verified on-chain from the transaction hash via public RPC
  (Base USDC, BNB Chain USDT/USDC)
- Environment-based configuration — no secrets in code

## Quickstart

```bash
pip install -r requirements.txt

export ADMIN_TOKEN="choose-a-strong-admin-token"
export DEEPSEEK_API_KEY="..."        # and/or OPENROUTER_API_KEY
export TREASURY_BASE="0x..."         # and/or TREASURY_BNB

uvicorn app.main:app --port 8080
```

Create an account and an API key with the admin token, top up, then call
the gateway exactly like the OpenAI API:

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer <your-gateway-key>" \
  -H "Content-Type: application/json" \
  -d '{"model": "deepseek-v4.1-flash", "messages": [{"role": "user", "content": "Hello"}]}'
```

## Pricing model

Usage is metered in prepaid credits (1 credit = 0.01 USD). Per-model credit
prices are defined in `app/pricing.py`; upstream routing and failover order
in `app/upstreams.py`. Both are plain Python — adapt them to your own
providers and pricing.

## Status

Working MVP, tested locally (including the concurrency test above).
Roadmap: streaming (SSE) responses, automatic payment watching, and a
self-service signup page.

## License

MIT — see [LICENSE](LICENSE).

Maintained by [@onvavite](https://github.com/onvavite).
