from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.core.models import FeatureSnapshot, RegimeVector
from app.core.policy import load_policy
from app.engine.setups import SetupCandidate


DEFAULT_WEIGHTS: dict[str, Decimal] = {
    "regime": Decimal("15"), "mtf": Decimal("15"), "structure": Decimal("15"),
    "liquidity": Decimal("10"), "zone": Decimal("10"), "volume": Decimal("10"),
    "momentum": Decimal("5"), "volatility": Decimal("5"), "timing": Decimal("10"),
    "setup_quality": Decimal("5"),
}


def _weights(policy_payload: dict[str, Any] | None) -> dict[str, Decimal]:
    payload = policy_payload or load_policy()
    result = {key: Decimal(str(value)) for key, value in payload["score_weights"].items()}
    if sum(result.values(), Decimal("0")) != Decimal("100"):
        raise ValueError("score weights must sum to 100")
    return result


def score_candidate(
    candidate: SetupCandidate,
    features: FeatureSnapshot,
    regime: RegimeVector,
    policy_payload: dict[str, Any] | None = None,
) -> tuple[Decimal, dict[str, Decimal]]:
    if not features.valid:
        return Decimal("0"), {}
    weights = _weights(policy_payload)
    v = features.values
    scores = {key: candidate.component_scores.get(key, Decimal("0")) for key in weights}
    scores["setup_quality"] = min(Decimal("100"), sum(scores.values(), Decimal("0")) / Decimal("5"))

    rsi = v.get("5m.rsi")
    if rsi is not None:
        scores["momentum"] = Decimal("70") if Decimal("45") <= rsi <= Decimal("70") else Decimal("45")
    atr_pct = v.get("15m.atr_percentile")
    if atr_pct is not None:
        scores["volatility"] = Decimal("80") if Decimal("10") <= atr_pct <= Decimal("90") else Decimal("40")
    scores["mtf"] = Decimal("90") if regime.trend_direction == "UP" else Decimal("45")
    scores["liquidity"] = Decimal("80") if regime.liquidity_state == "HEALTHY" else Decimal("35")

    total = sum((weights[key] / Decimal("100")) * scores[key] for key in weights)
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), scores
