from pathlib import Path

from app.config import Settings
from app.database import TradingDatabase
from app.notifier import NotificationService


class FakeBroker:
    def get_account_info(self) -> dict[str, str]:
        return {"equity": "10200", "buying_power": "5000"}

    def get_current_positions(self) -> list[dict[str, str]]:
        return [{"symbol": "SPY", "qty": "1", "market_value": "500", "unrealized_pl": "12"}]


def test_status_report_includes_daily_goal_and_positions(tmp_path: Path) -> None:
    settings = Settings(
        alpaca_api_key="key",
        alpaca_secret_key="secret",
        database_path=tmp_path / "test.db",
        daily_profit_target=0.01,
    )
    database = TradingDatabase(settings.database_path)
    database.log_signal("SPY", "BUY", "test signal", close_price=100, stop_loss=98)
    database.set_status("daily_profit_pct", 0.012)
    database.set_status("daily_goal_reached", "true")

    notifier = NotificationService(settings, database, FakeBroker())  # type: ignore[arg-type]
    report = notifier.build_status_report()

    assert "Daily target reached: True" in report["body"]
    assert "Daily progress: 1.20% / 1.00%" in report["body"]
    assert "SPY: qty=1" in report["body"]
    assert "SPY BUY" in report["body"]


def test_whatsapp_recipients_supports_multiple_numbers() -> None:
    recipients = NotificationService._whatsapp_recipients("+256782900106,whatsapp:+27835576370")

    assert recipients == ["whatsapp:+256782900106", "whatsapp:+27835576370"]
