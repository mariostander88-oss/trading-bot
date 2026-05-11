from pathlib import Path

from app.config import load_settings
from app.database import TradingDatabase


def test_database_writes_signal_logs(tmp_path: Path) -> None:
    database = TradingDatabase(tmp_path / "test.db")
    signal_id = database.log_signal(
        symbol="SPY",
        signal="HOLD",
        reason="No setup.",
        close_price=100.0,
        metadata={"test": True},
    )

    rows = database.latest_rows("signals", 5)

    assert signal_id == 1
    assert len(rows) == 1
    assert rows[0]["symbol"] == "SPY"
    assert rows[0]["signal"] == "HOLD"


def test_database_initializes_bot_status(tmp_path: Path) -> None:
    database = TradingDatabase(tmp_path / "test.db")
    assert database.get_status("emergency_stop") is False
    assert database.get_status("manual_live_trading_enabled") is False


def test_database_url_writes_use_same_database_api(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'url.db').as_posix()}"
    database = TradingDatabase(database_url=database_url)

    order_id = database.log_order(
        symbol="QQQ",
        side="BUY",
        quantity=1,
        status="blocked",
        reason="test",
    )

    rows = database.latest_rows("orders", 5)

    assert database.backend == "sqlite"
    assert order_id == 1
    assert rows[0]["symbol"] == "QQQ"
    assert rows[0]["status"] == "blocked"


def test_settings_load_database_url_without_changing_paper_defaults(tmp_path: Path, monkeypatch) -> None:
    for key in [
        "ALPACA_API_KEY",
        "ALPACA_SECRET_KEY",
        "ALPACA_BASE_URL",
        "TRADING_MODE",
        "LIVE_TRADING_CONFIRMED",
        "DATA_FEED",
        "DATABASE_URL",
        "DATABASE_PATH",
    ]:
        monkeypatch.delenv(key, raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "ALPACA_API_KEY=key",
                "ALPACA_SECRET_KEY=secret",
                "DATABASE_URL=postgresql://user:pass@example.com:5432/postgres?sslmode=require",
            ]
        ),
        encoding="utf-8",
    )

    settings = load_settings(env_file)

    assert settings.database_url.startswith("postgresql://")
    assert settings.trading_mode == "paper"
    assert settings.alpaca_base_url == "https://paper-api.alpaca.markets"
    assert settings.data_feed == "iex"
