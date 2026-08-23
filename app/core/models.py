from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DecisionStatus(StrEnum):
    NO_TRADE = "NO_TRADE"
    WATCH = "WATCH"
    CANDIDATE = "CANDIDATE"
    ENTER = "ENTER"
    UNSAFE = "UNSAFE"


class SetupType(StrEnum):
    TREND_PULLBACK = "TREND_PULLBACK"
    BREAKOUT_RETEST = "BREAKOUT_RETEST"
    SWEEP_REVERSAL = "LIQUIDITY_SWEEP_REVERSAL"
    RANGE_REVERSION = "RANGE_EDGE_REVERSION"


class Candle(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    timeframe: str
    open_time_ms: int = Field(ge=0)
    close_time_ms: int = Field(ge=0)
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: Decimal = Field(ge=0)
    is_closed: bool
    source_id: str = "binance"

    def validate_ohlc(self) -> None:
        if self.high < max(self.open, self.close):
            raise ValueError("high is below open/close")
        if self.low > min(self.open, self.close):
            raise ValueError("low is above open/close")
        if self.close_time_ms < self.open_time_ms:
            raise ValueError("close time precedes open time")
        if not self.is_closed:
            raise ValueError("strategy decisions require closed candles")


class FeatureSnapshot(BaseModel):
    symbol: str
    decision_time_ms: int
    data_cutoff_ms: int
    feature_version: str = "features.v1"
    values: dict[str, Decimal | None]
    valid: bool = True
    invalid_reasons: list[str] = Field(default_factory=list)


class RegimeVector(BaseModel):
    trend_direction: str
    trend_strength: str
    volatility_phase: str
    structure_mode: str
    market_safety: str
    liquidity_state: str
    reversal_status: str
    available_time_ms: int
    version: str = "regime.v1"


class RiskPlan(BaseModel):
    risk_cash: Decimal = Field(ge=0)
    risk_pct: Decimal = Field(ge=0)
    entry_price: Decimal = Field(gt=0)
    stop_price: Decimal = Field(gt=0)
    target_price: Decimal = Field(gt=0)
    unit_risk: Decimal = Field(gt=0)
    quantity: Decimal = Field(ge=0)
    effective_risk_cash: Decimal = Field(ge=0)
    expected_cost_cash: Decimal = Field(ge=0)
    effective_rr: Decimal = Field(ge=0)
    valid: bool
    reason_codes: list[str] = Field(default_factory=list)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str
    symbol: str
    decision_time_ms: int
    data_cutoff_ms: int
    status: DecisionStatus
    reason_codes: list[str] = Field(default_factory=list)
    invalidation_codes: list[str] = Field(default_factory=list)
    regime: RegimeVector | None = None
    setup: SetupType | None = None
    entry_price: Decimal | None = None
    stop_price: Decimal | None = None
    target_price: Decimal | None = None
    quality_score: Decimal | None = None
    component_scores: dict[str, Decimal] = Field(default_factory=dict)
    strategy_diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    risk: RiskPlan | None = None
    ev_status: str = "INSUFFICIENT_DATA"
    ev_r: Decimal | None = None
    ev_sample_size: int = 0
    lineage: dict[str, str] = Field(default_factory=dict)
    decision_hash: str | None = None
    created_at: datetime


class ExchangeSymbolFilters(BaseModel):
    symbol: str
    status: str
    base_asset: str
    quote_asset: str
    base_asset_precision: int
    quote_asset_precision: int
    filters: dict[str, dict[str, Any]]
    snapshot_time_ms: int
    exchange_info_version: str
