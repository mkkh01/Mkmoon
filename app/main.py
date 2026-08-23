from __future__ import annotations

import logging
import re
import threading
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from time import time

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from app.adapters.binance_public import BinancePublicClient
from app.core.settings import get_settings
from app.storage.postgres import PostgresStore
from app.storage.redis import RedisStore


settings = get_settings()
postgres = PostgresStore(settings.database_url) if settings.database_url else None
redis_store = RedisStore(settings.redis_url) if settings.redis_url else None
binance_public = BinancePublicClient(settings.binance_base_url, settings.binance_data_base_url)
DASHBOARD_PATH = Path(__file__).with_name("static") / "index.html"
DASHBOARD_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT",
    "TRXUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "POLUSDT", "LTCUSDT", "BCHUSDT",
    "ATOMUSDT", "UNIUSDT", "ETCUSDT", "FILUSDT", "NEARUSDT", "APTUSDT", "OPUSDT",
    "ARBUSDT", "SUIUSDT", "INJUSDT", "XLMUSDT",
]


class DashboardLogHandler(logging.Handler):
    def __init__(self, max_records: int = 400) -> None:
        super().__init__()
        self.records: deque[dict[str, str | int]] = deque(maxlen=max_records)
        self._lock = threading.Lock()

    @staticmethod
    def _redact(message: str) -> str:
        message = re.sub(r"(?i)(?:postgres(?:ql)?|redis)://[^\\s]+", "[redacted-url]", message)
        return re.sub(r"(?i)(password|passwd|secret|token)=([^\\s,]+)", r"\\1=[redacted]", message)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            item = {
                "timestamp": int(record.created * 1000),
                "level": record.levelname,
                "logger": record.name,
                "message": self._redact(record.getMessage()),
            }
            with self._lock:
                self.records.append(item)
        except Exception:
            self.handleError(record)

    def snapshot(self, search: str = "", level: str = "", limit: int = 200) -> list[dict[str, str | int]]:
        search_upper = search.strip().upper()
        level_upper = level.strip().upper()
        with self._lock:
            records = list(self.records)
        return [
            item for item in reversed(records)
            if (not level_upper or str(item["level"]).upper() == level_upper)
            and (not search_upper or search_upper in str(item["message"]).upper() or search_upper in str(item["logger"]).upper())
        ][:limit]


log_buffer = DashboardLogHandler()
root_logger = logging.getLogger()
root_logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
if not any(isinstance(handler, DashboardLogHandler) for handler in root_logger.handlers):
    root_logger.addHandler(log_buffer)
logger = logging.getLogger("mkmoon.dashboard")


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("Starting Mkmoon API in %s mode", settings.trading_mode)
    if postgres:
        await postgres.connect()
        logger.info("PostgreSQL connection established")
    if redis_store:
        await redis_store.connect()
        logger.info("Redis connection established")
    logger.info("Paper-only guard active; live trading enabled=%s", settings.live_trading_enabled)
    yield
    await binance_public.aclose()
    if redis_store:
        await redis_store.close()
    if postgres:
        await postgres.close()
    logger.info("Mkmoon API stopped")


app = FastAPI(title="Mkmoon Binance Spot Engine", version="0.2.0", lifespan=lifespan)


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(DASHBOARD_PATH, media_type="text/html")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "environment": settings.app_env,
        "trading_mode": settings.trading_mode,
        "live_trading_enabled": settings.live_trading_enabled,
    }


@app.get("/ready")
async def ready() -> dict:
    if settings.app_env == "production":
        if postgres is None or postgres.pool is None or redis_store is None or redis_store.client is None:
            raise HTTPException(status_code=503, detail="Required service connections are not ready")
    return {"status": "ready"}


@app.get("/api/dashboard/summary")
async def dashboard_summary() -> dict:
    counts = {"total": 0, "open": 0, "closed": 0}
    decision_count = 0
    database_status = "not_configured"
    redis_status = "not_configured"
    errors: list[str] = []
    if postgres and postgres.pool:
        database_status = "connected"
        try:
            counts = await postgres.paper_order_counts()
            decision_count = len(await postgres.recent_decisions(limit=5))
        except Exception as exc:
            database_status = "error"
            errors.append("database query failed")
            logger.exception("Dashboard database query failed: %s", type(exc).__name__)
    if redis_store and redis_store.client:
        redis_status = "connected"
        try:
            await redis_store.client.ping()
        except Exception as exc:
            redis_status = "error"
            errors.append("redis ping failed")
            logger.exception("Dashboard Redis ping failed: %s", type(exc).__name__)
    return {
        "service": "online",
        "environment": settings.app_env,
        "trading_mode": settings.trading_mode,
        "live_trading_enabled": settings.live_trading_enabled,
        "database": database_status,
        "redis": redis_status,
        "orders": counts,
        "recent_decisions": decision_count,
        "dashboard_symbols": len(DASHBOARD_SYMBOLS),
        "server_time_ms": int(time() * 1000),
        "errors": errors,
    }


@app.get("/api/logs")
def application_logs(
    search: str = Query(default="", max_length=100),
    level: str = Query(default="", max_length=20),
    limit: int = Query(default=200, ge=1, le=400),
) -> list[dict[str, str | int]]:
    return log_buffer.snapshot(search=search, level=level, limit=limit)


@app.get("/api/decisions")
async def recent_decisions(
    limit: int = Query(default=50, ge=1, le=500),
    search: str = Query(default="", max_length=100),
) -> list[dict]:
    if postgres is None or postgres.pool is None:
        return []
    return await postgres.recent_decisions(limit, search)


@app.get("/api/paper-orders")
async def paper_orders(
    view: str = Query(default="all", pattern="^(all|open|closed)$"),
    search: str = Query(default="", max_length=100),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    if postgres is None or postgres.pool is None:
        return []
    return await postgres.recent_paper_orders(view=view, search=search, limit=limit, offset=offset)


@app.get("/api/market/tickers")
async def market_tickers() -> dict:
    try:
        tickers = await binance_public.ticker_prices(DASHBOARD_SYMBOLS)
        return {
            "source": "Binance public market data",
            "symbols_requested": len(DASHBOARD_SYMBOLS),
            "updated_at_ms": int(time() * 1000),
            "data": tickers,
        }
    except Exception as exc:
        logger.exception("Binance ticker request failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Binance public market data unavailable") from exc


@app.get("/api/cycles")
async def recent_cycles(
    limit: int = Query(default=30, ge=1, le=200),
    search: str = Query(default="", max_length=100),
) -> list[dict]:
    if postgres is None or postgres.pool is None:
        return []
    return await postgres.recent_cycles(limit=limit, search=search)


@app.get("/api/cycles/{cycle_id}")
async def cycle_detail(cycle_id: str) -> dict:
    if postgres is None or postgres.pool is None:
        raise HTTPException(status_code=503, detail="PostgreSQL is not ready")
    result = await postgres.cycle_detail(cycle_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Cycle not found")
    return result


@app.get("/api/dashboard/config")
def dashboard_config() -> dict:
    return {
        "symbols": DASHBOARD_SYMBOLS,
        "paper_only": settings.trading_mode == "paper" and not settings.live_trading_enabled,
        "poll_interval_seconds": settings.poll_interval_seconds,
        "timezone": settings.timezone,
    }

