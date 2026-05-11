from pathlib import Path

import pytest

from app.broker import AlpacaBroker, BrokerSafetyError, OrderRequest
from app.config import Settings
from app.database import TradingDatabase


def test_live_trading_cannot_run_by_accident(tmp_path: Path) -> None:
    settings = Settings(
        alpaca_api_key="key",
        alpaca_secret_key="secret",
        alpaca_base_url="https://api.alpaca.markets",
        trading_mode="live",
        live_trading_confirmed=False,
        database_path=tmp_path / "test.db",
    )
    database = TradingDatabase(settings.database_path)
    broker = AlpacaBroker(settings, database, trading_client=object())

    with pytest.raises(BrokerSafetyError):
        broker.place_market_order(OrderRequest(symbol="SPY", side="BUY", quantity=1, reason="test"))


def test_live_trading_requires_dashboard_gate_even_when_env_confirmed(tmp_path: Path) -> None:
    settings = Settings(
        alpaca_api_key="key",
        alpaca_secret_key="secret",
        alpaca_base_url="https://api.alpaca.markets",
        trading_mode="live",
        live_trading_confirmed=True,
        database_path=tmp_path / "test.db",
    )
    database = TradingDatabase(settings.database_path)
    broker = AlpacaBroker(settings, database, trading_client=object())

    with pytest.raises(BrokerSafetyError):
        broker.ensure_ordering_allowed()
