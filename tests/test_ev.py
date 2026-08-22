from decimal import Decimal

from app.engine.ev import estimate_ev


def test_ev_requires_minimum_sample() -> None:
    result = estimate_ev([Decimal("1"), Decimal("-1")], minimum_sample=3)
    assert result.status == "INSUFFICIENT_DATA"
    assert result.ev_r is None


def test_ev_does_not_call_uncertain_result_valid() -> None:
    values = [Decimal("1")] * 50 + [Decimal("-1")] * 50
    result = estimate_ev(values, minimum_sample=100, minimum_ev_r=Decimal("0.10"))
    assert result.status != "VALID"
