from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from app.core.settings import get_settings
from app.storage.postgres import PostgresStore
from app.storage.redis import RedisStore

settings = get_settings()
postgres = PostgresStore(settings.database_url) if settings.database_url else None
redis_store = RedisStore(settings.redis_url) if settings.redis_url else None


@asynccontextmanager
async def lifespan(_: FastAPI):
    if postgres:
        await postgres.connect()
    if redis_store:
        await redis_store.connect()
    yield
    if redis_store:
        await redis_store.close()
    if postgres:
        await postgres.close()


app = FastAPI(title="Mkmoon Binance Spot Engine", version="0.1.0", lifespan=lifespan)


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
    if settings.app_env == "production" and (postgres is None or redis_store is None):
        raise HTTPException(status_code=503, detail="DATABASE_URL and REDIS_URL are required in production")
    return {"status": "ready"}


@app.get("/api/decisions")
async def recent_decisions(limit: int = 50) -> list[dict]:
    if postgres is None:
        return []
    return await postgres.recent_decisions(limit)
