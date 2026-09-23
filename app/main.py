# -*- coding: utf-8 -*-
"""Mkmoon — main wiring: web server + asyncio loops.

Loops:
  fast tick (3s)     — order fills / position management on ACTIVE symbols only
  price cache (30s)  — one batched ticker call for all 40 symbols
  candle closes      — klines fetch ONLY when a 15m/1h/4h candle closes
  cycle (60s)        — the 'دورة': joint coupling check + cycle_report row
  heartbeat (30m)    — Telegram digest (configurable) + immediate fault alerts
"""
import asyncio
import json
import os
import resource
import time
from datetime import datetime, timezone

from aiohttp import web

from . import config as C
from . import pairs as P
from .detector import LiveDetector, HTFStructure
from .gates import passes
from .exchange import Market
from .trader import PaperTrader
from .store import Store
from .health import CycleTracker
from .bot import Bot


def _rss_mb():
    try:
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
    except Exception:
        return 0.0


def _human_s(s):
    s = int(s)
    d, r = divmod(s, 86400)
    h, r = divmod(r, 3600)
    m, _ = divmod(r, 60)
    return f"{d}ي {h}س {m}د"


class App:
    def __init__(self):
        self.session = None
        self.store = Store()
        self.cycles = CycleTracker()
        self.market = None
        self.bot = None
        self.trader = None
        self.det = {}            # (sym, "15m") -> LiveDetector
        self.htf = {}            # (sym, "1h"|"4h") -> HTFStructure
        self.fed = {}            # (sym, tf) -> last fed bar open t
        self.last_bucket = {"15m": 0, "1h": 0, "4h": 0}
        self.started_ms = int(time.time() * 1000)
        self.boot_done = False
        self._hb_last = 0
        self._prev_counters = dict(self.cycles.counters)

    # ------------------------------------------------------------------ boot
    async def build(self):
        import aiohttp
        self.session = aiohttp.ClientSession(
            headers={"User-Agent": "Mkmoon/1.0 (paper-trading bot)"})
        self.market = Market(self.session)
        await self.store.connect()
        if self.store.redis_ok:
            self.cycles.ok("redis")
        if self.store.pg_ok:
            self.cycles.ok("pg")

        async def ledger(kind, row):
            await self.store.write(kind, row)

        async def notifier(ev, payload):
            await self.bot.notify(ev, payload)

        self.trader = PaperTrader(notifier=notifier, ledger=ledger,
                                  tier_b_enabled=C.TIER_B_ENABLED)
        self.bot = Bot(self.session, self.responder)

        symbols = P.ALL_SYMBOLS
        for sym in symbols:
            self.det[sym] = LiveDetector(C.TRIG_MS)
            self.htf[(sym, "1h")] = HTFStructure(C.TF_MS["1h"])
            self.htf[(sym, "4h")] = HTFStructure(C.TF_MS["4h"])
        await self.backfill()
        self.boot_done = True

    async def backfill(self):
        """Boot: pull lookback windows ONCE per symbol per TF, feed detectors."""
        for sym in P.ALL_SYMBOLS:
            for tf, look in C.BARS_LOOKBACK.items():
                bars = await self.market.klines(sym, tf, look)
                if not bars:
                    self.cycles.add_fault(f"backfill {sym} {tf} empty")
                    continue
                for b in bars:
                    await self.feed_bar(sym, tf, b, quiet=True)
                self.fed[(sym, tf)] = bars[-1]["t"]
                await asyncio.sleep(0.05)   # stay well below rate limits
        self.cycles.ok("market")

    async def feed_bar(self, sym, tf, b, quiet=False):
        key = (sym, tf)
        if self.fed.get(key) is not None and b["t"] <= self.fed[key]:
            return
        self.fed[key] = b["t"]
        if tf == "15m":
            self.cycles.hit("bars_15m")
            self.cycles.ok("detector_15m")
            det = self.det[sym]
            out = det.add_bar(b["t"], b["o"], b["h"], b["l"], b["c"], b["v"])
            self.cycles.hit("setups_raw", len(out["setups"]))
            for s in out["setups"]:
                await self.on_new_setup(sym, s, quiet=quiet)
        else:
            self.cycles.hit("bars_1h" if tf == "1h" else "bars_4h")
            self.htf[(sym, tf)].add_bar(b["t"], b["o"], b["h"], b["l"], b["c"])

    async def on_new_setup(self, sym, s, quiet=False):
        # HTF context (informational in this config) — only CLOSED HTF candles
        s["htf1"] = self.htf[(sym, "1h")].state
        s["htf2"] = self.htf[(sym, "4h")].state
        if not passes(s, C.CFG):
            return
        self.cycles.hit("setups_pass")
        order = await self.trader.on_setup(s, sym, P.tier_of(sym), int(time.time() * 1000))
        if order:
            self.cycles.hit("orders")

    # ------------------------------------------------------------- fast tick
    async def fast_tick_loop(self):
        while True:
            try:
                t0 = time.time()
                now_ms = int(t0 * 1000)
                active = self.trader.active_symbols()
                if active:
                    books = await self.market.fetch_books(active)
                    for sym, (bid, ask) in books.items():
                        self.cycles.hit("ticks")
                        await self.trader.on_price(sym, bid, ask, now_ms,
                                                   now_ms // 60_000)
                self.cycles.ok("market")
                await self.detect_closes(now_ms)
            except Exception as e:
                self.cycles.hit("errors")
                self.cycles.add_fault(f"fast_tick: {e!r}"[:120])
            dt = C.FAST_TICK_S - (time.time() - t0)
            if dt > 0:
                await asyncio.sleep(dt)

    async def detect_closes(self, now_ms):
        """When a TF bucket rolls over -> fetch the CLOSED candle per symbol."""
        for tf, tfms in (("4h", C.TF_MS["4h"]), ("1h", C.TF_MS["1h"]),
                         ("15m", C.TRIG_MS)):
            bucket = now_ms // tfms
            prev = self.last_bucket[tf]
            self.last_bucket[tf] = bucket
            if prev == 0 or bucket <= prev:
                continue
            # closed bar open time = (bucket-1) * tfms ; small grace for API
            await asyncio.sleep(2.0)
            await self.fetch_closed(tf, (bucket - 1) * tfms)

    async def fetch_closed(self, tf, bar_t):
        for sym in P.ALL_SYMBOLS:
            try:
                bars = await self.market.klines(sym, tf, 2)
                for b in bars:
                    if b["t"] == bar_t and b["t_close"] <= int(time.time() * 1000) + 1000:
                        await self.feed_bar(sym, tf, b)
                await asyncio.sleep(0.03)
            except Exception as e:
                self.cycles.hit("errors")
                self.cycles.add_fault(f"fetch_closed {sym} {tf}: {e!r}"[:120])

    async def price_cache_loop(self):
        while True:
            try:
                await self.market.refresh_price_cache(P.ALL_SYMBOLS)
                self.cycles.hit("price_cache")
                await self.store.kv_set("prices", {s: self.market.last_px(s)
                                                   for s in P.ALL_SYMBOLS})
            except Exception as e:
                self.cycles.hit("errors")
            await asyncio.sleep(C.PRICE_CACHE_S)

    # ----------------------------------------------------------------- cycle
    async def cycle_loop(self):
        while True:
            try:
                await self.run_cycle()
            except Exception as e:
                self.cycles.hit("errors")
                self.cycles.add_fault(f"cycle: {e!r}"[:120])
            await asyncio.sleep(C.CYCLE_S)

    async def run_cycle(self):
        now_ms = int(time.time() * 1000)
        await self.store.ping()
        if self.store.redis_ok:
            self.cycles.ok("redis")
        if self.store.pg_ok:
            self.cycles.ok("pg")
        else:
            self.cycles.add_fault(f"supabase: {self.store.pg_err}")
        if not self.store.redis_ok and C.REDIS_URL:
            self.cycles.add_fault(f"redis: {self.store.redis_err}")
        if self.bot and not self.bot.ok and C.BOT_TOKEN:
            self.cycles.add_fault(f"telegram: {self.bot.last_err}")

        await self.trader.on_cycle(now_ms)

        joints = self.cycles.joints_report(self.market, self.store, self.trader,
                                           self.bot, _rss_mb())
        verdict, ok = self.cycles.verdict(joints)
        cnt = self.cycles.counters
        delta = {k: cnt[k] - self._prev_counters.get(k, 0) for k in cnt}
        self._prev_counters = dict(cnt)
        snap = self.trader.snapshot()
        report = {
            "t": now_ms, "cycle": self.cycles.cycle + 1,
            "uptime_s": self.cycles.uptime_s(),
            "verdict": verdict if ok else f" faults | {verdict}",
            "detail": {
                "wall": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "uptime_human": _human_s(self.cycles.uptime_s()),
                "delta": delta, "joints": joints,
                "equity_pct": round(self.trader.equity, 3),
                "wins": self.trader.wins, "losses": self.trader.losses,
                "counters": cnt,
            },
        }
        self.cycles.push_cycle(report)
        await self.store.write("cycle", report)
        await self.store.kv_set("last_cycle", report)
        await self.store.kv_set("trader_snapshot", snap)

        # immediate fault alerts (never silent degradation)
        if not ok and self.bot:
            await self.bot.notify("fault", {"msg": verdict[:300]})
        if self.cycles.cycle % 1440 == 0:
            await self.store.cleanup_old_cycles()

        # heartbeat digest (default every 30 min, configurable)
        if self.bot and C.HEARTBEAT_MINUTES > 0 and \
                time.time() - self._hb_last >= C.HEARTBEAT_MINUTES * 60:
            self._hb_last = time.time()
            msg = self.heartbeat_text(report, snap)
            await self.bot.notify("heartbeat", {"msg": msg})

    def heartbeat_text(self, report, snap):
        j = report["detail"]["joints"]
        last_sig = self.trader.signals[-1] if self.trader.signals else None
        sig_line = "لا توجد إشارات بعد"
        if last_sig:
            dirn = "شراء" if last_sig["side"] == 1 else "بيع"
            sig_line = f"{last_sig['symbol']} {dirn} @ {_px(last_sig['limit'])}"
        open_n = snap["open_positions"]
        return (
            f"💓 نبض النظام — دورة #{report['cycle']} ({report['detail']['wall']})\n"
            f"الحكم: {report['verdict']}\n"
            f"رأس المال الافتراضي: {snap['equity_pct']}% | صفقات: {snap['wins']}W/{snap['losses']}L\n"
            f"صفقات مفتوحة: {open_n} | أوامر معلقة: {snap['resting_orders']}\n"
            f"آخر إشارة: {sig_line}\n"
            f"Binance {j['market_data']['MB']}MB ({j['market_data']['budget_used_pct']}%) | "
            f"Redis {'✓' if j['redis']['ok'] else '✗'} | "
            f"PG {'✓' if j['supabase_pg']['ok'] else '✗'} | "
            f"رام {j['memory']['rss_mb']}MB"
        )

    # ------------------------------------------------------------- responses
    async def responder(self, action):
        try:
            if action == "OPEN":
                return self._resp_open()
            if action == "CLOSED":
                return self._resp_closed()
            if action == "PERF":
                return self._resp_perf()
            if action == "PRICES":
                return self._resp_prices()
            if action == "CYCLE":
                return await self._resp_cycle()
        except Exception as e:
            return f"خطأ مؤقت: {e!r}"
        return "?"

    def _resp_open(self):
        lines = ["🟢 <b>الصفقات المفتوحة</b>"]
        open_pos = [p for p in self.trader.positions.values() if p.exit_t is None]
        resting = [o for o in self.trader.orders.values() if o.status == "resting"]
        if not open_pos and not resting:
            return "🟢 لا توجد صفقات مفتوحة ولا أوامر معلقة الآن."
        for p in open_pos:
            px = self.market.last_px(p.symbol)
            mtm = self.trader.mark_price(p.symbol, px)
            side_ar = "شراء" if p.side == 1 else "بيع"
            tier_ar = "" if p.tier == "A" else " (تجريبي)"
            ln = (f"▫️ {p.symbol}{tier_ar} | {side_ar} | {p.id}\n"
                  f"   دخول {_px(p.entry_px)} → الآن {_px(px)} | "
                  f"{mtm['pnl_R']:+.2f}R ({mtm['pnl_pct']:+.2f}%)")
            if p.locked:
                ln += f"\n   🔒 وقف متحرك: {_px(p.locked_stop)}"
            else:
                ln += f"\n   وقف {_px(p.stop)} | هدف {_px(p.tp)}"
            lines.append(ln)
        for o in resting:
            side_ar = "شراء" if o.side == 1 else "بيع"
            tier_ar = "" if o.tier == "A" else " (تجريبي)"
            mins = max(int((o.expire_ms - time.time() * 1000) / 60000), 0)
            lines.append(f"▫️ {o.symbol}{tier_ar} | أمر {side_ar} معلق @ {_px(o.limit)} "
                         f"(ينتهي خلال {mins}د) | {o.id}")
        return "\n".join(lines)

    def _resp_closed(self):
        rows = self.trader.closed[-8:]
        if not rows:
            return "📕 لا توجد صفقات مغلقة بعد."
        lines = ["📕 <b>آخر الصفقات المغلقة</b>"]
        for r in reversed(rows):
            reason = C.REASON_AR.get(r["exit_reason"], r["exit_reason"])
            emoji = "📗" if r["net_R"] > 0 else "📕"
            tier_ar = "" if r["tier"] == "A" else " (تجريبي)"
            lines.append(
                f"{emoji} {r['symbol']}{tier_ar} | {reason}\n"
                f"   {_px(r['entry_px'])} → {_px(r['exit_px'])} | "
                f"{r['net_R']:+.3f}R ({r['net_R'] * C.CFG['risk_pct']:+.2f}%) | "
                f"{r['hold_h']:.1f}س")
        return "\n".join(lines)

    def _resp_perf(self):
        s = self.trader.snapshot()
        n = s["wins"] + s["losses"]
        wr = (100.0 * s["wins"] / n) if n else 0.0
        closed = [r for r in self.trader.closed if r["net_R"] > 0]
        closed_l = [r for r in self.trader.closed if r["net_R"] <= 0]
        aw = sum(r["net_R"] for r in closed) / len(closed) if closed else 0
        al = sum(r["net_R"] for r in closed_l) / len(closed_l) if closed_l else 0
        exp = s["sum_net_R"] / n if n else 0
        today = [r for r in self.trader.closed
                 if r["exit_t"] // 86400_000 == int(time.time() * 1000) // 86400_000]
        return (
            f"📈 <b>أداء النظام</b> ({C.STRATEGY_ID})\n"
            f"صفقات: {n} ({s['wins']}W / {s['losses']}L) | نسبة الربح: {wr:.1f}%\n"
            f"متوسط الربح: {aw:+.3f}R | متوسط الخسارة: {al:+.3f}R\n"
            f"التوقع: {exp:+.3f}R | مجموع R: {s['sum_net_R']:+.2f}\n"
            f"رأس المال الافتراضي: {s['equity_pct']:.2f}% (بدأ 100%)\n"
            f"أقصى تراجع: {s['max_dd_pct']:.2f}%\n"
            f"صفقات اليوم: {len(today)}\n"
            f"أوامر معلقة: {s['resting_orders']} | مفتوحة: {s['open_positions']}\n"
            f"ملاحظة: تداول وقي بعائد مئوي مركّب (مخاطرة 1% لكل صفقة)."
        )

    def _resp_prices(self):
        lines = ["💹 <b>الأسعار الحية</b>"]
        for sym in P.ALL_SYMBOLS:
            px = self.market.last_px(sym)
            chg = self.market.chg24.get(sym)
            tier = "▪️" if sym in P.TIER_A else "▫️"
            chg_s = f" ({chg:+.1f}%)" if chg is not None else ""
            lines.append(f"{tier} {sym}: {_px(px)}{chg_s}")
        lines.append("▪️ معتمد (10) | ▫️ تجريبي (30)")
        return "\n".join(lines)

    async def _resp_cycle(self):
        rep = self.cycles.log[-1] if self.cycles.log else await self.store.latest_cycle()
        if not rep:
            return "لا توجد دورة مسجلة بعد."
        return self.cycles.summary_text(rep)


def _px(x):
    if x is None:
        return "-"
    if x >= 1000:
        return f"{x:,.1f}"
    if x >= 1:
        return f"{x:.4f}"
    return f"{x:.6f}"


# --------------------------------------------------------------------- web --
async def make_web(app_obj):
    async def health(request):
        snap = app_obj.trader.snapshot() if app_obj.trader else {}
        joints_ok = True
        body = {"ok": joints_ok, "app": C.APP_NAME, "version": C.VERSION,
                "strategy": C.STRATEGY_ID, "uptime_s": app_obj.cycles.uptime_s(),
                "cycle": app_obj.cycles.cycle, "boot_done": app_obj.boot_done,
                "equity_pct": snap.get("equity_pct"), "rss_mb": _rss_mb(),
                "faults": app_obj.cycles.faults[-5:]}
        return web.json_response(body)

    async def status(request):
        if request.query.get("key") != C.STATUS_KEY:
            return web.json_response({"error": "unauthorized"}, status=401)
        joints = app_obj.cycles.joints_report(app_obj.market, app_obj.store,
                                              app_obj.trader, app_obj.bot, _rss_mb())
        verdict, _ = app_obj.cycles.verdict(joints)
        body = {
            "strategy": C.STRATEGY_ID,
            "verdict": verdict,
            "uptime_s": app_obj.cycles.uptime_s(),
            "cycle": app_obj.cycles.cycle,
            "trader": app_obj.trader.snapshot(),
            "joints": joints,
            "counters": app_obj.cycles.counters,
            "last_cycle": app_obj.cycles.log[-1] if app_obj.cycles.log else None,
            "pairs": {"tier_a": P.TIER_A, "tier_b": P.TIER_B},
        }
        return web.json_response(body)

    async def run_cycle(request):
        """Manual cycle trigger (also usable from cron-job.org as a keep-alive)."""
        if request.query.get("key") != C.STATUS_KEY:
            return web.json_response({"error": "unauthorized"}, status=401)
        await app_obj.run_cycle()
        return web.json_response({"ok": True, "cycle": app_obj.cycles.cycle})

    w = web.Application()
    w.router.add_get("/health", health)
    w.router.add_get("/status", status)
    w.router.add_get("/run-cycle", run_cycle)
    runner = web.AppRunner(w)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", C.PORT)
    await site.start()
    return runner


async def main():
    app_obj = App()
    await app_obj.build()
    await app_obj.bot.start()
    runner = await make_web(app_obj)
    print(f"[Mkmoon] web on :{C.PORT} | symbols={len(P.ALL_SYMBOLS)} | "
          f"redis={app_obj.store.redis_ok} pg={app_obj.store.pg_ok} "
          f"bot={bool(C.BOT_TOKEN)}", flush=True)
    tasks = [
        asyncio.create_task(app_obj.fast_tick_loop()),
        asyncio.create_task(app_obj.price_cache_loop()),
        asyncio.create_task(app_obj.cycle_loop()),
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        await runner.cleanup()
        await app_obj.store.close()
        await app_obj.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
