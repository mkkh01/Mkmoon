# Render deployment guide

## Services

The repository includes `render.yaml` with:

1. `mkmoon-api`: FastAPI web service with `/health` and `/ready`.
2. `mkmoon-worker`: background worker that polls Binance public market data and writes Paper decisions.

Both services intentionally default to `TRADING_MODE=paper` and `LIVE_TRADING_ENABLED=false`.

## Environment variables

Set these in Render's environment settings, not in the repository:

| Variable | Required | Value / policy |
|---|---:|---|
| `DATABASE_URL` | Yes | Private PostgreSQL connection string for Supabase |
| `REDIS_URL` | Yes | Redis connection string |
| `BINANCE_BASE_URL` | Yes | `https://api.binance.com` |
| `BINANCE_DATA_BASE_URL` | Yes | `https://data-api.binance.vision` |
| `SYMBOLS` | Yes | Explicit allowlist such as `BTCUSDT,ETHUSDT,SOLUSDT` |
| `TRADING_MODE` | Yes | `paper` initially |
| `LIVE_TRADING_ENABLED` | Yes | `false` initially |
| `BINANCE_API_KEY` | No for Paper | Add only for a later private-account phase |
| `BINANCE_API_SECRET` | No for Paper | Add only for a later private-account phase |

Use separate Render environment groups for Paper, Shadow, and any future Live service. Never reuse a Live key in development. Do not grant withdrawal permission to a trading key.

## Deploy steps

Create a new Blueprint in Render from the GitHub repository `mkkh01/Mkmoon`, review the two services, enter `DATABASE_URL` and `REDIS_URL` as secret values, and deploy. Confirm that the Web service returns `200` from `/health` and `/ready` before starting the worker.

The worker does not send orders. It reads only public Binance endpoints, keeps closed candles, evaluates the deterministic decision engine, stores decisions in PostgreSQL, and publishes decision JSON to Redis when configured.

## Safe promotion gates

Do not change `TRADING_MODE` to `live` merely because the service is healthy. Promote only after Replay parity, historical Backtest with costs, Paper/Backtest drift checks, Shadow monitoring, an independently reviewed risk configuration, and a kill-switch procedure. A future live deployment must use a separate Render service and a separate restricted Binance API key.
