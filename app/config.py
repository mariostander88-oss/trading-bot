from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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


def _parse_csv(value: str | None, default: str = "") -> tuple[str, ...]:
    raw = value if value is not None else default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _parse_report_times(value: str | None) -> tuple[str, ...]:
    return _parse_csv(value, default="09:00,18:00")


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
    daily_profit_target: float = 0.01
    daily_goal_blocks_new_buys: bool = True
    max_open_positions: int = 3
    stop_loss_pct: float = 0.02
    database_url: str = ""
    database_path: Path = Path("trading_bot.db")
    api_admin_token: str = ""
    notifications_enabled: bool = False
    notification_channels: tuple[str, ...] = ("email",)
    notification_times: tuple[str, ...] = ("09:00", "18:00")
    notification_timezone: str = "Africa/Nairobi"
    report_subject_prefix: str = "Trading Bot"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_to: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = ""
    twilio_whatsapp_to: str = ""

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

        if not 0 <= self.daily_profit_target <= 1:
            raise ConfigurationError("DAILY_PROFIT_TARGET must be between 0 and 1.")

        if self.max_open_positions < 1:
            raise ConfigurationError("MAX_OPEN_POSITIONS must be at least 1.")

        if not 0 < self.stop_loss_pct < 1:
            raise ConfigurationError("STOP_LOSS_PCT must be between 0 and 1.")

        if self.data_feed not in {"iex", "sip"}:
            raise ConfigurationError("DATA_FEED must be either 'iex' or 'sip'.")

        try:
            ZoneInfo(self.notification_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ConfigurationError(f"Invalid NOTIFICATION_TIMEZONE: {self.notification_timezone}") from exc

        for report_time in self.notification_times:
            hour_minute = report_time.split(":")
            if len(hour_minute) != 2:
                raise ConfigurationError("NOTIFICATION_TIMES must be comma-separated HH:MM values.")
            try:
                hour, minute = int(hour_minute[0]), int(hour_minute[1])
            except ValueError as exc:
                raise ConfigurationError("NOTIFICATION_TIMES must be comma-separated HH:MM values.") from exc
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ConfigurationError("NOTIFICATION_TIMES must use 24-hour HH:MM values.")

        valid_channels = {"email", "whatsapp"}
        unknown_channels = set(self.notification_channels) - valid_channels
        if unknown_channels:
            raise ConfigurationError("NOTIFICATION_CHANNELS may only contain email and whatsapp.")

        if self.notifications_enabled:
            if not self.notification_channels:
                raise ConfigurationError("NOTIFICATION_CHANNELS must not be empty when notifications are enabled.")
            if "email" in self.notification_channels and not (
                self.smtp_host and self.smtp_from and self.smtp_to
            ):
                raise ConfigurationError("Email notifications require SMTP_HOST, SMTP_FROM, and SMTP_TO.")
            if "whatsapp" in self.notification_channels and not (
                self.twilio_account_sid
                and self.twilio_auth_token
                and self.twilio_whatsapp_from
                and self.twilio_whatsapp_to
            ):
                raise ConfigurationError(
                    "WhatsApp notifications require Twilio SID/token and WhatsApp from/to numbers."
                )


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
        daily_profit_target=float(os.getenv("DAILY_PROFIT_TARGET", "0.01")),
        daily_goal_blocks_new_buys=_parse_bool(os.getenv("DAILY_GOAL_BLOCKS_NEW_BUYS"), default=True),
        max_open_positions=int(os.getenv("MAX_OPEN_POSITIONS", "3")),
        stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", "0.02")),
        database_url=os.getenv("DATABASE_URL", "").strip(),
        database_path=Path(os.getenv("DATABASE_PATH", "trading_bot.db")),
        api_admin_token=os.getenv("API_ADMIN_TOKEN", "").strip(),
        notifications_enabled=_parse_bool(os.getenv("NOTIFICATIONS_ENABLED"), default=False),
        notification_channels=tuple(
            channel.lower() for channel in _parse_csv(os.getenv("NOTIFICATION_CHANNELS"), default="email")
        ),
        notification_times=_parse_report_times(os.getenv("NOTIFICATION_TIMES")),
        notification_timezone=os.getenv("NOTIFICATION_TIMEZONE", "Africa/Nairobi").strip(),
        report_subject_prefix=os.getenv("REPORT_SUBJECT_PREFIX", "Trading Bot").strip(),
        smtp_host=os.getenv("SMTP_HOST", "").strip(),
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        smtp_username=os.getenv("SMTP_USERNAME", "").strip(),
        smtp_password=os.getenv("SMTP_PASSWORD", "").strip(),
        smtp_from=os.getenv("SMTP_FROM", "").strip(),
        smtp_to=os.getenv("SMTP_TO", "").strip(),
        smtp_use_tls=_parse_bool(os.getenv("SMTP_USE_TLS"), default=True),
        smtp_use_ssl=_parse_bool(os.getenv("SMTP_USE_SSL"), default=False),
        twilio_account_sid=os.getenv("TWILIO_ACCOUNT_SID", "").strip(),
        twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN", "").strip(),
        twilio_whatsapp_from=os.getenv("TWILIO_WHATSAPP_FROM", "").strip(),
        twilio_whatsapp_to=os.getenv("TWILIO_WHATSAPP_TO", "").strip(),
    )
    settings.validate()
    return settings
