# -*- coding: utf-8 -*-
"""Market data — Binance PUBLIC market-data mirror (geo-open):

  REST:  https://data-api.binance.vision/api/v3/...   (same schema as api.binance.com)
  (WS upgrade path: wss://data-stream.binance.vision — documented, optional)

We ONLY read market data (paper trading needs no trading endpoints). All calls
are budgeted & counted (bytes + requests) for the free-tier quota dashboard.

Design (memory / quota discipline — never 'all coins × all candles every tick'):
  * boot backfill:    3 klines calls per symbol (15m/1h/4h)
  * candle closes:    1 klines call per symbol per closed TF candle
  * price cache:      1 batched ticker/price for ALL symbols every 30s
  * fast tick (3s):   1 batched bookTicker ONLY for active symbols (0..4)
"""
import asyncio
import json
import time

import aiohttp

from . import config as C

REST = "https://data-api.binance.vision/api/v3"


class ByteMeter:
    def __init__(self):
        self.req = 0
        self.bytes = 0
        self.errors = 0
        self.last_ok_ms = 0
        self.last_err = ""
        self._t0 = int(time.time())

    def add(self, n, ok=True, err=""):
        self.req += 1
        self.bytes += n
        if ok:
            self.last_ok_ms = int(time.time() * 1000)
        else:
            self.errors += 1
            self.last_err = err[:120]

    def snapshot(self):
        age_min = max((int(time.time()) - self._t0) / 60.0, 1e-6)
        mb = self.bytes / 1e6
        return {"req": self.req, "MB": round(mb, 3), "req_per_min": round(self.req / age_min, 2),
                "MB_per_day": round(mb / max(age_min / 1440.0, 1e-6), 3),
                "errors": self.errors, "last_err": self.last_err,
                "last_ok_ms": self.last_ok_ms}


class Market:
    def __init__(self, session: aiohttp.ClientSession):
        self.http = session
        self.meter = ByteMeter()
        self.prices = {}          # symbol -> {"bid":..,"ask":..,"px":..,"t":..}
        self.chg24 = {}           # symbol -> pct change (from ticker cache)

    async def _get(self, path, params=None, retries=3):
        url = f"{REST}{path}"
        delay = 0.5
        for attempt in range(retries):
            try:
                async with self.http.get(url, params=params,
                                         timeout=aiohttp.ClientTimeout(total=10)) as r:
                    body = await r.read()
                    if r.status == 200:
                        self.meter.add(len(body), ok=True)
                        return json.loads(body)
                    self.meter.add(len(body), ok=False, err=f"HTTP {r.status}")
                    if r.status in (429, 418):
                        await asyncio.sleep(delay * (attempt + 1) * 4)
                        continue
                    await asyncio.sleep(delay * (attempt + 1))
            except Exception as e:
                self.meter.add(0, ok=False, err=repr(e))
                await asyncio.sleep(delay * (attempt + 1))
        return None

    # ------------------------------------------------------------------ API
    async def klines(self, symbol, interval, limit):
        rows = await self._get("/klines", {"symbol": symbol, "interval": interval,
                                           "limit": limit})
        if not rows:
            return []
        out = []
        for r in rows:
            out.append({
                "t": int(r[0]),                  # open time ms
                "t_close": int(r[6]),            # close time ms
                "o": float(r[1]), "h": float(r[2]), "l": float(r[3]),
                "c": float(r[4]), "v": float(r[5]),
            })
        return out

    async def refresh_price_cache(self, symbols):
        """One batched call for ALL symbols (every 30s)."""
        if not symbols:
            return
        joined = '["' + '","'.join(symbols) + '"]'
        rows = await self._get("/ticker/price", {"symbols": joined})
        if isinstance(rows, list):
            now = int(time.time() * 1000)
            for r in rows:
                px = float(r["price"])
                prev = self.prices.get(r["symbol"], {})
                self.prices[r["symbol"]] = {"bid": px, "ask": px, "px": px, "t": now}
        rows = await self._get("/ticker/24hr", {"symbols": joined})
        if isinstance(rows, list):
            for r in rows:
                try:
                    self.chg24[r["symbol"]] = float(r.get("priceChangePercent", 0.0))
                except (TypeError, ValueError):
                    pass

    async def fetch_books(self, symbols):
        """Batched bookTicker for ACTIVE symbols only (fast tick)."""
        if not symbols:
            return {}
        joined = '["' + '","'.join(symbols) + '"]'
        rows = await self._get("/ticker/bookTicker", {"symbols": joined}, retries=1)
        out = {}
        now = int(time.time() * 1000)
        if isinstance(rows, list):
            for r in rows:
                bid, ask = float(r["bidPrice"]), float(r["askPrice"])
                out[r["symbol"]] = (bid, ask)
                self.prices[r["symbol"]] = {"bid": bid, "ask": ask,
                                            "px": (bid + ask) / 2.0, "t": now}
        return out

    def last_px(self, symbol):
        p = self.prices.get(symbol)
        return p["px"] if p else None

    def health(self):
        s = self.meter.snapshot()
        s["budget_MB"] = C.BYTES_BUDGET_MB
        s["budget_used_pct"] = round(100.0 * s["MB"] / max(C.BYTES_BUDGET_MB, 1), 2)
        return s
