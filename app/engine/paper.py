from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.core.models import Candle, Decision


@dataclass(frozen=True)
class PaperEntry:
    fill_price: Decimal
    quantity: Decimal
    fee: Decimal
    entry_time_ms: int


@dataclass(frozen=True)
class PaperFill:
    status: str
    fill_price: Decimal | None
    exit_reason: str | None
    realized_pnl: Decimal
    fee: Decimal = Decimal("0")


def simulate_long_entry(
    decision: Decision,
    *,
    fee_rate: Decimal = Decimal("0.001"),
    slippage_rate: Decimal = Decimal("0.0005"),
) -> PaperEntry | None:
    """Fill a Paper LONG entry conservatively at an adverse slippage price."""
    if (
        decision.status.value != "ENTER"
        or decision.entry_price is None
        or decision.risk is None
        or decision.risk.quantity <= 0
        or slippage_rate < 0
        or fee_rate < 0
    ):
        return None
    fill_price = decision.entry_price * (Decimal("1") + slippage_rate)
    if decision.stop_price is None or decision.target_price is None:
        return None
    if not decision.stop_price < fill_price < decision.target_price:
        return None
    quantity = decision.risk.quantity
    actual_unit_risk = (fill_price - decision.stop_price) + fill_price * (fee_rate * Decimal("2") + slippage_rate * Decimal("2"))
    if actual_unit_risk * quantity > decision.risk.risk_cash:
        return None
    fee = fill_price * quantity * fee_rate
    return PaperEntry(
        fill_price=fill_price,
        quantity=quantity,
        fee=fee,
        entry_time_ms=decision.decision_time_ms,
    )


def simulate_long_exit_levels(
    *,
    entry_price: Decimal,
    stop_price: Decimal,
    target_price: Decimal,
    quantity: Decimal,
    candle: Candle,
    fee_rate: Decimal = Decimal("0.001"),
) -> PaperFill:
    if quantity <= 0 or entry_price <= 0 or stop_price <= 0 or target_price <= entry_price:
        return PaperFill("NOT_ACTIVE", None, None, Decimal("0"))
    if candle.low <= stop_price and candle.high >= target_price:
        # Conservative rule: for a long, assume stop is hit first when intrabar ordering is unknown.
        exit_price = stop_price
        reason = "STOP_AND_TARGET_SAME_CANDLE_CONSERVATIVE_STOP"
    elif candle.low <= stop_price:
        exit_price = stop_price
        reason = "STOP_LOSS"
    elif candle.high >= target_price:
        exit_price = target_price
        reason = "TAKE_PROFIT"
    else:
        return PaperFill("OPEN", None, None, Decimal("0"))
    fee = exit_price * quantity * fee_rate
    gross = (exit_price - entry_price) * quantity
    return PaperFill("CLOSED", exit_price, reason, gross - fee, fee)


def simulate_long_exit(decision: Decision, candle: Candle, fee_rate: Decimal = Decimal("0.001")) -> PaperFill:
    if decision.status.value != "ENTER" or decision.entry_price is None or decision.stop_price is None or decision.target_price is None:
        return PaperFill("NOT_ACTIVE", None, None, Decimal("0"))
    qty = decision.risk.quantity if decision.risk else Decimal("0")
    return simulate_long_exit_levels(
        entry_price=decision.entry_price,
        stop_price=decision.stop_price,
        target_price=decision.target_price,
        quantity=qty,
        candle=candle,
        fee_rate=fee_rate,
    )
