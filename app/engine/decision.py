from datetime import datetime, timezone
from decimal import Decimal

from app.core.hashing import decision_hash
from app.core.models import Candle, Decision, DecisionStatus, RegimeVector
from app.core.policy import load_policy
from app.core.settings import Settings
from app.engine.ev import estimate_ev
from app.engine.features import compute_features
from app.engine.regime import classify_regime
from app.engine.risk import RiskFilters, calculate_risk_plan
from app.engine.scoring import score_candidate
from app.engine.setups import evaluate_setup_with_diagnostics


def evaluate_decision(
    *,
    symbol: str,
    candles_by_timeframe: dict[str, list[Candle]],
    decision_time_ms: int,
    settings: Settings,
    filters: RiskFilters,
    equity: Decimal = Decimal("10000"),
    remaining_daily_risk: Decimal | None = None,
    remaining_portfolio_risk: Decimal | None = None,
    observed_r: list[Decimal] | None = None,
    data_source: str = "binance-spot",
) -> Decision:
    policy = load_policy(settings.config_path)
    features = compute_features(symbol, candles_by_timeframe, decision_time_ms)
    regime = classify_regime(features, policy)
    reasons: list[str] = list(features.invalid_reasons)
    risk = None
    setup = None
    entry = stop = target = None
    quality = None
    components: dict[str, Decimal] = {}
    strategy_diagnostics: list[dict] = []
    edge = policy.get("edge", {})
    minimum_sample = int(edge.get("minimum_sample", 100))
    minimum_ev_r = Decimal(str(edge.get("minimum_ev_r", "0.10")))
    ev_estimate = estimate_ev(observed_r or [], minimum_sample=minimum_sample, minimum_ev_r=minimum_ev_r)
    ev_status = ev_estimate.status
    ev_r = ev_estimate.ev_r
    ev_sample_size = ev_estimate.sample_size

    if not features.valid:
        status = DecisionStatus.UNSAFE
        reasons.append("DATA_UNSAFE")
    else:
        candidate, strategy_diagnostics = evaluate_setup_with_diagnostics(candles_by_timeframe, features, regime)
        if candidate is None:
            status = DecisionStatus.WATCH
            reasons.append("SETUP_INCOMPLETE")
        else:
            setup = candidate.setup
            entry, stop, target = candidate.entry_price, candidate.stop_price, candidate.target_price
            quality, components = score_candidate(candidate, features, regime, policy)
            if quality < Decimal(str(policy["decision"]["min_quality_score"])):
                reasons.append("QUALITY_BELOW_THRESHOLD")
            daily = remaining_daily_risk if remaining_daily_risk is not None else equity * settings.max_daily_risk_pct
            portfolio = remaining_portfolio_risk if remaining_portfolio_risk is not None else equity * settings.max_portfolio_risk_pct
            risk = calculate_risk_plan(
                equity=equity,
                risk_pct=settings.risk_pct,
                remaining_daily_risk=daily,
                remaining_portfolio_risk=portfolio,
                symbol_risk_cap=equity * settings.max_portfolio_risk_pct,
                cluster_risk_cap=equity * settings.max_portfolio_risk_pct,
                entry_price=entry,
                stop_price=stop,
                target_price=target,
                fee_rate=Decimal(str(policy["risk"]["fee_rate"])),
                slippage_rate=Decimal(str(policy["risk"]["slippage_rate"])),
                filters=filters,
            )
            reasons.extend(risk.reason_codes)
            if risk.effective_rr < Decimal(str(policy["risk"]["min_effective_rr"])):
                reasons.append("RR_TOO_LOW")
            mode = str(edge.get("mode", "disabled_until_calibrated"))
            if mode == "disabled_until_calibrated":
                reasons.append("EV_DISABLED_UNTIL_CALIBRATED")
                status = DecisionStatus.CANDIDATE if not reasons[:-1] and risk.valid else DecisionStatus.NO_TRADE
                ev_status = "DISABLED_UNTIL_CALIBRATED"
            elif ev_estimate.status != "VALID":
                reasons.append("EV_INSUFFICIENT" if ev_estimate.status == "INSUFFICIENT_DATA" else "EV_UNCERTAIN")
                status = DecisionStatus.NO_TRADE
            else:
                status = DecisionStatus.ENTER if not reasons and risk.valid else DecisionStatus.NO_TRADE

    payload = {
        "symbol": symbol,
        "decision_time_ms": decision_time_ms,
        "data_cutoff_ms": features.data_cutoff_ms,
        "status": status.value,
        "reasons": sorted(set(reasons)),
        "regime": regime.model_dump(mode="json"),
        "setup": setup.value if setup else None,
        "entry": entry,
        "stop": stop,
        "target": target,
        "quality": quality,
        "components": components,
        "strategy_diagnostics": strategy_diagnostics,
        "risk": risk.model_dump(mode="json") if risk else None,
        "ev_status": ev_status,
        "ev_r": ev_r,
        "ev_sample_size": ev_sample_size,
        "lineage": {
            "code_version": str(policy["code_version"]),
            "feature_version": str(policy["feature_version"]),
            "config_version": str(policy["version"]),
            "universe_snapshot_id": "runtime-symbol-list.v1",
            "data_source": data_source,
        },
    }
    digest = decision_hash(payload)
    return Decision(
        decision_id=f"{symbol}:{features.data_cutoff_ms}:{digest[:16]}",
        symbol=symbol,
        decision_time_ms=decision_time_ms,
        data_cutoff_ms=features.data_cutoff_ms,
        status=status,
        reason_codes=sorted(set(reasons)),
        regime=RegimeVector.model_validate(payload["regime"]),
        setup=setup,
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        quality_score=quality,
        component_scores=components,
        strategy_diagnostics=strategy_diagnostics,
        risk=risk,
        ev_status=ev_status,
        ev_r=ev_r,
        ev_sample_size=ev_sample_size,
        lineage=payload["lineage"],
        decision_hash=digest,
        created_at=datetime.fromtimestamp(decision_time_ms / 1000, timezone.utc),
    )
