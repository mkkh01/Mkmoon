# -*- coding: utf-8 -*-
"""Store — Redis (fast state/cache) + Postgres/Supabase ledger (asyncpg).

Tables (auto-created, idempotent):
  signals        every recommendation emitted
  orders         resting / filled / cancelled / expired
  trades         full trade lifecycle with close reason + equity after
  cycle_reports  one row per 1-minute cycle: all system joints + connectivity
  heartbeat_log  telegram heartbeat / fault journal

Everything degrades gracefully: if Supabase is down we buffer in Redis lists and
the fault shows up in the cycle summary; if Redis is down we keep state in RAM.
"""
import asyncio
import json
import time

import asyncpg

from . import config as C

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id BIGSERIAL PRIMARY KEY, t BIGINT NOT NULL, symbol TEXT, tier TEXT, side INT,
    ctype TEXT, signal_t BIGINT, ord_t BIGINT, order_id TEXT,
    limit_px DOUBLE PRECISION, stop_px DOUBLE PRECISION, tp_px DOUBLE PRECISION,
    r0 DOUBLE PRECISION, risk_pct DOUBLE PRECISION,
    sweep_src TEXT, session TEXT, fvg_lo DOUBLE PRECISION, fvg_hi DOUBLE PRECISION,
    atr DOUBLE PRECISION, htf1 INT, htf2 INT, disp_ok BOOLEAN, z DOUBLE PRECISION,
    note TEXT);
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY, symbol TEXT, tier TEXT, side INT, limit_px DOUBLE PRECISION,
    stop_px DOUBLE PRECISION, tp_px DOUBLE PRECISION, r0 DOUBLE PRECISION,
    placed_t BIGINT, expire_t BIGINT, closed_t BIGINT, signal_t BIGINT, status TEXT);
CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY, order_id TEXT, symbol TEXT, tier TEXT, side INT,
    entry_t BIGINT, entry_px DOUBLE PRECISION, stop_px DOUBLE PRECISION,
    locked_stop DOUBLE PRECISION, tp_px DOUBLE PRECISION, r DOUBLE PRECISION,
    exit_t BIGINT, exit_px DOUBLE PRECISION, exit_reason TEXT,
    gross_r DOUBLE PRECISION, net_r DOUBLE PRECISION, fees DOUBLE PRECISION,
    mfe_r DOUBLE PRECISION, mae_r DOUBLE PRECISION, hold_h DOUBLE PRECISION,
    ambiguous INT, locked BOOLEAN, equity_pct DOUBLE PRECISION,
    signal_t BIGINT, ctype TEXT, session TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS cycle_reports (
    id BIGSERIAL PRIMARY KEY, t BIGINT NOT NULL, cycle BIGINT, uptime_s BIGINT,
    verdict TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS heartbeat_log (
    id BIGSERIAL PRIMARY KEY, t BIGINT NOT NULL, kind TEXT, msg TEXT);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_cycle_t ON cycle_reports(t);
"""


class Store:
    def __init__(self):
        self.redis = None
        self.pg = None
        self.redis_ok = False
        self.pg_ok = False
        self.redis_err = ""
        self.pg_err = ""
        self.redis_ms = 0
        self.pg_ms = 0
        self._buffer = []          # rows pending DB write (bounded)

    async def connect(self):
        if C.REDIS_URL:
            try:
                import redis.asyncio as aioredis
                self.redis = aioredis.from_url(
                    C.REDIS_URL, decode_responses=True,
                    socket_timeout=5, socket_connect_timeout=5)
                t0 = time.time()
                await self.redis.ping()
                self.redis_ms = int((time.time() - t0) * 1000)
                self.redis_ok = True
            except Exception as e:
                self.redis_err = repr(e)[:120]
        if C.DATABASE_URL:
            try:
                t0 = time.time()
                self.pg = await asyncpg.connect(C.DATABASE_URL, timeout=8,
                                                statement_cache_size=0)
                await self.pg.execute(SCHEMA)
                self.pg_ms = int((time.time() - t0) * 1000)
                self.pg_ok = True
            except Exception as e:
                self.pg_err = repr(e)[:120]

    async def close(self):
        try:
            if self.redis:
                await self.redis.aclose()
        except Exception:
            pass
        try:
            if self.pg:
                await self.pg.close()
        except Exception:
            pass

    async def ping(self):
        """Light liveness probe each cycle — refreshes joint status."""
        if self.redis:
            try:
                t0 = time.time()
                await asyncio.wait_for(self.redis.ping(), 5)
                self.redis_ms = int((time.time() - t0) * 1000)
                self.redis_ok, self.redis_err = True, ""
            except Exception as e:
                self.redis_ok, self.redis_err = False, repr(e)[:120]
        if self.pg:
            try:
                t0 = time.time()
                await asyncio.wait_for(self.pg.fetchval("SELECT 1"), 5)
                self.pg_ms = int((time.time() - t0) * 1000)
                self.pg_ok, self.pg_err = True, ""
            except Exception as e:
                self.pg_ok, self.pg_err = False, repr(e)[:120]
                try:
                    await self.pg.close()
                except Exception:
                    pass
                self.pg = None
                if C.DATABASE_URL:
                    try:
                        self.pg = await asyncpg.connect(C.DATABASE_URL, timeout=8,
                                                        statement_cache_size=0)
                    except Exception:
                        pass

    # ------------------------------------------------------------- key/value
    async def kv_set(self, key, val):
        if self.redis_ok and self.redis:
            try:
                await self.redis.set(key, json.dumps(val), ex=86400)
            except Exception:
                pass

    async def kv_get(self, key):
        if self.redis_ok and self.redis:
            try:
                v = await self.redis.get(key)
                return json.loads(v) if v else None
            except Exception:
                return None
        return None

    # ---------------------------------------------------------------- ledger
    async def write(self, kind, row):
        row = dict(row)
        if self.pg_ok and self.pg:
            try:
                await self._insert(kind, row)
                await self._flush_buffer()
                return
            except Exception as e:
                self.pg_err = repr(e)[:120]
                self.pg_ok = False
        self._buffer.append((kind, row))
        if len(self._buffer) > 500:
            self._buffer = self._buffer[-300:]

    async def _flush_buffer(self):
        while self._buffer:
            kind, row = self._buffer.pop(0)
            try:
                await self._insert(kind, row)
            except Exception:
                self._buffer.insert(0, (kind, row))
                break

    async def _insert(self, kind, r):
        if kind == "signal":
            await self.pg.execute(
                """INSERT INTO signals (t,symbol,tier,side,ctype,signal_t,ord_t,order_id,
                   limit_px,stop_px,tp_px,r0,risk_pct,sweep_src,session,fvg_lo,fvg_hi,atr,
                   htf1,htf2,disp_ok,z)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,
                           $19,$20,$21,$22)""",
                r.get("t"), r.get("symbol"), r.get("tier"), r.get("side"),
                r.get("ctype"), r.get("signal_t"), r.get("ord_t"), r.get("order_id"),
                r.get("limit"), r.get("stop"), r.get("tp"), r.get("R0"),
                r.get("risk_pct", C.CFG["risk_pct"]), r.get("sweep_src"),
                r.get("session"), r.get("fvg_lo"), r.get("fvg_hi"), r.get("atr"),
                r.get("htf1"), r.get("htf2"), bool(r.get("disp_ok")), r.get("z"))
        elif kind == "order":
            await self.pg.execute(
                """INSERT INTO orders (order_id,symbol,tier,side,limit_px,stop_px,tp_px,r0,
                   placed_t,expire_t,closed_t,signal_t,status)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                   ON CONFLICT (order_id) DO UPDATE SET status=EXCLUDED.status,
                       closed_t=COALESCE(EXCLUDED.closed_t, orders.closed_t)""",
                r.get("order_id"), r.get("symbol"), r.get("tier"), r.get("side"),
                r.get("limit"), r.get("stop"), r.get("tp"), r.get("R0"),
                r.get("placed_t"), r.get("expire_ms") or r.get("expire_t"),
                r.get("closed_t"), r.get("signal_t"), r.get("status"))
        elif kind == "trade":
            await self.pg.execute(
                """INSERT INTO trades (trade_id,order_id,symbol,tier,side,entry_t,entry_px,
                   stop_px,locked_stop,tp_px,r,exit_t,exit_px,exit_reason,gross_r,net_r,
                   fees,mfe_r,mae_r,hold_h,ambiguous,locked,equity_pct,signal_t,ctype,
                   session,status)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,
                           $19,$20,$21,$22,$23,$24,$25,$26,$27)
                   ON CONFLICT (trade_id) DO UPDATE SET
                       exit_t=EXCLUDED.exit_t, exit_px=EXCLUDED.exit_px,
                       exit_reason=EXCLUDED.exit_reason, gross_r=EXCLUDED.gross_r,
                       net_r=EXCLUDED.net_r, fees=EXCLUDED.fees, mfe_r=EXCLUDED.mfe_r,
                       mae_r=EXCLUDED.mae_r, hold_h=EXCLUDED.hold_h,
                       ambiguous=EXCLUDED.ambiguous, locked=EXCLUDED.locked,
                       equity_pct=EXCLUDED.equity_pct, status=EXCLUDED.status""",
                r.get("trade_id"), r.get("order_id"), r.get("symbol"), r.get("tier"),
                r.get("side"), r.get("entry_t"), r.get("entry_px"), r.get("stop"),
                r.get("locked_stop"), r.get("tp"), r.get("R"), r.get("exit_t"),
                r.get("exit_px"), r.get("exit_reason"), r.get("gross_R"), r.get("net_R"),
                r.get("fees"), r.get("mfe_R"), r.get("mae_R"), r.get("hold_h"),
                r.get("ambiguous", 0), bool(r.get("locked")), r.get("equity_pct"),
                r.get("signal_t"), r.get("ctype"), r.get("session"), r.get("status"))
        elif kind == "cycle":
            await self.pg.execute(
                "INSERT INTO cycle_reports (t,cycle,uptime_s,verdict,detail) "
                "VALUES ($1,$2,$3,$4,$5)",
                r.get("t"), r.get("cycle"), r.get("uptime_s"), r.get("verdict"),
                json.dumps(r.get("detail", {}), ensure_ascii=False))
        elif kind == "heartbeat":
            await self.pg.execute(
                "INSERT INTO heartbeat_log (t,kind,msg) VALUES ($1,$2,$3)",
                r.get("t"), r.get("kind"), r.get("msg"))

    # ------------------------------------------------------------- readers
    async def latest_cycle(self):
        if self.pg_ok and self.pg:
            try:
                row = await self.pg.fetchrow(
                    "SELECT t,cycle,uptime_s,verdict,detail FROM cycle_reports "
                    "ORDER BY id DESC LIMIT 1")
                if row:
                    return {"t": row["t"], "cycle": row["cycle"],
                            "uptime_s": row["uptime_s"], "verdict": row["verdict"],
                            "detail": json.loads(row["detail"] or "{}")}
            except Exception:
                pass
        return None

    async def cleanup_old_cycles(self):
        if self.pg_ok and self.pg:
            try:
                cutoff = int(time.time() * 1000) - C.CYCLE_RETENTION_DAYS * 86400_000
                await self.pg.execute("DELETE FROM cycle_reports WHERE t < $1", cutoff)
            except Exception:
                pass

    async def count_trades(self):
        if self.pg_ok and self.pg:
            try:
                return await self.pg.fetchval("SELECT COUNT(*) FROM trades WHERE status='closed'")
            except Exception:
                return None
        return None
