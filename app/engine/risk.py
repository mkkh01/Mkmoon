from decimal import Decimal, ROUND_DOWN

from app.core.models import RiskPlan


class RiskFilters:
    def __init__(self, min_qty: Decimal, max_qty: Decimal, step_size: Decimal, min_notional: Decimal = Decimal("0"), max_notional: Decimal | None = None, tick_size: Decimal = Decimal("0")):
        self.min_qty = min_qty
        self.max_qty = max_qty
        self.step_size = step_size
        self.min_notional = min_notional
        self.max_notional = max_notional
        self.tick_size = tick_size


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    units = (value / step).to_integral_value(rounding=ROUND_DOWN)
    return units * step


def normalize_price(value: Decimal, tick_size: Decimal, direction: str = "down") -> Decimal:
    if tick_size <= 0:
        return value
    units = (value / tick_size).to_integral_value(rounding=ROUND_DOWN if direction == "down" else ROUND_DOWN)
    return units * tick_size


def calculate_risk_plan(
    *,
    equity: Decimal,
    risk_pct: Decimal,
    remaining_daily_risk: Decimal,
    remaining_portfolio_risk: Decimal,
    symbol_risk_cap: Decimal,
    cluster_risk_cap: Decimal,
    entry_price: Decimal,
    stop_price: Decimal,
    target_price: Decimal,
    fee_rate: Decimal,
    slippage_rate: Decimal,
    filters: RiskFilters,
) -> RiskPlan:
    if equity <= 0 or entry_price <= 0 or stop_price <= 0 or target_price <= entry_price:
        return RiskPlan(
            risk_cash=Decimal("0"), risk_pct=risk_pct, entry_price=entry_price, stop_price=stop_price,
            target_price=target_price, unit_risk=Decimal("1"), quantity=Decimal("0"),
            effective_risk_cash=Decimal("0"), expected_cost_cash=Decimal("0"), effective_rr=Decimal("0"),
            valid=False, reason_codes=["INVALID_PRICE_GEOMETRY"],
        )
    risk_cash = min(equity * risk_pct, remaining_daily_risk, remaining_portfolio_risk, symbol_risk_cap, cluster_risk_cap)
    if risk_cash <= 0:
        return RiskPlan(
            risk_cash=Decimal("0"), risk_pct=risk_pct, entry_price=entry_price, stop_price=stop_price,
            target_price=target_price, unit_risk=Decimal("1"), quantity=Decimal("0"),
            effective_risk_cash=Decimal("0"), expected_cost_cash=Decimal("0"), effective_rr=Decimal("0"),
            valid=False, reason_codes=["RISK_LIMIT"],
        )
    price_risk = entry_price - stop_price
    cost_per_unit = entry_price * (fee_rate * Decimal("2") + slippage_rate * Decimal("2"))
    unit_risk = price_risk + cost_per_unit
    raw_qty = risk_cash / unit_risk
    quantity = floor_to_step(raw_qty, filters.step_size)
    reasons: list[str] = []
    if quantity < filters.min_qty:
        reasons.append("MIN_QTY")
    if quantity > filters.max_qty:
        quantity = filters.max_qty
        quantity = floor_to_step(quantity, filters.step_size)
    notional = quantity * entry_price
    if notional < filters.min_notional:
        reasons.append("MIN_NOTIONAL")
    if filters.max_notional is not None and notional > filters.max_notional:
        reasons.append("MAX_NOTIONAL")
    effective_risk = quantity * unit_risk
    expected_cost = quantity * cost_per_unit
    reward = quantity * (target_price - entry_price)
    rr = reward / effective_risk if effective_risk > 0 else Decimal("0")
    if effective_risk > risk_cash:
        reasons.append("RISK_PRECISION")
    return RiskPlan(
        risk_cash=risk_cash, risk_pct=risk_pct, entry_price=entry_price, stop_price=stop_price,
        target_price=target_price, unit_risk=unit_risk, quantity=quantity,
        effective_risk_cash=effective_risk, expected_cost_cash=expected_cost,
        effective_rr=rr, valid=not reasons and quantity > 0, reason_codes=reasons,
    )
