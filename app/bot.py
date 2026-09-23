# -*- coding: utf-8 -*-
"""Telegram bot — raw HTTP long-poll (no external bot framework).

5 inline buttons (as requested):
  🟢 الصفقات المفتوحة | 📕 الصفقات المغلقة | 📈 أداء النظام
  💹 الأسعار الحية    | 🧾 ملخص الدورة

All trade lifecycle notifications (open → close WITH reason) + 30-min heartbeat
digest + IMMEDIATE fault alerts. Bound to CHAT_ID from the environment.
"""
import asyncio
import json
import time

import aiohttp

from . import config as C

API = "https://api.telegram.org/bot{tok}/{method}"

BUTTONS = [
    [{"text": "🟢 الصفقات المفتوحة", "callback_data": "OPEN"},
     {"text": "📕 الصفقات المغلقة", "callback_data": "CLOSED"}],
    [{"text": "📈 أداء النظام", "callback_data": "PERF"},
     {"text": "💹 الأسعار الحية", "callback_data": "PRICES"}],
    [{"text": "🧾 ملخص الدورة", "callback_data": "CYCLE"}],
]


def _fmt_px(x):
    if x is None:
        return "-"
    if x >= 1000:
        return f"{x:,.1f}"
    if x >= 1:
        return f"{x:.4f}"
    return f"{x:.6f}"


class Bot:
    def __init__(self, session: aiohttp.ClientSession, responder):
        """responder: async (action:str) -> str  (builds button replies)."""
        self.http = session
        self.responder = responder
        self.ok = False
        self.last_err = ""
        self.last_ok = 0
        self.offset = 0
        self.sent = 0
        self._task = None

    # ------------------------------------------------------------- transport
    async def call(self, method, **params):
        if not C.BOT_TOKEN:
            return None
        url = API.format(tok=C.BOT_TOKEN, method=method)
        try:
            async with self.http.post(url, json=params,
                                      timeout=aiohttp.ClientTimeout(total=35)) as r:
                body = await r.read()
                data = json.loads(body)
                if data.get("ok"):
                    self.ok = True
                    self.last_err = ""
                    self.last_ok = int(time.time() * 1000)
                    return data.get("result")
                self.ok = False
                self.last_err = str(data.get("description", ""))[:120]
        except Exception as e:
            self.ok = False
            self.last_err = repr(e)[:120]
        return None

    async def send(self, text, buttons=False):
        if not C.BOT_TOKEN or not C.CHAT_ID:
            return
        params = {"chat_id": C.CHAT_ID, "text": text[:4000]}
        if buttons:
            params["reply_markup"] = json.dumps({"inline_keyboard": BUTTONS})
        await self.call("sendMessage", **params)
        self.sent += 1

    async def notify(self, ev, payload):
        """Lifecycle notifications — every trade followed to the end + reason."""
        ar = C.REASON_AR
        side_ar = "🟢 شراء" if payload.get("side") == 1 else "🔴 بيع"
        tier = payload.get("tier", "A")
        tier_ar = "" if tier == "A" else " — تجريبي (خارج التحقق)"
        sym = payload.get("symbol", "")
        if ev == "order_placed":
            r_pct = payload.get("risk_pct", 1.0)
            tp_pct = payload.get("tp_pct", 0.9)
            txt = (
                f"🔔 <b>توصية صفقة جديدة</b>{tier_ar}\n"
                f"{sym} | {side_ar} | {payload.get('ctype','').upper()}\n"
                f"الدخول (أمر معلق): {_fmt_px(payload.get('limit'))}\n"
                f"الوقف: {_fmt_px(payload.get('stop'))} ({-r_pct}% من رأس المال)\n"
                f"الهدف: {_fmt_px(payload.get('tp'))} (+{tp_pct}% ≈ +0.9R)\n"
                f"القفل الآمن: +0.30R عند تجاوز 0.35R\n"
                f"صلاحية الأمر: 6 ساعات"
            )
            await self._send_html(txt)
        elif ev == "trade_opened":
            txt = (
                f"✅ <b>تم فتح صفقة</b>{tier_ar}\n"
                f"{sym} | {side_ar} | {payload['trade_id']}\n"
                f"سعر الدخول: {_fmt_px(payload['entry_px'])}\n"
                f"الوقف: {_fmt_px(payload['stop'])} | الهدف: {_fmt_px(payload['tp'])}\n"
                f"أُبلغ بالدخول لحظة التعبئة — سأتابعها حتى النهاية مع ذكر السبب."
            )
            await self._send_html(txt)
        elif ev == "trade_closed":
            reason = ar.get(payload.get("exit_reason"), payload.get("exit_reason"))
            net = payload.get("net_R", 0.0)
            emoji = "📗" if net > 0 else "📕"
            pnl_pct = net * payload.get("risk_pct", C.CFG["risk_pct"])
            txt = (
                f"{emoji} <b>إغلاق صفقة — {reason}</b>{tier_ar}\n"
                f"{sym} | {side_ar} | {payload['trade_id']}\n"
                f"دخول {_fmt_px(payload['entry_px'])} → خروج {_fmt_px(payload['exit_px'])}\n"
                f"النتيجة: {net:+.3f}R ({pnl_pct:+.2f}% من رأس المال)\n"
                f"السبب: {reason}\n"
                f"مدة الاحتفاظ: {payload.get('hold_h', 0):.1f} ساعة\n"
                f"رأس المال الافتراضي الآن: {payload.get('equity_pct', 0):.2f}%"
            )
            await self._send_html(txt)
        elif ev == "order_cancelled":
            txt = (f"🚫 <b>إلغاء أمر</b>{tier_ar}\n{sym} | {side_ar} | {payload.get('order_id')}\n"
                   f"السبب: {payload.get('reason', ar['cancelled'])}")
            await self._send_html(txt)
        elif ev == "order_expired":
            txt = (f"⌛ <b>أمر منتهي بدون تعبئة</b>{tier_ar}\n{sym} | {side_ar} | {payload.get('order_id')}\n"
                   f"السبب: {ar['expired']}")
            await self._send_html(txt)
        elif ev == "fault":
            await self.send(f"🚨 <b>عطل في النظام</b>\n{payload.get('msg','')}"[:400])
        elif ev == "heartbeat":
            await self.send(payload.get("msg", ""))

    async def _send_html(self, text):
        if not C.BOT_TOKEN or not C.CHAT_ID:
            return
        await self.call("sendMessage", chat_id=C.CHAT_ID, text=text[:4000],
                        parse_mode="HTML")
        self.sent += 1

    # ------------------------------------------------------------- long-poll
    async def poll_loop(self):
        while True:
            res = await self.call("getUpdates", timeout=40, offset=self.offset,
                                  allowed_updates=json.dumps(["callback_query", "message"]))
            if res is None:
                await asyncio.sleep(5)
                continue
            for upd in res:
                self.offset = upd["update_id"] + 1
                try:
                    await self._handle(upd)
                except Exception:
                    pass

    async def _handle(self, upd):
        cq = upd.get("callback_query")
        if cq:
            chat_id = cq.get("message", {}).get("chat", {}).get("id")
            if C.CHAT_ID and str(chat_id) != str(C.CHAT_ID):
                await self.call("answerCallbackQuery", callback_query_id=cq["id"])
                return
            action = cq.get("data", "")
            await self.call("answerCallbackQuery", callback_query_id=cq["id"])
            text = await self.responder(action)
            await self.send(text)
            return
        msg = upd.get("message")
        if msg:
            chat_id = msg.get("chat", {}).get("id")
            if C.CHAT_ID and str(chat_id) != str(C.CHAT_ID):
                return
            text = (msg.get("text") or "").strip()
            if text.startswith("/start") or text == "القائمة":
                await self.send(
                    "🌙 Mkmoon — نظام التداول الورقي (ICT-DAY-15M-SLM-01)\n"
                    "اختر من الأزرار:", buttons=True)
            elif text.startswith("/help"):
                await self.send(
                    "الأزرار:\n🟢 مفتوحة | 📕 مغلقة | 📈 أداء | 💹 أسعار | 🧾 ملخص الدورة\n"
                    "كل صفقة تُبلَّغ عند الفتح والإغلاق مع ذكر السبب.")
            else:
                await self.send("استعمل /start لعرض الأزرار.")

    async def start(self):
        await self.call("getMe")
        await self.send(
            "🌙 <b>Mkmoon يعمل الآن</b>\n"
            f"الاستراتيجية: {C.STRATEGY_ID}\n"
            "الوضع: تداول وقي (paper) بأسعار حية — كل صفقة تُتابع حتى النهاية مع ذكر السبب.\n"
            "استعمل /start لعرض الأزرار.",
            buttons=False)
        await self.send("📋 القائمة:", buttons=True)
        self._task = asyncio.create_task(self.poll_loop())
