from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.core.models import FeatureSnapshot, RegimeVector
from app.core.policy import load_policy


class RegimePolicy:
    def __init__(self, mapping: dict[str, Any]):
        self.min_efficiency_trend = Decimal(str(mapping["min_efficiency_trend"]))
        self.strong_efficiency = Decimal(str(mapping["strong_efficiency"]))
        self.trend_slope_pct = Decimal(str(mapping["trend_slope_pct"]))
        self.low_vol_percentile = Decimal(str(mapping["low_vol_percentile"]))
        self.high_vol_percentile = Decimal(str(mapping["high_vol_percentile"]))
        self.min_relative_volume = Decimal(str(mapping["min_relative_volume"]))
        self.max_relative_volume = Decimal(str(mapping["max_relative_volume"]))


def classify_regime(features: FeatureSnapshot, policy_payload: dict[str, Any] | None = None) -> RegimeVector:
    payload = policy_payload or load_policy()
    policy = RegimePolicy(payload["regime"])
    v = features.values
    trend_slope = v.get("1h.ema20_slope")
    efficiency = v.get("1h.efficiency_ratio")
    atr_pct = v.get("15m.atr_percentile")
    rel_volume = v.get("15m.relative_volume")
    ema20 = v.get("1h.ema20")
    ema50 = v.get("1h.ema50")

    if not features.valid:
        safety = "UNSAFE"
    elif rel_volume is not None and rel_volume > policy.max_relative_volume:
        safety = "DEGRADED"
    else:
        safety = "TRADABLE"

    direction = "NEUTRAL"
    if ema20 is not None and ema50 is not None:
        direction = "UP" if ema20 > ema50 else "DOWN" if ema20 < ema50 else "NEUTRAL"

    strength = "NONE"
    if efficiency is not None and efficiency >= policy.strong_efficiency:
        strength = "STRONG"
    elif efficiency is not None and efficiency >= policy.min_efficiency_trend:
        strength = "MODERATE"
    elif efficiency is not None:
        strength = "WEAK"

    phase = "NORMAL"
    if atr_pct is not None and atr_pct <= policy.low_vol_percentile:
        phase = "COMPRESSION"
    elif atr_pct is not None and atr_pct >= policy.high_vol_percentile:
        phase = "EXPANSION"

    structure = "RANGE"
    if efficiency is not None and efficiency >= policy.min_efficiency_trend:
        structure = "TREND"
    if trend_slope is not None and abs(trend_slope) < policy.trend_slope_pct:
        structure = "CHOPPY" if efficiency is not None and efficiency < policy.min_efficiency_trend else structure

    liquidity = "HEALTHY"
    if rel_volume is not None and rel_volume < policy.min_relative_volume:
        liquidity = "THIN"
    elif rel_volume is not None and rel_volume > policy.max_relative_volume:
        liquidity = "ABNORMAL"

    return RegimeVector(
        trend_direction=direction,
        trend_strength=strength,
        volatility_phase=phase,
        structure_mode=structure,
        market_safety=safety,
        liquidity_state=liquidity,
        reversal_status="NONE",
        available_time_ms=features.data_cutoff_ms,
        version=str(payload["version"]),
    )
