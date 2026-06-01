from pathlib import Path

from app.config import Settings
from app.database import TradingDatabase
from app.risk_manager import RiskManager


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        alpaca_api_key="key",
        alpaca_secret_key="secret",
        database_path=tmp_path / "test.db",
        max_risk_per_trade=0.01,
        max_daily_loss=0.03,
        max_open_positions=1,
    )


def test_risk_manager_blocks_oversized_trade(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    database = TradingDatabase(settings.database_path)
    manager = RiskManager(settings, database)

    result = manager.check_trade(
        side="BUY",
        equity=10_000,
        entry_price=100,
        stop_loss=90,
        open_positions_count=0,
        has_existing_position=False,
        requested_quantity=20,
    )

    assert not result.allowed
    assert "exceeds max risk" in result.reason


def test_stop_loss_is_required(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    database = TradingDatabase(settings.database_path)
    manager = RiskManager(settings, database)

    result = manager.check_trade(
        side="BUY",
        equity=10_000,
        entry_price=100,
        stop_loss=None,
        open_positions_count=0,
        has_existing_position=False,
    )

    assert not result.allowed
    assert "stop loss is required" in result.reason


def test_daily_profit_target_blocks_new_buys(tmp_path: Path) -> None:
    settings = Settings(
        alpaca_api_key="key",
        alpaca_secret_key="secret",
        database_path=tmp_path / "test.db",
        daily_profit_target=0.01,
        daily_goal_blocks_new_buys=True,
    )
    database = TradingDatabase(settings.database_path)
    database.set_daily_start_equity_if_needed(10_000)
    manager = RiskManager(settings, database)

    result = manager.check_trade(
        side="BUY",
        equity=10_200,
        entry_price=100,
        stop_loss=98,
        open_positions_count=0,
        has_existing_position=False,
    )

    assert not result.allowed
    assert "daily profit target" in result.reason
    assert database.get_status("daily_goal_reached") is True


def test_existing_long_position_blocks_additional_buy(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    database = TradingDatabase(settings.database_path)
    manager = RiskManager(settings, database)

    result = manager.check_trade(
        side="BUY",
        equity=10_000,
        entry_price=100,
        stop_loss=98,
        open_positions_count=1,
        has_existing_position=True,
        buying_power=5_000,
    )

    assert not result.allowed
    assert "existing long position" in result.reason


def test_buy_quantity_is_capped_to_buying_power(tmp_path: Path) -> None:
    settings = Settings(
        alpaca_api_key="key",
        alpaca_secret_key="secret",
        database_path=tmp_path / "test.db",
        max_risk_per_trade=0.01,
        max_daily_loss=0.03,
        max_open_positions=3,
    )
    database = TradingDatabase(settings.database_path)
    manager = RiskManager(settings, database)

    result = manager.check_trade(
        side="BUY",
        equity=100_000,
        entry_price=750,
        stop_loss=735,
        open_positions_count=0,
        has_existing_position=False,
        buying_power=49_000,
    )

    assert result.allowed
    assert result.quantity == 64
    assert "buying power" in result.reason
