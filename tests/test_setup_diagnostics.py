from decimal import Decimal

from app.core.models import FeatureSnapshot, RegimeVector, SetupType
from app.engine.setups import evaluate_setup_diagnostics


def test_all_strategies_return_condition_diagnostics_when_data_is_incomplete() -> None:
    features = FeatureSnapshot(
        symbol="BTCUSDT",
        decision_time_ms=1000,
        data_cutoff_ms=900,
        values={},
        valid=False,
        invalid_reasons=["NO_DATA"],
    )
    regime = RegimeVector(
        trend_direction="UNKNOWN",
        trend_strength="UNKNOWN",
        volatility_phase="UNKNOWN",
        structure_mode="UNKNOWN",
        market_safety="UNSAFE",
        liquidity_state="UNKNOWN",
        reversal_status="UNKNOWN",
        available_time_ms=900,
    )

    diagnostics = evaluate_setup_diagnostics({}, features, regime)

    assert [item["strategy"] for item in diagnostics] == [setup.value for setup in SetupType]
    assert len(diagnostics) == 4
    assert all(item["status"] == "INCOMPLETE" for item in diagnostics)
    assert all(item["total_conditions"] >= 8 for item in diagnostics)
    assert all(item["passed_conditions"] == 0 for item in diagnostics)
    assert all(len(item["conditions"]) == item["total_conditions"] for item in diagnostics)
    assert all(item["progress_pct"] == Decimal("0") for item in diagnostics)


def test_strategy_diagnostic_contains_human_readable_condition_fields() -> None:
    features = FeatureSnapshot(
        symbol="BTCUSDT",
        decision_time_ms=1000,
        data_cutoff_ms=900,
        values={},
        valid=False,
        invalid_reasons=["NO_DATA"],
    )
    regime = RegimeVector(
        trend_direction="UNKNOWN",
        trend_strength="UNKNOWN",
        volatility_phase="UNKNOWN",
        structure_mode="UNKNOWN",
        market_safety="UNSAFE",
        liquidity_state="UNKNOWN",
        reversal_status="UNKNOWN",
        available_time_ms=900,
    )

    diagnostic = evaluate_setup_diagnostics({}, features, regime)[0]
    first = diagnostic["conditions"][0]

    assert first["key"] == "features_valid"
    assert first["label_ar"] == "الميزات الحسابية مكتملة"
    assert first["passed"] is False
    assert "expected" in first
