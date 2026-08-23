from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import re
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

log = logging.getLogger("mkmoon.telegram")

SummaryProvider = Callable[[], Awaitable[dict]]
CycleProvider = Callable[[], Awaitable[dict | None]]
ListProvider = Callable[[], Awaitable[list[dict]]]
TickerProvider = Callable[[], Awaitable[list[dict]]]


class TelegramBot:
    """Read-only Telegram monitor for Mkmoon.

    The bot never calls Binance private endpoints and never changes trading settings.
    """

    ACTIONS = {
        "حالة النظام": "status",
        "الأسعار الحية": "prices",
        "آخر دورة": "cycle",
        "تشخيص 25 رمزًا": "diagnostics",
        "لماذا لا توجد صفقة؟": "why",
        "الاستراتيجيات": "strategies",
        "القرارات الأخيرة": "decisions",
        "الصفقات الورقية": "orders",
        "ملخص الأداء": "performance",
        "السجل والتدقيق": "logs",
        "فتح Dashboard": "dashboard",
        "المساعدة": "help",
    }
    STRATEGIES = {
        "TREND_PULLBACK": "الارتداد مع الاتجاه",
        "BREAKOUT_RETEST": "الاختراق وإعادة الاختبار",
        "LIQUIDITY_SWEEP_REVERSAL": "استرداد كسر السيولة",
        "RANGE_EDGE_REVERSION": "الارتداد من النطاق",
    }
    REASONS = {
        "SETUP_INCOMPLETE": "شروط الاستراتيجية غير مكتملة",
        "RR_TOO_LOW": "نسبة العائد إلى المخاطر منخفضة",
        "EV_DISABLED_UNTIL_CALIBRATED": "EV متوقف حتى المعايرة",
        "EV_INSUFFICIENT": "بيانات EV غير كافية",
        "QUALITY_BELOW_THRESHOLD": "الجودة أقل من الحد المطلوب",
        "DATA_UNSAFE": "البيانات غير آمنة",
        "RISK_LIMIT": "تجاوز حد المخاطر",
        "INVALID_ENTRY_PLAN": "خطة الدخول غير صالحة",
    }

    def __init__(
        self,
        token: str,
        *,
        webhook_secret: str | None = None,
        allowed_chat_ids: set[int] | None = None,
        dashboard_url: str = "https://mkmoon.onrender.com/",
        get_summary: SummaryProvider,
        get_tickers: TickerProvider | None = None,
        get_cycle: CycleProvider,
        get_decisions: ListProvider,
        get_orders: ListProvider,
        get_cycles: ListProvider,
        get_logs: ListProvider,
    ) -> None:
        self.token = token.strip()
        if not self.token:
            raise ValueError("Telegram token cannot be empty")
        derived_secret = hashlib.sha256(f"mkmoon-webhook:{self.token}".encode("utf-8")).hexdigest()
        candidate_secret = (webhook_secret or "").strip()
        self.webhook_secret = (
            candidate_secret
            if re.fullmatch(r"[A-Za-z0-9_-]{1,256}", candidate_secret)
            else derived_secret
        )
        self.allowed_chat_ids = allowed_chat_ids or set()
        self.dashboard_url = dashboard_url.rstrip("/") + "/"
        self.get_summary = get_summary
        self.get_tickers = get_tickers
        self.get_cycle = get_cycle
        self.get_decisions = get_decisions
        self.get_orders = get_orders
        self.get_cycles = get_cycles
        self.get_logs = get_logs
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))
        self._dispatch_limit = asyncio.Semaphore(2)
        self.started = False
        self.startup_error: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.token)

    async def close(self) -> None:
        await self.client.aclose()

    def verify_webhook_secret(self, supplied: str | None) -> bool:
        return bool(supplied) and secrets.compare_digest(supplied, self.webhook_secret)

    def is_allowed_chat(self, chat_id: int, chat_type: str | None) -> bool:
        # Fail closed: no chat can read system data until an explicit allowlist is configured.
        return chat_type == "private" and bool(self.allowed_chat_ids) and chat_id in self.allowed_chat_ids

    @classmethod
    def readable_reason(cls, value: object) -> str:
        raw = str(value or "—")
        return cls.REASONS.get(raw, raw)

    async def _api(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self.client.post(
            f"https://api.telegram.org/bot{self.token}/{method}",
            json=payload,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {body.get('description', 'unknown error')}")
        return body

    async def startup(self, public_base_url: str) -> None:
        try:
            me = await self._api("getMe", {})
        except Exception as error:
            self.startup_error = f"getMe_{type(error).__name__}"
            raise
        username = (me.get("result") or {}).get("username", "unknown")
        webhook_url = public_base_url.rstrip("/") + "/telegram/webhook"
        try:
            await self._api(
                "setWebhook",
                {
                    "url": webhook_url,
                    "secret_token": self.webhook_secret,
                    "allowed_updates": ["message", "callback_query"],
                    "max_connections": 5,
                    "drop_pending_updates": False,
                },
            )
        except Exception as error:
            self.startup_error = f"setWebhook_{type(error).__name__}"
            raise
        self.started = True
        self.startup_error = None
        log.info("Telegram webhook configured bot=%s", username)

    async def _send(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        await self._api("sendMessage", payload)

    async def _send_html(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        await self._api("sendMessage", payload)

    async def _send_long(self, chat_id: int, text: str) -> None:
        chunks: list[str] = []
        remaining = text
        while len(remaining) > 3800:
            split_at = remaining.rfind("\n", 0, 3800)
            split_at = split_at if split_at > 500 else 3800
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:].lstrip("\n")
        if remaining:
            chunks.append(remaining)
        for chunk in chunks or ["—"]:
            await self._send(chat_id, chunk)

    @staticmethod
    def main_keyboard() -> dict[str, Any]:
        return {
            "keyboard": [
                [{"text": "حالة النظام"}, {"text": "الأسعار الحية"}],
                [{"text": "آخر دورة"}, {"text": "تشخيص 25 رمزًا"}],

                [{"text": "لماذا لا توجد صفقة؟"}],
                [{"text": "الاستراتيجيات"}, {"text": "القرارات الأخيرة"}],
                [{"text": "الصفقات الورقية"}, {"text": "ملخص الأداء"}],
                [{"text": "السجل والتدقيق"}, {"text": "فتح Dashboard"}],
                [{"text": "المساعدة"}],
            ],
            "resize_keyboard": True,
            "is_persistent": True,
        }

    @staticmethod
    def strategy_keyboard() -> dict[str, Any]:
        return {
            "inline_keyboard": [
                [{"text": label, "callback_data": f"strategy:{key}"}]
                for key, label in TelegramBot.STRATEGIES.items()
            ]
        }

    @staticmethod
    def _status(summary: dict) -> str:
        worker = summary.get("paper_worker") or {}
        return (
            "<b>حالة Mkmoon</b>\n"
            f"الخدمة: <b>{html.escape(str(summary.get('service', '—')))}</b>\n"
            f"Worker: <b>{html.escape(str(worker.get('last_cycle_status') or '—'))}</b>\n"
            f"PostgreSQL: <b>{html.escape(str(summary.get('database', '—')))}</b>\n"
            f"Redis: <b>{html.escape(str(summary.get('redis', '—')))}</b>\n"
            f"وضع التداول: <b>{html.escape(str(summary.get('trading_mode', '—')))}</b>\n"
            f"التداول الحي: <b>{'مغلق' if summary.get('live_trading_enabled') is False else 'تحقق مطلوب'}</b>\n"
            f"الرموز في آخر دورة: <b>{html.escape(str(summary.get('worker_symbols_processed', '—')))} من {html.escape(str(summary.get('worker_symbols', '—')))}</b>"
        )

    @staticmethod
    def _cycle_text(detail: dict | None) -> str:
        if not detail:
            return "لا توجد دورة مسجلة بعد."
        run = detail.get("run") or {}
        summary = run.get("summary") or {}
        return (
            "<b>آخر دورة مكتملة</b>\n"
            f"الحالة: <b>{html.escape(str(run.get('status', '—')))}</b>\n"
            f"المعالجة: <b>{run.get('symbols_processed', 0)} من {run.get('symbols_requested', 0)}</b> رمز\n"
            f"القرارات: <b>{run.get('decisions_count', 0)}</b>\n"
            f"الأوامر الورقية: <b>{run.get('orders_created', 0)}</b>\n"
            f"الأخطاء: <b>{run.get('error_count', 0)}</b>\n"
            f"المدة: <b>{summary.get('duration_ms', '—')} ms</b>\n"
            f"المصدر: <code>{html.escape(str(summary.get('binance_data_source', '—')))}</code>\n"
            f"التشخيصات: <b>{len(summary.get('symbol_diagnostics') or {})}</b> رمز"
        )

    def _diagnostics_text(self, detail: dict | None) -> str:
        if not detail:
            return "لا يوجد تشخيص محفوظ بعد."
        summary = (detail.get("run") or {}).get("summary") or {}
        diagnostics = summary.get("symbol_diagnostics") or {}
        lines = ["تشخيص 25 رمزًا — Score التغطية وليس احتمال الربح:", ""]
        for symbol in sorted(diagnostics):
            item = diagnostics[symbol] or {}
            reasons = ", ".join(self.readable_reason(reason) for reason in (item.get("reason_codes") or [])) or "—"
            setup = item.get("setup") or "لا استراتيجية مكتملة"
            quality = item.get("quality_score") or "—"
            strategies = item.get("strategy_diagnostics") or []
            scores = ", ".join(
                f"{self.STRATEGIES.get(str(strategy.get('strategy', '')), str(strategy.get('strategy', '')))} {strategy.get('passed_conditions', 0)}/{strategy.get('total_conditions', 0)}"
                for strategy in strategies
            )
            lines.append(f"{symbol}: {item.get('decision_status', item.get('status', '—'))} | {scores} | {setup} | Q={quality} | {reasons}")
        return "\n".join(lines)

    def _why_text(self, detail: dict | None) -> str:
        if not detail:
            return "لا توجد دورة لتحليل أسباب المنع."
        summary = (detail.get("run") or {}).get("summary") or {}
        reasons = summary.get("reason_counts") or {}
        statuses = summary.get("decision_status_counts") or {}
        lines = ["<b>لماذا لا توجد صفقة؟</b>", "حالات القرار:"]
        lines.extend(f"• {html.escape(str(key))}: <b>{value}</b>" for key, value in statuses.items())
        lines.append("أسباب المنع:")
        lines.extend(f"• <code>{html.escape(self.readable_reason(key))}</code>: <b>{value}</b>" for key, value in reasons.items())
        if not reasons:
            lines.append("• لا توجد أسباب منع مسجلة في هذه الدورة.")
        return "\n".join(lines)

    @staticmethod
    def _price_text(rows: list[dict]) -> str:
        if not rows:
            return "لا توجد أسعار متاحة حاليًا من Binance."
        lines = [
            "<b>الأسعار الحية</b>",
            "قراءة عامة من Binance عند الضغط — لا تُشغّل دورة Worker ولا تنفذ أوامر.",
            "",
        ]
        for row in rows:
            symbol = html.escape(str(row.get("symbol") or "—"))
            price = html.escape(str(row.get("price") or "—"))
            change = row.get("change_percent")
            change_text = "غير متاح" if change in (None, "", "—") else f"{html.escape(str(change))}%"
            lines.append(f"<code>{symbol}</code> | <b>{price}</b> USDT | 24h: {change_text}")
        return "\n".join(lines)

    async def _action(self, chat_id: int, action: str) -> None:
        if action == "status":
            await self._send_html(chat_id, self._status(await self.get_summary()), self.main_keyboard())
        elif action == "prices":
            if self.get_tickers is None:
                await self._send(chat_id, "مصدر الأسعار الحية غير مهيأ حاليًا.", self.main_keyboard())
                return
            try:
                rows = await self.get_tickers()
            except Exception as error:
                log.warning("Telegram live prices unavailable error_type=%s", type(error).__name__)
                await self._send(
                    chat_id,
                    "تعذر قراءة الأسعار العامة من Binance حاليًا. حاول بعد قليل؛ لم تتأثر دورة Worker.",
                    self.main_keyboard(),
                )
                return
            await self._send_html(chat_id, self._price_text(rows), self.main_keyboard())
        elif action == "cycle":
            await self._send_html(chat_id, self._cycle_text(await self.get_cycle()), self.main_keyboard())
        elif action == "diagnostics":
            await self._send_long(chat_id, self._diagnostics_text(await self.get_cycle()))
        elif action == "why":
            await self._send_html(chat_id, self._why_text(await self.get_cycle()), self.main_keyboard())
        elif action == "strategies":
            await self._send_html(chat_id, "اختر استراتيجية لعرض تغطية شروطها عبر الرموز:", self.strategy_keyboard())
        elif action == "decisions":
            rows = await self.get_decisions()
            lines = ["القرارات الأخيرة:"] + [
                f"{row.get('symbol', '—')} | {row.get('status', '—')} | {', '.join(self.readable_reason(reason) for reason in ((row.get('payload') or {}).get('reasons') or [])[:3]) or 'بدون سبب'}"
                for row in rows[:30]
            ]

            await self._send_long(chat_id, "\n".join(lines),)
        elif action == "orders":
            rows = await self.get_orders()
            if not rows:
                await self._send(chat_id, "لا توجد صفقات ورقية محفوظة حاليًا.", self.main_keyboard())
            else:
                lines = ["الصفقات الورقية:"] + [
                    f"{row.get('symbol', '—')} | {row.get('status', '—')} | qty={row.get('filled_quantity', 0)} | price={row.get('average_fill_price') or row.get('requested_price', '—')}"
                    for row in rows[:30]
                ]
                await self._send_long(chat_id, "\n".join(lines))
        elif action == "performance":
            rows = await self.get_cycles()
            completed = [row for row in rows if row.get("status") == "COMPLETED"]
            durations = [int(row["finished_at_ms"] - row["started_at_ms"]) for row in completed if row.get("finished_at_ms") and row.get("started_at_ms")]
            avg_duration = round(sum(durations) / len(durations)) if durations else 0
            errors = sum(int(row.get("error_count") or 0) for row in rows)
            orders = sum(int(row.get("orders_created") or 0) for row in rows)
            text = (
                "<b>ملخص الأداء التشغيلي</b>\n"
                f"الدورات المقروءة: <b>{len(rows)}</b>\n"
                f"المكتملة: <b>{len(completed)}</b>\n"
                f"متوسط زمن المكتملة: <b>{avg_duration} ms</b>\n"
                f"الأخطاء المسجلة: <b>{errors}</b>\n"
                f"أوامر Paper: <b>{orders}</b>\n\n"
                "هذا ليس تقييمًا للربحية؛ الربحية تحتاج عينة Paper كافية."
            )
            await self._send_html(chat_id, text, self.main_keyboard())
        elif action == "logs":
            detail = await self.get_cycle()
            events = (detail or {}).get("events") or []
            if events:
                lines = ["آخر أحداث التدقيق:"] + [
                    f"{event.get('stage', '—')} | {event.get('status', '—')} | {event.get('symbol') or 'الدورة'} | {', '.join(self.readable_reason(reason) for reason in (event.get('reason_codes') or [])) or 'بدون سبب'}"
                    for event in events[-40:]
                ]
            else:
                rows = await self.get_logs()
                lines = ["سجل التطبيق:"] + [
                    f"{row.get('level', '—')} | {row.get('logger', '—')} | {row.get('message', '—')}"
                    for row in rows[:40]
                ]
            await self._send_long(chat_id, "\n".join(lines))
        elif action == "dashboard":
            await self._send_html(chat_id, f"Dashboard: <a href=\"{html.escape(self.dashboard_url)}\">فتح اللوحة</a>", self.main_keyboard())
        elif action == "help":
            await self._send_html(
                chat_id,
                "<b>مساعدة Mkmoon</b>\n"
                "البوت للقراءة والتنبيهات فقط. Worker يحلل تلقائيًا، والأزرار تقرأ النتائج المحفوظة.\n"
                "الأسعار الحية تُقرأ من Binance عند الضغط فقط، ولا تبدأ دورة جديدة.\n"
                "Score التغطية = نسبة الشروط المحققة، وليس احتمال الربح. Quality Score يظهر فقط عند اكتمال candidate.\n"
                "لا توجد أزرار شراء أو بيع أو تفعيل Live.",
                self.main_keyboard(),
            )

    async def _strategy(self, chat_id: int, strategy: str) -> None:
        detail = await self.get_cycle()
        summary = (detail or {}).get("run", {}).get("summary", {}) if detail else {}
        diagnostics = summary.get("symbol_diagnostics") or {}
        label = self.STRATEGIES.get(strategy, strategy)
        lines = [f"{label} — Score التغطية لكل رمز:", ""]
        for symbol in sorted(diagnostics):
            items = [item for item in (diagnostics[symbol] or {}).get("strategy_diagnostics", []) if item.get("strategy") == strategy]
            if not items:
                continue
            item = items[0]
            lines.append(f"{symbol}: {item.get('passed_conditions', 0)}/{item.get('total_conditions', 0)} = {item.get('score', 0)}/100 | {item.get('first_failed_condition_ar') or 'جاهزة'}")
        await self._send_long(chat_id, "\n".join(lines) or "لا يوجد تشخيص محفوظ لهذه الاستراتيجية.")

    async def handle_update(self, update: dict[str, Any]) -> None:
        async with self._dispatch_limit:
            try:
                callback = update.get("callback_query") or {}
                if callback:
                    message = callback.get("message") or {}
                    chat = message.get("chat") or {}
                    chat_id = int(chat["id"])
                    if not self.is_allowed_chat(chat_id, chat.get("type")):
                        await self._api("answerCallbackQuery", {"callback_query_id": callback.get("id"), "text": "غير مصرح"})
                        return
                    await self._api("answerCallbackQuery", {"callback_query_id": callback.get("id")})
                    data = str(callback.get("data") or "")
                    if data.startswith("strategy:"):
                        await self._strategy(chat_id, data.split(":", 1)[1])
                    return
                message = update.get("message") or {}
                chat = message.get("chat") or {}
                if not chat:
                    return
                chat_id = int(chat["id"])
                text = str(message.get("text") or "").strip()
                if text == "/id":
                    await self._send_html(chat_id, f"معرف هذه المحادثة هو: <code>{chat_id}</code>")
                    return
                if not self.is_allowed_chat(chat_id, chat.get("type")):
                    await self._send(chat_id, "هذه المحادثة غير مصرح لها بعد. استخدم /id وأرسل المعرف إلى مسؤول النظام.")
                    return
                if text in {"/start", "/menu", "القائمة"}:
                    await self._send_html(chat_id, "<b>مرحبًا بك في Mkmoon</b>\nاختر ما تريد قراءته:", self.main_keyboard())
                    return
                action = self.ACTIONS.get(text)
                if action:
                    await self._action(chat_id, action)
                elif text:
                    await self._send(chat_id, "استخدم أزرار القائمة أو أرسل /start لعرضها.", self.main_keyboard())
            except Exception:
                # Never include the token or the raw update in logs.
                log.exception("Telegram update handling failed")
