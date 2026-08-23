from __future__ import annotations

import asyncio
import logging
import re
import secrets
import threading
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from time import time

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from app.adapters.binance_public import BinancePublicClient
from app.core.settings import get_settings
from app.integrations.telegram_bot import TelegramBot
from app.storage.postgres import PostgresStore
from app.storage.redis import RedisStore


settings = get_settings()
postgres = PostgresStore(settings.database_url) if settings.database_url else None
redis_store = RedisStore(settings.redis_url) if settings.redis_url else None
binance_public = BinancePublicClient(
    settings.binance_base_url,
    settings.binance_data_base_url,
    fallback_base_url=settings.binance_fallback_base_url,
)
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
        message = re.sub(r"(?i)(?:postgres(?:ql)?|redis)://[^\s]+", "[redacted-url]", message)
        return re.sub(r"(?i)(password|passwd|secret|token)=([^\s,]+)", r"\1=[redacted]", message)

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
paper_worker_state: dict[str, object] = {
    "enabled": settings.trading_mode == "paper" and not settings.live_trading_enabled,
    "running": False,
    "last_cycle_id": None,
    "last_cycle_started_at_ms": None,
    "last_cycle_finished_at_ms": None,
    "last_cycle_duration_ms": None,
    "last_cycle_status": None,
    "last_cycle_error": None,
    "last_cycle_data_source": None,
    "last_cycle_errors": [],
    "last_cycle_symbols_requested": len(settings.symbol_list()),
    "last_cycle_symbols_processed": 0,
}
telegram_bot: TelegramBot | None = None


async def _telegram_get_summary() -> dict:
    return await dashboard_summary()


async def _telegram_get_cycle() -> dict | None:
    if postgres is None or postgres.pool is None:
        return None
    rows = await postgres.recent_cycles(limit=100)
    selected = next((row for row in rows if row.get("status") == "COMPLETED"), None)
    selected = selected or next((row for row in rows if row.get("status") in {"PARTIAL", "FAILED"}), None)
    if not selected:
        return None
    return await postgres.cycle_detail(str(selected["cycle_id"]))


async def _telegram_get_decisions() -> list[dict]:
    if postgres is None or postgres.pool is None:
        return []
    return await postgres.recent_decisions(limit=100)


async def _telegram_get_orders() -> list[dict]:
    if postgres is None or postgres.pool is None:
        return []
    return await postgres.recent_paper_orders(limit=100)


async def _telegram_get_cycles() -> list[dict]:
    if postgres is None or postgres.pool is None:
        return []
    return await postgres.recent_cycles(limit=100)


async def _telegram_get_logs() -> list[dict]:
    return log_buffer.snapshot(limit=200)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global telegram_bot
    logger.info("Starting Mkmoon API in %s mode", settings.trading_mode)
    if postgres:
        await postgres.connect()
        logger.info("PostgreSQL connection established")
    if redis_store:
        await redis_store.connect()
        logger.info("Redis connection established")
    logger.info("Paper-only guard active; live trading enabled=%s", settings.live_trading_enabled)

    if settings.telegram_bot_token:
        try:
            telegram_bot = TelegramBot(
                settings.telegram_bot_token,
                webhook_secret=settings.telegram_webhook_secret,
                allowed_chat_ids=settings.telegram_chat_ids(),
                dashboard_url=settings.public_base_url,
                get_summary=_telegram_get_summary,
                get_cycle=_telegram_get_cycle,
                get_decisions=_telegram_get_decisions,
                get_orders=_telegram_get_orders,
                get_cycles=_telegram_get_cycles,
                get_logs=_telegram_get_logs,
            )
            await telegram_bot.startup(settings.public_base_url)
        except Exception as error:
            if telegram_bot and not telegram_bot.startup_error:
                telegram_bot.startup_error = type(error).__name__
            logger.error("Telegram bot startup failed error_type=%s", type(error).__name__)

    paper_task: asyncio.Task | None = None
    paper_enabled = settings.trading_mode == "paper" and not settings.live_trading_enabled
    if paper_enabled:
        from app.worker import run_once

        async def paper_loop() -> None:
            # Let the HTTP server finish startup before the first network-heavy cycle.
            await asyncio.sleep(3)
            while True:
                started = time()
                paper_worker_state["last_cycle_started_at_ms"] = int(started * 1000)
                paper_worker_state["last_cycle_status"] = "RUNNING"
                paper_worker_state["last_cycle_error"] = None
                paper_worker_state["last_cycle_id"] = None
                paper_worker_state["last_cycle_duration_ms"] = None
                paper_worker_state["last_cycle_data_source"] = None
                paper_worker_state["last_cycle_errors"] = []
                paper_worker_state["last_cycle_symbols_requested"] = len(settings.symbol_list())
                paper_worker_state["last_cycle_symbols_processed"] = 0
                try:
                    result = await asyncio.wait_for(
                        run_once(postgres_store=postgres, redis_store=redis_store, client=binance_public),
                        timeout=settings.cycle_timeout_seconds,
                    )
                    paper_worker_state["last_cycle_id"] = result.cycle_id
                    paper_worker_state["last_cycle_status"] = result.status
                    paper_worker_state["last_cycle_duration_ms"] = result.duration_ms
                    paper_worker_state["last_cycle_data_source"] = result.data_source
                    paper_worker_state["last_cycle_errors"] = list(result.errors)
                    paper_worker_state["last_cycle_symbols_requested"] = result.symbols_requested
                    paper_worker_state["last_cycle_symbols_processed"] = result.symbols_processed
                    if result.errors:
                        paper_worker_state["last_cycle_error"] = result.errors[-1]
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    paper_worker_state["last_cycle_status"] = "FAILED_UNHANDLED"
                    paper_worker_state["last_cycle_error"] = type(error).__name__
                    paper_worker_state["last_cycle_errors"] = [type(error).__name__]
                    logger.exception("embedded Paper cycle failed; no orders are sent")
                finally:
                    paper_worker_state["last_cycle_finished_at_ms"] = int(time() * 1000)
                elapsed = time() - started
                await asyncio.sleep(max(5, settings.poll_interval_seconds - int(elapsed)))

        paper_worker_state["running"] = True
        paper_task = asyncio.create_task(paper_loop(), name="mkmoon-embedded-paper-worker")
        logger.info("Embedded Paper worker started interval_seconds=%s", settings.poll_interval_seconds)

    try:
        yield
    finally:
        if paper_task:
            paper_task.cancel()
            await asyncio.gather(paper_task, return_exceptions=True)
            paper_worker_state["running"] = False
            paper_worker_state["last_cycle_status"] = "STOPPED"
            logger.info("Embedded Paper worker stopped")
        if telegram_bot:
            await telegram_bot.close()
            telegram_bot = None
        await binance_public.aclose()
        if redis_store:
            await redis_store.close()
        if postgres:
            await postgres.close()
        logger.info("Mkmoon API stopped")


app = FastAPI(title="Mkmoon Binance Spot Engine", version="0.2.0", lifespan=lifespan)


@app.middleware("http")
async def dashboard_api_guard(request: Request, call_next):
    configured = settings.dashboard_access_token
    if configured and request.url.path.startswith("/api/"):
        supplied = request.headers.get("authorization", "")
        token = supplied[7:] if supplied.lower().startswith("bearer ") else request.headers.get("x-dashboard-token", "")
        if not token or not secrets.compare_digest(token, configured):
            return JSONResponse({"detail": "Dashboard API authentication required"}, status_code=401)
    return await call_next(request)


@app.post("/telegram/webhook", include_in_schema=False)
async def telegram_webhook(request: Request) -> dict[str, bool]:
    if telegram_bot is None:
        raise HTTPException(status_code=503, detail="Telegram bot is not configured")
    supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if not telegram_bot.verify_webhook_secret(supplied):
        raise HTTPException(status_code=403, detail="Invalid Telegram webhook secret")
    try:
        update = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON update") from exc
    if not isinstance(update, dict):
        raise HTTPException(status_code=400, detail="Telegram update must be an object")
    await telegram_bot.handle_update(update)
    return {"ok": True}


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(DASHBOARD_PATH, media_type="text/html")


@app.get("/telegram/health", include_in_schema=False)
def telegram_health() -> dict:
    return {
        "configured": bool(settings.telegram_bot_token),
        "started": bool(telegram_bot and telegram_bot.started),
        "startup_error": telegram_bot.startup_error if telegram_bot else None,
        "allowed_chat_ids_configured": bool(settings.telegram_allowed_chat_ids.strip()),
        "webhook_path": "/telegram/webhook",
        "read_only": True,
    }


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
            decision_count = await postgres.decision_count()

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
        "worker_symbols": int(paper_worker_state.get("last_cycle_symbols_requested") or len(settings.symbol_list())),
        "worker_symbols_configured": len(settings.symbol_list()),
        "worker_symbols_processed": int(paper_worker_state.get("last_cycle_symbols_processed") or 0),
        "worker_data_source": paper_worker_state.get("last_cycle_data_source"),
        "server_time_ms": int(time() * 1000),
        "errors": errors,
        "paper_worker": dict(paper_worker_state),
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
            "source": binance_public.last_public_base_url or "Binance public market data",
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

