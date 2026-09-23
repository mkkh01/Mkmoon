# -*- coding: utf-8 -*-
"""CycleTracker — the 'دورة' (cycle) accounting & system-joint coupling check.

Every minute the system writes one cycle_report: counters of every joint
(detector / trader / market / store / bot), connectivity status with latencies,
resource use, and a verdict — 'كل المفاصل تعمل بترابط' or the fault list.
Faults also trigger IMMEDIATE Telegram alerts (not waiting for the digest).
"""
import asyncio
import time

from . import config as C


class CycleTracker:
    def __init__(self):
        self.t0 = time.time()
        self.cycle = 0
        self.counters = {
            "ticks": 0, "price_cache": 0, "bars_15m": 0, "bars_1h": 0, "bars_4h": 0,
            "setups_raw": 0, "setups_pass": 0, "orders": 0, "fills": 0,
            "closes": 0, "cancels": 0, "errors": 0, "faults": 0,
        }
        self.last = {           # last-success timestamps (ms) per joint
            "market": 0, "redis": 0, "pg": 0, "bot": 0,
            "detector_15m": 0, "cycle": 0,
        }
        self.faults = []        # active fault strings
        self.log = []           # recent cycle summaries (ring)
        self._fault_seen = set()

    def hit(self, key, n=1):
        self.counters[key] = self.counters.get(key, 0) + n

    def ok(self, joint):
        self.last[joint] = int(time.time() * 1000)

    def add_fault(self, msg):
        if msg not in self._fault_seen:
            self._fault_seen.add(msg)
            self.faults.append(msg)
            self.hit("faults")

    def clear_fault(self, msg):
        self._fault_seen.discard(msg)
        if msg in self.faults:
            self.faults.remove(msg)

    def uptime_s(self):
        return int(time.time() - self.t0)

    def joints_report(self, market, store, trader, bot, mem_mb):
        now = int(time.time() * 1000)

        def age(j):
            t = self.last.get(j, 0)
            return None if not t else round((now - t) / 1000.0, 1)

        m = market.health()
        joints = {
            "market_data": {"ok": m["errors"] < 10, "last_ok_age_s": age("market"),
                            "req": m["req"], "MB": m["MB"],
                            "budget_used_pct": m["budget_used_pct"]},
            "detector_15m": {"ok": age("detector_15m") is not None,
                             "last_close_age_s": age("detector_15m"),
                             "bars_15m": self.counters["bars_15m"]},
            "trader": {"ok": True, "equity_pct": round(trader.equity, 3),
                       "open_positions": len([p for p in trader.positions.values()
                                              if p.exit_t is None]),
                       "resting_orders": len([o for o in trader.orders.values()
                                              if o.status == "resting"]),
                       "fills": self.counters["fills"],
                       "closes": self.counters["closes"]},
            "redis": {"ok": store.redis_ok, "latency_ms": store.redis_ms,
                      "err": store.redis_err},
            "supabase_pg": {"ok": store.pg_ok, "latency_ms": store.pg_ms,
                            "err": store.pg_err},
            "telegram": {"ok": (bot.ok or not C.BOT_TOKEN),
                         "last_ok_age_s": age("bot"), "err": bot.last_err},
            "memory": {"rss_mb": mem_mb},
        }
        return joints

    def verdict(self, joints):
        bad = [f"{k}: {v.get('err') or 'down'}" for k, v in joints.items()
               if isinstance(v, dict) and v.get("ok") is False]
        if bad:
            return " faults | ".join(bad), False
        return "كل المفاصل تعمل بترابط ✓", True

    def push_cycle(self, report):
        self.log.append(report)
        if len(self.log) > 200:
            self.log = self.log[-120:]
        self.cycle += 1
        self.ok("cycle")

    def summary_text(self, report):
        """Arabic 'summary cycle' — movement of every joint during this cycle."""
        d = report["detail"]
        j = d["joints"]
        lines = [
            f"🧾 ملخص الدورة #{report['cycle']} — {d['wall']}",
            f"⏱ زمن التشغيل: {d['uptime_human']}",
            "",
            "حركة المفاصل خلال الدقيقة:",
            f"• نبضات الأسعار: {d['delta'].get('ticks', 0)} | تحديث كاش السعر: {d['delta'].get('price_cache', 0)}",
            f"• أعمدة 15م أُغلقت: {d['delta'].get('bars_15m', 0)} | 1س: {d['delta'].get('bars_1h', 0)} | 4س: {d['delta'].get('bars_4h', 0)}",
            f"• إشارات خام: {d['delta'].get('setups_raw', 0)} | بعد التصفية: {d['delta'].get('setups_pass', 0)}",
            f"• أوامر: {d['delta'].get('orders', 0)} | تعبئات: {d['delta'].get('fills', 0)} | إغلاقات: {d['delta'].get('closes', 0)} | إلغاءات: {d['delta'].get('cancels', 0)}",
            "",
            "حالة الاتصالات:",
            f"• Binance: {'✓' if j['market_data']['ok'] else '✗'} ({j['market_data']['MB']}MB، {j['market_data']['budget_used_pct']}% من الميزانية)",
            f"• Redis: {'✓' if j['redis']['ok'] else '✗'} {j['redis']['latency_ms']}ms",
            f"• Supabase/Postgres: {'✓' if j['supabase_pg']['ok'] else '✗'} {j['supabase_pg']['latency_ms']}ms",
            f"• Telegram: {'✓' if j['telegram']['ok'] else '✗'}",
            f"• الذاكرة: {j['memory']['rss_mb']}MB",
            "",
            f"📊 رأس المال الافتراضي: {d['equity_pct']}% | الصفقات: {d['wins']}W/{d['losses']}L",
            f"الحكم: {report['verdict']}",
        ]
        return "\n".join(lines)
