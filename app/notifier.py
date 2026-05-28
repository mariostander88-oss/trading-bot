from __future__ import annotations

import base64
import logging
import smtplib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from typing import Any
from zoneinfo import ZoneInfo

from app.broker import AlpacaBroker
from app.config import Settings
from app.database import TradingDatabase, utc_now


logger = logging.getLogger(__name__)


class NotificationError(RuntimeError):
    """Raised when a configured notification provider fails."""


class NotificationService:
    def __init__(self, settings: Settings, database: TradingDatabase, broker: AlpacaBroker) -> None:
        self.settings = settings
        self.database = database
        self.broker = broker

    def send_status_report(self, force: bool = False) -> dict[str, object]:
        if not self.settings.notifications_enabled and not force:
            return {"status": "skipped", "reason": "Notifications are disabled."}

        report = self.build_status_report()
        delivered: list[str] = []
        errors: list[dict[str, str]] = []

        for channel in self.settings.notification_channels:
            try:
                if channel == "email":
                    self._send_email(report["subject"], report["body"])
                elif channel == "whatsapp":
                    self._send_whatsapp(report["whatsapp_body"])
                delivered.append(channel)
            except Exception as exc:
                logger.exception("Failed to send %s notification", channel)
                errors.append({"channel": channel, "error": str(exc)})

        status = "sent" if delivered and not errors else "partial" if delivered else "failed"
        self.database.set_status("last_notification_at", utc_now())
        self.database.set_status(
            "last_notification_status",
            {"status": status, "delivered": delivered, "errors": errors},
        )
        if errors and not delivered:
            raise NotificationError(f"All notification channels failed: {errors}")
        return {"status": status, "delivered": delivered, "errors": errors}

    def build_status_report(self) -> dict[str, str]:
        local_now = datetime.now(ZoneInfo(self.settings.notification_timezone))
        status = self.database.status_snapshot()
        latest_signals = self.database.latest_rows("signals", 5)
        latest_trades = self.database.latest_rows("trades", 5)
        latest_errors = self.database.latest_rows("errors", 3)

        account, account_error = self._try_call(self.broker.get_account_info)
        positions, positions_error = self._try_call(self.broker.get_current_positions)
        position_rows = positions if isinstance(positions, list) else []

        equity = self._first_value(account, "equity", "portfolio_value")
        buying_power = self._first_value(account, "buying_power", "cash")
        daily_profit_pct = self.database.get_status("daily_profit_pct", 0) or 0
        daily_goal_reached = bool(self.database.get_status("daily_goal_reached", False))

        subject = f"{self.settings.report_subject_prefix} report - {local_now:%Y-%m-%d %H:%M}"
        lines = [
            subject,
            "",
            f"Mode: {self.settings.trading_mode.upper()}",
            f"Watchlist: {', '.join(self.settings.watchlist)}",
            f"Last cycle: {self._status_value(status, 'last_cycle_status')} at {self._status_value(status, 'last_cycle_at')}",
            f"Emergency stop: {self.database.get_status('emergency_stop', False)}",
            f"Daily target reached: {daily_goal_reached}",
            f"Daily progress: {float(daily_profit_pct) * 100:.2f}% / {self.settings.daily_profit_target * 100:.2f}%",
            f"Daily realized P&L: {self.database.daily_realized_pnl():.2f}",
        ]

        if account_error:
            lines.append(f"Account: unavailable ({account_error})")
        else:
            lines.extend(
                [
                    f"Equity: {equity}",
                    f"Buying power: {buying_power}",
                ]
            )

        if positions_error:
            lines.append(f"Positions: unavailable ({positions_error})")
        elif position_rows:
            lines.append("")
            lines.append("Open positions:")
            for position in position_rows[:10]:
                symbol = position.get("symbol", "?")
                qty = position.get("qty", "?")
                market_value = position.get("market_value", "?")
                unrealized = position.get("unrealized_pl", "?")
                lines.append(f"- {symbol}: qty={qty}, value={market_value}, unrealized={unrealized}")
        else:
            lines.append("Open positions: none")

        lines.append("")
        lines.append("Latest signals:")
        lines.extend(self._format_signal_rows(latest_signals))

        lines.append("")
        lines.append("Latest trades:")
        lines.extend(self._format_trade_rows(latest_trades))

        if latest_errors:
            lines.append("")
            lines.append("Latest errors:")
            for error in latest_errors:
                lines.append(f"- {error.get('created_at')}: {error.get('component')} - {error.get('message')}")

        body = "\n".join(lines)
        return {
            "subject": subject,
            "body": body,
            "whatsapp_body": self._truncate(body, 1500),
        }

    def _send_email(self, subject: str, body: str) -> None:
        if not (self.settings.smtp_host and self.settings.smtp_from and self.settings.smtp_to):
            raise NotificationError("Email is not fully configured.")

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.settings.smtp_from
        message["To"] = self.settings.smtp_to
        message.set_content(body)

        smtp_class = smtplib.SMTP_SSL if self.settings.smtp_use_ssl else smtplib.SMTP
        with smtp_class(self.settings.smtp_host, self.settings.smtp_port, timeout=30) as smtp:
            if self.settings.smtp_use_tls and not self.settings.smtp_use_ssl:
                smtp.starttls()
            if self.settings.smtp_username or self.settings.smtp_password:
                smtp.login(self.settings.smtp_username, self.settings.smtp_password)
            recipients = [item.strip() for item in self.settings.smtp_to.split(",") if item.strip()]
            smtp.send_message(message, to_addrs=recipients)

    def _send_whatsapp(self, body: str) -> None:
        required = [
            self.settings.twilio_account_sid,
            self.settings.twilio_auth_token,
            self.settings.twilio_whatsapp_from,
            self.settings.twilio_whatsapp_to,
        ]
        if not all(required):
            raise NotificationError("WhatsApp is not fully configured.")

        account_sid = self.settings.twilio_account_sid
        url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
        token = base64.b64encode(f"{account_sid}:{self.settings.twilio_auth_token}".encode()).decode()
        for recipient in self._whatsapp_recipients(self.settings.twilio_whatsapp_to):
            payload = urllib.parse.urlencode(
                {
                    "From": self._whatsapp_address(self.settings.twilio_whatsapp_from),
                    "To": recipient,
                    "Body": body,
                }
            ).encode()
            request = urllib.request.Request(
                url,
                data=payload,
                headers={
                    "Authorization": f"Basic {token}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    if response.status >= 300:
                        raise NotificationError(f"Twilio returned HTTP {response.status}.")
            except urllib.error.HTTPError as exc:
                details = exc.read().decode(errors="replace")
                raise NotificationError(f"Twilio returned HTTP {exc.code}: {details}") from exc

    @staticmethod
    def _try_call(func: Any) -> tuple[Any, str | None]:
        try:
            return func(), None
        except Exception as exc:
            return None, str(exc)

    @staticmethod
    def _first_value(data: Any, *keys: str) -> Any:
        if not isinstance(data, dict):
            return "unknown"
        for key in keys:
            value = data.get(key)
            if value not in {None, ""}:
                return value
        return "unknown"

    @staticmethod
    def _status_value(status: dict[str, Any], key: str) -> Any:
        value = status.get(key, {})
        return value.get("value", "") if isinstance(value, dict) else value

    @staticmethod
    def _format_signal_rows(rows: list[dict[str, Any]]) -> list[str]:
        if not rows:
            return ["- none yet"]
        return [
            f"- {row.get('created_at')}: {row.get('symbol')} {row.get('signal')} - {row.get('reason')}"
            for row in rows
        ]

    @staticmethod
    def _format_trade_rows(rows: list[dict[str, Any]]) -> list[str]:
        if not rows:
            return ["- none yet"]
        return [
            f"- {row.get('created_at')}: {row.get('symbol')} {row.get('side')} "
            f"qty={row.get('quantity')} price={row.get('price')}"
            for row in rows
        ]

    @staticmethod
    def _truncate(value: str, max_length: int) -> str:
        if len(value) <= max_length:
            return value
        return value[: max_length - 20].rstrip() + "\n...truncated..."

    @staticmethod
    def _whatsapp_address(value: str) -> str:
        stripped = value.strip()
        return stripped if stripped.startswith("whatsapp:") else f"whatsapp:{stripped}"

    @classmethod
    def _whatsapp_recipients(cls, value: str) -> list[str]:
        recipients = [cls._whatsapp_address(item) for item in value.split(",") if item.strip()]
        if not recipients:
            raise NotificationError("At least one WhatsApp recipient is required.")
        return recipients
