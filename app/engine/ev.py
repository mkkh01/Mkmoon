from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, getcontext
from math import sqrt

getcontext().prec = 28


@dataclass(frozen=True)
class EVEstimate:
    status: str
    ev_r: Decimal | None
    sample_size: int
    win_probability: Decimal | None
    win_probability_lower: Decimal | None
    avg_win_r: Decimal | None
    avg_loss_r: Decimal | None
    reason: str | None = None


def _wilson_lower(successes: int, trials: int, z: Decimal = Decimal("1.96")) -> Decimal:
    if trials == 0:
        return Decimal("0")
    p = successes / trials
    zf = float(z)
    denominator = 1 + (zf * zf / trials)
    centre = p + (zf * zf / (2 * trials))
    margin = zf * sqrt((p * (1 - p) / trials) + (zf * zf / (4 * trials * trials)))
    return Decimal(str(max(0.0, (centre - margin) / denominator)))


def estimate_ev(observed_r: list[Decimal], minimum_sample: int = 100, minimum_ev_r: Decimal = Decimal("0.10")) -> EVEstimate:
    sample_size = len(observed_r)
    if sample_size < minimum_sample:
        return EVEstimate("INSUFFICIENT_DATA", None, sample_size, None, None, None, None, "MINIMUM_SAMPLE")
    wins = [value for value in observed_r if value > 0]
    losses = [value for value in observed_r if value < 0]
    breakeven = [value for value in observed_r if value == 0]
    p_win = Decimal(len(wins)) / Decimal(sample_size)
    p_loss = Decimal(len(losses)) / Decimal(sample_size)
    p_be = Decimal(len(breakeven)) / Decimal(sample_size)
    avg_win = sum(wins, Decimal("0")) / Decimal(len(wins)) if wins else Decimal("0")
    avg_loss = abs(sum(losses, Decimal("0")) / Decimal(len(losses))) if losses else Decimal("0")
    avg_be = sum(breakeven, Decimal("0")) / Decimal(len(breakeven)) if breakeven else Decimal("0")
    ev = p_win * avg_win - p_loss * avg_loss + p_be * avg_be
    lower = _wilson_lower(len(wins), sample_size)
    status = "VALID" if ev >= minimum_ev_r and lower > Decimal("0") else "NEGATIVE_OR_UNCERTAIN"
    return EVEstimate(status, ev, sample_size, p_win, lower, avg_win, avg_loss)
