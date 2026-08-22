from decimal import Decimal, getcontext

from app.core.models import Candle, FeatureSnapshot

getcontext().prec = 28


def _ordered(candles: list[Candle]) -> list[Candle]:
    ordered = sorted(candles, key=lambda c: c.open_time_ms)
    for candle in ordered:
        candle.validate_ohlc()
    if any(a.open_time_ms == b.open_time_ms for a, b in zip(ordered, ordered[1:])):
        raise ValueError("duplicate candle identity")
    return ordered


def _sma(values: list[Decimal], window: int) -> Decimal | None:
    if len(values) < window:
        return None
    return sum(values[-window:], Decimal("0")) / Decimal(window)


def _ema(values: list[Decimal], window: int) -> Decimal | None:
    if len(values) < window:
        return None
    alpha = Decimal("2") / Decimal(window + 1)
    current = sum(values[:window], Decimal("0")) / Decimal(window)
    for value in values[window:]:
        current = alpha * value + (Decimal("1") - alpha) * current
    return current


def _true_ranges(candles: list[Candle]) -> list[Decimal]:
    result: list[Decimal] = []
    previous_close: Decimal | None = None
    for candle in candles:
        if previous_close is None:
            tr = candle.high - candle.low
        else:
            tr = max(candle.high - candle.low, abs(candle.high - previous_close), abs(candle.low - previous_close))
        result.append(tr)
        previous_close = candle.close
    return result


def _atr(candles: list[Candle], window: int = 14) -> Decimal | None:
    return _sma(_true_ranges(candles), window)


def _percentile_rank(history: list[Decimal], value: Decimal) -> Decimal | None:
    if not history:
        return None
    below_or_equal = sum(1 for item in history if item <= value)
    return (Decimal(below_or_equal) / Decimal(len(history))) * Decimal("100")


def _rsi(candles: list[Candle], window: int = 14) -> Decimal | None:
    if len(candles) < window + 1:
        return None
    changes = [candles[i].close - candles[i - 1].close for i in range(1, len(candles))]
    gains = [max(change, Decimal("0")) for change in changes]
    losses = [max(-change, Decimal("0")) for change in changes]
    avg_gain = _sma(gains, window)
    avg_loss = _sma(losses, window)
    if avg_gain is None or avg_loss is None:
        return None
    if avg_loss == 0:
        return Decimal("100")
    rs = avg_gain / avg_loss
    return Decimal("100") - (Decimal("100") / (Decimal("1") + rs))


def compute_features(symbol: str, candles_by_timeframe: dict[str, list[Candle]], decision_time_ms: int) -> FeatureSnapshot:
    values: dict[str, Decimal | None] = {}
    invalid: list[str] = []
    cutoff_candidates: list[int] = []

    for timeframe, raw in candles_by_timeframe.items():
        candles = _ordered([c for c in raw if c.is_closed and c.close_time_ms <= decision_time_ms])
        if not candles:
            invalid.append(f"NO_CLOSED_CANDLES:{timeframe}")
            continue
        cutoff_candidates.append(candles[-1].close_time_ms)
        closes = [c.close for c in candles]
        atr_values: list[Decimal] = []
        for index in range(14, len(candles) + 1):
            candidate = _atr(candles[:index], 14)
            if candidate is not None:
                atr_values.append(candidate)
        current_atr = atr_values[-1] if atr_values else None
        prefix = timeframe.lower()
        values[f"{prefix}.atr"] = current_atr
        values[f"{prefix}.atr_percentile"] = _percentile_rank(atr_values[-100:], current_atr) if current_atr else None
        values[f"{prefix}.rsi"] = _rsi(candles)
        values[f"{prefix}.ema20"] = _ema(closes, 20)
        values[f"{prefix}.ema50"] = _ema(closes, 50)
        values[f"{prefix}.ema20_slope"] = (
            (closes[-1] - closes[-6]) / closes[-6] * Decimal("100") if len(closes) >= 6 and closes[-6] else None
        )
        volumes = [c.volume for c in candles]
        avg_volume = _sma(volumes, 20)
        values[f"{prefix}.relative_volume"] = volumes[-1] / avg_volume if avg_volume else None
        lookback = min(20, len(candles) - 1)
        if lookback > 0:
            net_change = abs(closes[-1] - closes[-1 - lookback])
            path = sum(abs(closes[i] - closes[i - 1]) for i in range(len(candles) - lookback, len(candles)))
            values[f"{prefix}.efficiency_ratio"] = net_change / path if path else Decimal("0")
        else:
            values[f"{prefix}.efficiency_ratio"] = None

    if not cutoff_candidates:
        invalid.append("NO_DATA")
    required = ["4h.atr", "1h.atr", "15m.atr", "5m.atr"]
    for key in required:
        if values.get(key) is None:
            invalid.append(f"MISSING_CRITICAL_FEATURE:{key}")

    return FeatureSnapshot(
        symbol=symbol,
        decision_time_ms=decision_time_ms,
        data_cutoff_ms=min(cutoff_candidates) if cutoff_candidates else decision_time_ms,
        values=values,
        valid=not invalid,
        invalid_reasons=sorted(set(invalid)),
    )
