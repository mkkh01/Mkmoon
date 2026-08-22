from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.core.models import Candle, Decision


@dataclass(frozen=True)
class PaperFill:
    status: str
    fill_price: Decimal | None
    exit_reason: str | None
    realized_pnl: Decimal


def simulate_long_exit(decision: Decision, candle: Candle, fee_rate: Decimal = Decimal("0.001")) -> PaperFill:
    if decision.status.value != "ENTER" or decision.entry_price is None or decision.stop_price is None or decision.target_price is None:
        return PaperFill("NOT_ACTIVE", None, None, Decimal("0"))
    entry = decision.entry_price
    stop = decision.stop_price
    target = decision.target_price
    qty = decision.risk.quantity if decision.risk else Decimal("0")
    if qty <= 0:
        return PaperFill("NOT_ACTIVE", None, None, Decimal("0"))
    if candle.low <= stop and candle.high >= target:
        # Conservative rule: for a long, assume stop is hit first when intrabar ordering is unknown.
        exit_price = stop
        reason = "STOP_AND_TARGET_SAME_CANDLE_CONSERVATIVE_STOP"
    elif candle.low <= stop:
        exit_price = stop
        reason = "STOP_LOSS"
    elif candle.high >= target:
        exit_price = target
        reason = "TAKE_PROFIT"
    else:
        return PaperFill("OPEN", None, None, Decimal("0"))
    gross = (exit_price - entry) * qty
    fees = (entry + exit_price) * qty * fee_rate
    return PaperFill("CLOSED", exit_price, reason, gross - fees)
