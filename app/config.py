from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigurationError(RuntimeError):
    """Raised when configuration is missing or unsafe."""


def _parse_bool(value: str | bool | None, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_watchlist(value: str | None) -> list[str]:
    raw = value or "SPY,QQQ"
    symbols = [symbol.strip().upper() for symbol in raw.split(",") if symbol.strip()]
    return symbols or ["SPY", "QQQ"]


@dataclass(frozen=True)
class Settings:
    alpaca_api_key: str
    alpaca_secret_key: str
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    trading_mode: str = "paper"
    live_trading_confirmed: bool = False
    watchlist: tuple[str, ...] = ("SPY", "QQQ")
    timeframe_minutes: int = 15
    historical_bars_limit: int = 120
    data_feed: str = "iex"
    max_risk_per_trade: float = 0.01
    max_daily_loss: float = 0.03
    max_open_positions: int = 3
    stop_loss_pct: float = 0.02
    database_url: str = ""
    database_path: Path = Path("trading_bot.db")
    api_admin_token: str = ""

    @property
    def is_paper(self) -> bool:
        return self.trading_mode == "paper"

    @property
    def is_live(self) -> bool:
        return self.trading_mode == "live"

    def live_trading_allowed(self, manual_live_trading_enabled: bool) -> bool:
        return self.is_live and self.live_trading_confirmed and manual_live_trading_enabled

    def validate(self) -> None:
        if self.trading_mode not in {"paper", "live"}:
            raise ConfigurationError("TRADING_MODE must be either 'paper' or 'live'.")

        if not self.alpaca_api_key or not self.alpaca_secret_key:
            raise ConfigurationError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in .env.")

        if "your_" in self.alpaca_api_key.lower() or "your_" in self.alpaca_secret_key.lower():
            raise ConfigurationError("Replace placeholder Alpaca credentials in .env before running the bot.")

        if self.is_paper and "paper-api.alpaca.markets" not in self.alpaca_base_url:
            raise ConfigurationError("Paper mode must use the Alpaca paper base URL.")

        if self.is_live and "paper-api.alpaca.markets" in self.alpaca_base_url:
            raise ConfigurationError("Live mode cannot use the paper base URL.")

        if not 0 < self.max_risk_per_trade <= 1:
            raise ConfigurationError("MAX_RISK_PER_TRADE must be between 0 and 1.")

        if not 0 < self.max_daily_loss <= 1:
            raise ConfigurationError("MAX_DAILY_LOSS must be between 0 and 1.")

        if self.max_open_positions < 1:
            raise ConfigurationError("MAX_OPEN_POSITIONS must be at least 1.")

        if not 0 < self.stop_loss_pct < 1:
            raise ConfigurationError("STOP_LOSS_PCT must be between 0 and 1.")

        if self.data_feed not in {"iex", "sip"}:
            raise ConfigurationError("DATA_FEED must be either 'iex' or 'sip'.")


def load_settings(env_file: str | os.PathLike[str] = ".env") -> Settings:
    load_dotenv(env_file)

    settings = Settings(
        alpaca_api_key=os.getenv("ALPACA_API_KEY", "").strip(),
        alpaca_secret_key=os.getenv("ALPACA_SECRET_KEY", "").strip(),
        alpaca_base_url=os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets").strip(),
        trading_mode=os.getenv("TRADING_MODE", "paper").strip().lower(),
        live_trading_confirmed=_parse_bool(os.getenv("LIVE_TRADING_CONFIRMED"), default=False),
        watchlist=tuple(_parse_watchlist(os.getenv("WATCHLIST"))),
        timeframe_minutes=int(os.getenv("TIMEFRAME_MINUTES", "15")),
        historical_bars_limit=int(os.getenv("HISTORICAL_BARS_LIMIT", "120")),
        data_feed=os.getenv("DATA_FEED", "iex").strip().lower(),
        max_risk_per_trade=float(os.getenv("MAX_RISK_PER_TRADE", "0.01")),
        max_daily_loss=float(os.getenv("MAX_DAILY_LOSS", "0.03")),
        max_open_positions=int(os.getenv("MAX_OPEN_POSITIONS", "3")),
        stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", "0.02")),
        database_url=os.getenv("DATABASE_URL", "").strip(),
        database_path=Path(os.getenv("DATABASE_PATH", "trading_bot.db")),
        api_admin_token=os.getenv("API_ADMIN_TOKEN", "").strip(),
    )
    settings.validate()
    return settings
