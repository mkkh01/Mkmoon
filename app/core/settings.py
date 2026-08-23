from decimal import Decimal
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    trading_mode: str = "paper"
    live_trading_enabled: bool = False

    binance_base_url: str = "https://api.binance.com"
    binance_data_base_url: str = "https://data-api.binance.vision"
    binance_fallback_base_url: str | None = None
    binance_ws_base_url: str = "wss://stream.binance.com:9443/ws"
    binance_api_key: str | None = None
    binance_api_secret: str | None = None
    binance_recv_window_ms: int = Field(default=5000, ge=100, le=60000)

    database_url: str | None = None
    redis_url: str | None = None
    symbols: str = "BTCUSDT,ETHUSDT,SOLUSDT"
    timezone: str = "UTC"
    candle_persistence_enabled: bool = True

    risk_pct: Decimal = Field(default=Decimal("0.0025"), gt=0, le=Decimal("0.01"))
    max_daily_risk_pct: Decimal = Field(default=Decimal("0.01"), gt=0, le=Decimal("0.10"))
    max_portfolio_risk_pct: Decimal = Field(default=Decimal("0.01"), gt=0, le=Decimal("0.10"))
    min_quality_score: Decimal = Field(default=Decimal("70"), ge=0, le=100)
    min_effective_rr: Decimal = Field(default=Decimal("1.5"), gt=0)
    min_atr_percentile: Decimal = Field(default=Decimal("5"), ge=0, le=100)
    max_atr_percentile: Decimal = Field(default=Decimal("95"), ge=0, le=100)
    poll_interval_seconds: int = Field(default=60, ge=5)
    config_path: str = "configs/config.v1.yaml"

    @field_validator("trading_mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        value = value.lower().strip()
        if value not in {"paper", "shadow", "live"}:
            raise ValueError("trading_mode must be paper, shadow, or live")
        return value

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, value: str) -> str:
        symbols = [s.strip().upper() for s in value.split(",") if s.strip()]
        if not symbols:
            raise ValueError("at least one symbol is required")
        if any(not s.isalnum() for s in symbols):
            raise ValueError("symbols must be alphanumeric Binance symbols")
        return ",".join(dict.fromkeys(symbols))

    def symbol_list(self) -> list[str]:
        return self.symbols.split(",")

    def assert_safe_mode(self) -> None:
        if self.trading_mode == "live" and not self.live_trading_enabled:
            raise RuntimeError("live mode requires LIVE_TRADING_ENABLED=true")
        if self.trading_mode == "live" and (not self.binance_api_key or not self.binance_api_secret):
            raise RuntimeError("live mode requires Binance credentials")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.assert_safe_mode()
    return settings
