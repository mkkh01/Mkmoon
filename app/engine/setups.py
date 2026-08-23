from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.core.models import Candle, FeatureSnapshot, RegimeVector, SetupType


SETUP_LABELS_AR: dict[SetupType, str] = {
    SetupType.TREND_PULLBACK: "ارتداد مع الاتجاه",
    SetupType.BREAKOUT_RETEST: "اختراق وإعادة اختبار",
    SetupType.SWEEP_REVERSAL: "استرداد بعد كسر سيولة",
    SetupType.RANGE_REVERSION: "ارتداد من نطاق",
}


@dataclass(frozen=True)
class SetupCandidate:
    setup: SetupType
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    component_scores: dict[str, Decimal]


@dataclass(frozen=True)
class StrategyDiagnostic:
    setup: SetupType
    conditions: tuple[dict[str, Any], ...]
    candidate: SetupCandidate | None = None
    blocked_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        passed = sum(1 for condition in self.conditions if condition["passed"])
        total = len(self.conditions)
        failed = [condition["label_ar"] for condition in self.conditions if not condition["passed"]]
        result: dict[str, Any] = {
            "strategy": self.setup.value,
            "label_ar": SETUP_LABELS_AR[self.setup],
            "passed_conditions": passed,
            "total_conditions": total,
            "progress_pct": round((passed / total) * 100, 2) if total else 0,
            "score": round((passed / total) * 100, 2) if total else 0,
            "score_basis": "condition_coverage",
            "ready": self.candidate is not None,
            "status": "READY" if self.candidate is not None else "INCOMPLETE",
            "first_failed_condition_ar": failed[0] if failed else None,
            "blocked_reason": self.blocked_reason,
            "conditions": list(self.conditions),
        }
        if self.candidate is not None:
            result["component_scores"] = {
                key: str(value) for key, value in self.candidate.component_scores.items()
            }
            result["entry"] = str(self.candidate.entry_price)
            result["stop"] = str(self.candidate.stop_price)
            result["target"] = str(self.candidate.target_price)
        else:
            result["component_scores"] = {}
        return result


def _latest(candles: list[Candle], count: int) -> list[Candle]:
    closed = sorted((c for c in candles if c.is_closed), key=lambda c: c.open_time_ms)
    return closed[-count:]


def _bullish_trigger(candles: list[Candle]) -> bool:
    last_two = _latest(candles, 2)
    return len(last_two) == 2 and last_two[-1].close > last_two[-1].open and last_two[-1].close > last_two[-2].high


def _condition(
    key: str,
    label_ar: str,
    passed: bool,
    *,
    actual: object | None = None,
    expected: object | None = None,
) -> dict[str, Any]:
    condition: dict[str, Any] = {"key": key, "label_ar": label_ar, "passed": bool(passed)}
    if actual is not None:
        condition["actual"] = str(actual)
    if expected is not None:
        condition["expected"] = str(expected)
    return condition


def _all_passed(conditions: list[dict[str, Any]]) -> bool:
    return all(condition["passed"] for condition in conditions)


def _evaluate_setup_details(
    candles_by_timeframe: dict[str, list[Candle]],
    features: FeatureSnapshot,
    regime: RegimeVector,
) -> list[StrategyDiagnostic]:
    c15 = _latest(candles_by_timeframe.get("15m", []), 25)
    c5 = _latest(candles_by_timeframe.get("5m", []), 3)
    has_15m_history = len(c15) >= 21
    has_5m_history = len(c5) >= 2
    atr = features.values.get("15m.atr")
    ema20 = features.values.get("15m.ema20")
    has_atr = atr is not None and atr > 0
    last = c15[-1] if c15 else None
    previous = c15[:-1]
    history_ready = has_15m_history and has_5m_history and has_atr and last is not None
    range_high = max((c.high for c in previous[-20:]), default=None) if len(previous) >= 20 else None
    range_low = min((c.low for c in previous[-20:]), default=None) if len(previous) >= 20 else None
    sweep_level = min((c.low for c in previous[-10:]), default=None) if len(previous) >= 10 else None
    bullish_trigger = _bullish_trigger(c5)
    volume_average = (
        sum((c.volume for c in previous[-5:]), Decimal("0")) / Decimal("5")
        if len(previous) >= 5 else None
    )
    width = range_high - range_low if range_high is not None and range_low is not None else None
    near_low = bool(
        last is not None and range_low is not None and width is not None and width > 0
        and last.close <= range_low + width * Decimal("0.25")
    )

    common = [
        _condition("features_valid", "الميزات الحسابية مكتملة", features.valid, actual=features.valid, expected=True),
        _condition("market_tradable", "السوق قابل للتداول", regime.market_safety == "TRADABLE", actual=regime.market_safety, expected="TRADABLE"),
        _condition("history_15m", "تاريخ 15m كافٍ (21 شمعة)", has_15m_history, actual=len(c15), expected=">=21"),
        _condition("history_5m", "تاريخ 5m كافٍ (شمعتان)", has_5m_history, actual=len(c5), expected=">=2"),
        _condition("atr_valid", "ATR صالح للمخاطر", has_atr, actual=atr, expected=">0"),
    ]

    diagnostics: list[StrategyDiagnostic] = []

    trend_conditions = common + [
        _condition("trend_up", "الاتجاه العام صاعد", regime.trend_direction == "UP", actual=regime.trend_direction, expected="UP"),
        _condition("trend_strength", "قوة الاتجاه قوية أو متوسطة", regime.trend_strength in {"STRONG", "MODERATE"}, actual=regime.trend_strength, expected="STRONG|MODERATE"),
        _condition(
            "pullback_zone", "السعر داخل منطقة EMA20 + ATR", bool(history_ready and last and ema20 is not None and atr is not None and last.low <= ema20 + atr),
            actual=last.low if last else None, expected="low <= EMA20 + ATR",
        ),
        _condition("close_above_ema20", "الإغلاق فوق EMA20", bool(history_ready and last and ema20 is not None and last.close >= ema20), actual=last.close if last else None, expected=">= EMA20"),
        _condition("bullish_trigger", "مُحفز 5m صاعد", bullish_trigger, actual=bullish_trigger, expected=True),
    ]
    trend_candidate = None
    if _all_passed(trend_conditions):
        entry = last.close
        stop = min(last.low, ema20 - atr * Decimal("0.75"))
        target = entry + (entry - stop) * Decimal("2")
        trend_candidate = SetupCandidate(SetupType.TREND_PULLBACK, entry, stop, target, {
            "regime": Decimal("90"), "structure": Decimal("80"), "timing": Decimal("85"),
            "volume": Decimal("70"), "zone": Decimal("70"),
        })
    diagnostics.append(StrategyDiagnostic(SetupType.TREND_PULLBACK, tuple(trend_conditions), trend_candidate))

    breakout_conditions = common + [
        _condition("breakout_close", "الإغلاق فوق قمة نطاق 20 شمعة", bool(history_ready and range_high is not None and last and last.close > range_high), actual=last.close if last else None, expected="> range_high"),
        _condition("supportive_volume", "الحجم يساوي أو يتجاوز متوسط 5 شموع", bool(history_ready and volume_average is not None and last and last.volume >= volume_average), actual=last.volume if last else None, expected=">= avg_volume_5"),
        _condition("bullish_trigger", "مُحفز 5m صاعد", bullish_trigger, actual=bullish_trigger, expected=True),
    ]
    breakout_candidate = None
    if _all_passed(breakout_conditions):
        entry = last.close
        stop = range_high - atr * Decimal("0.50")
        target = entry + (entry - stop) * Decimal("1.8")
        breakout_candidate = SetupCandidate(SetupType.BREAKOUT_RETEST, entry, stop, target, {
            "regime": Decimal("80"), "structure": Decimal("85"), "timing": Decimal("75"),
            "volume": Decimal("80"), "zone": Decimal("65"),
        })
    diagnostics.append(StrategyDiagnostic(SetupType.BREAKOUT_RETEST, tuple(breakout_conditions), breakout_candidate))

    sweep_conditions = common + [
        _condition("sweep_below_level", "القاع كسر أدنى مستوى للـ10 شموع السابقة", bool(history_ready and sweep_level is not None and last and last.low < sweep_level), actual=last.low if last else None, expected="< sweep_level"),
        _condition("reclaim_level", "الإغلاق استعاد مستوى السيولة", bool(history_ready and sweep_level is not None and last and last.close > sweep_level), actual=last.close if last else None, expected="> sweep_level"),
        _condition("bullish_trigger", "مُحفز 5m صاعد", bullish_trigger, actual=bullish_trigger, expected=True),
    ]
    sweep_candidate = None
    if _all_passed(sweep_conditions):
        entry = last.close
        stop = last.low - atr * Decimal("0.25")
        target = entry + (entry - stop) * Decimal("1.7")
        sweep_candidate = SetupCandidate(SetupType.SWEEP_REVERSAL, entry, stop, target, {
            "regime": Decimal("70"), "structure": Decimal("75"), "timing": Decimal("80"),
            "volume": Decimal("65"), "zone": Decimal("85"),
        })
    diagnostics.append(StrategyDiagnostic(SetupType.SWEEP_REVERSAL, tuple(sweep_conditions), sweep_candidate))

    range_conditions = common + [
        _condition("range_structure", "بنية السوق Range", regime.structure_mode == "RANGE", actual=regime.structure_mode, expected="RANGE"),
        _condition("near_range_low", "السعر قريب من قاع النطاق", near_low, actual=last.close if last else None, expected="within lower 25%"),
        _condition("bullish_trigger", "مُحفز 5m صاعد", bullish_trigger, actual=bullish_trigger, expected=True),
    ]
    range_candidate = None
    if _all_passed(range_conditions):
        entry = last.close
        stop = range_low - atr * Decimal("0.50")
        target = range_high - width * Decimal("0.15")
        range_candidate = SetupCandidate(SetupType.RANGE_REVERSION, entry, stop, target, {
            "regime": Decimal("75"), "structure": Decimal("75"), "timing": Decimal("75"),
            "volume": Decimal("60"), "zone": Decimal("80"),
        })
    diagnostics.append(StrategyDiagnostic(SetupType.RANGE_REVERSION, tuple(range_conditions), range_candidate))
    return diagnostics


def evaluate_setup_with_diagnostics(
    candles_by_timeframe: dict[str, list[Candle]],
    features: FeatureSnapshot,
    regime: RegimeVector,
) -> tuple[SetupCandidate | None, list[dict[str, Any]]]:
    diagnostics = _evaluate_setup_details(candles_by_timeframe, features, regime)
    candidate = next((item.candidate for item in diagnostics if item.candidate is not None), None)
    return candidate, [item.as_dict() for item in diagnostics]


def evaluate_setup_diagnostics(
    candles_by_timeframe: dict[str, list[Candle]],
    features: FeatureSnapshot,
    regime: RegimeVector,
) -> list[dict[str, Any]]:
    return evaluate_setup_with_diagnostics(candles_by_timeframe, features, regime)[1]


def evaluate_setup(
    candles_by_timeframe: dict[str, list[Candle]],
    features: FeatureSnapshot,
    regime: RegimeVector,
) -> SetupCandidate | None:
    return evaluate_setup_with_diagnostics(candles_by_timeframe, features, regime)[0]
