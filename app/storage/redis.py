from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import AsyncIterator

from redis.asyncio import Redis


class RedisStore:
    def __init__(self, url: str):
        self.url = url
        self.client: Redis | None = None

    async def connect(self) -> None:
        self.client = Redis.from_url(self.url, decode_responses=True, health_check_interval=30)
        await self.client.ping()

    async def close(self) -> None:
        if self.client:
            await self.client.aclose()
            self.client = None

    def _require(self) -> Redis:
        if self.client is None:
            raise RuntimeError("RedisStore is not connected")
        return self.client

    async def set_once(self, key: str, value: str, ttl_seconds: int = 86400) -> bool:
        return bool(await self._require().set(key, value, ex=ttl_seconds, nx=True))

    async def publish_decision(self, decision: dict) -> int:
        return int(await self._require().publish("mkmoon:decisions", json.dumps(decision, default=str)))

    @asynccontextmanager
    async def lock(self, name: str, ttl_seconds: int = 30) -> AsyncIterator[None]:
        lock = self._require().lock(f"mkmoon:lock:{name}", timeout=ttl_seconds, blocking_timeout=5)
        acquired = await lock.acquire()
        if not acquired:
            raise TimeoutError(f"could not acquire lock: {name}")
        try:
            yield
        finally:
            await lock.release()
