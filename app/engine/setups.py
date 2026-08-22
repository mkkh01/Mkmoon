from dataclasses import dataclass
from decimal import Decimal

from app.core.models import Candle, FeatureSnapshot, RegimeVector, SetupType


@dataclass(frozen=True)
class SetupCandidate:
    setup: SetupType
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    component_scores: dict[str, Decimal]


def _latest(candles: list[Candle], count: int) -> list[Candle]:
    closed = sorted((c for c in candles if c.is_closed), key=lambda c: c.open_time_ms)
    return closed[-count:]


def _bullish_trigger(candles: list[Candle]) -> bool:
    last_two = _latest(candles, 2)
    return len(last_two) == 2 and last_two[-1].close > last_two[-1].open and last_two[-1].close > last_two[-2].high


def evaluate_setup(
    candles_by_timeframe: dict[str, list[Candle]],
    features: FeatureSnapshot,
    regime: RegimeVector,
) -> SetupCandidate | None:
    if not features.valid or regime.market_safety != "TRADABLE":
        return None
    c15 = _latest(candles_by_timeframe.get("15m", []), 25)
    c5 = _latest(candles_by_timeframe.get("5m", []), 3)
    if len(c15) < 21 or len(c5) < 2:
        return None

    last = c15[-1]
    previous = c15[:-1]
    atr = features.values.get("15m.atr")
    ema20 = features.values.get("15m.ema20")
    if atr is None or atr <= 0:
        return None

    if (
        regime.trend_direction == "UP"
        and regime.trend_strength in {"STRONG", "MODERATE"}
        and ema20 is not None
        and last.low <= ema20 + atr
        and last.close >= ema20
        and _bullish_trigger(c5)
    ):
        entry = last.close
        stop = min(last.low, ema20 - atr * Decimal("0.75"))
        target = entry + (entry - stop) * Decimal("2")
        return SetupCandidate(SetupType.TREND_PULLBACK, entry, stop, target, {
            "regime": Decimal("90"), "structure": Decimal("80"), "timing": Decimal("85"),
            "volume": Decimal("70"), "zone": Decimal("70"),
        })

    range_high = max(c.high for c in previous[-20:])
    range_low = min(c.low for c in previous[-20:])
    if last.close > range_high and last.volume >= sum(c.volume for c in previous[-5:]) / Decimal("5"):
        if _bullish_trigger(c5):
            entry = last.close
            stop = range_high - atr * Decimal("0.50")
            target = entry + (entry - stop) * Decimal("1.8")
            return SetupCandidate(SetupType.BREAKOUT_RETEST, entry, stop, target, {
                "regime": Decimal("80"), "structure": Decimal("85"), "timing": Decimal("75"),
                "volume": Decimal("80"), "zone": Decimal("65"),
            })

    sweep_level = min(c.low for c in previous[-10:])
    if last.low < sweep_level and last.close > sweep_level and _bullish_trigger(c5):
        entry = last.close
        stop = last.low - atr * Decimal("0.25")
        target = entry + (entry - stop) * Decimal("1.7")
        return SetupCandidate(SetupType.SWEEP_REVERSAL, entry, stop, target, {
            "regime": Decimal("70"), "structure": Decimal("75"), "timing": Decimal("80"),
            "volume": Decimal("65"), "zone": Decimal("85"),
        })

    width = range_high - range_low
    near_low = last.close <= range_low + width * Decimal("0.25") if width > 0 else False
    if regime.structure_mode == "RANGE" and near_low and _bullish_trigger(c5):
        entry = last.close
        stop = range_low - atr * Decimal("0.50")
        target = range_high - width * Decimal("0.15")
        return SetupCandidate(SetupType.RANGE_REVERSION, entry, stop, target, {
            "regime": Decimal("75"), "structure": Decimal("75"), "timing": Decimal("75"),
            "volume": Decimal("60"), "zone": Decimal("80"),
        })
    return None
