from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager, suppress
from typing import AsyncIterator

from redis.asyncio import Redis

log = logging.getLogger("mkmoon.redis")


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
        if ttl_seconds < 30:
            raise ValueError("lock TTL must be at least 30 seconds")
        lock = self._require().lock(f"mkmoon:lock:{name}", timeout=ttl_seconds, blocking_timeout=5)
        acquired = await lock.acquire()
        if not acquired:
            raise TimeoutError(f"could not acquire lock: {name}")
        stop = asyncio.Event()
        lost = False

        async def heartbeat() -> None:
            nonlocal lost
            interval = max(10.0, ttl_seconds / 3)
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=interval)
                    return
                except asyncio.TimeoutError:
                    try:
                        ok = await lock.extend(ttl_seconds, replace_ttl=True)
                        if not ok:
                            lost = True
                            log.error("redis lock extension returned false name=%s", name)
                            return
                    except Exception:
                        lost = True
                        log.exception("redis lock extension failed name=%s", name)
                        return

        heartbeat_task = asyncio.create_task(heartbeat(), name=f"redis-lock-heartbeat:{name}")
        try:
            yield
        finally:
            stop.set()
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
            if not lost:
                try:
                    await lock.release()
                except Exception:
                    log.warning("redis lock release failed name=%s; lock may have expired", name, exc_info=True)
