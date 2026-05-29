import pandas as pd

import app.strategy as strategy_module
from app.strategy import MovingAverageRsiStrategy, VALID_SIGNALS


def test_strategy_returns_only_valid_signals() -> None:
    data = pd.DataFrame(
        {
            "open": [100 + idx * 0.1 for idx in range(80)],
            "high": [101 + idx * 0.1 for idx in range(80)],
            "low": [99 + idx * 0.1 for idx in range(80)],
            "close": [100 + idx * 0.1 for idx in range(80)],
            "volume": [1_000 for _ in range(80)],
        }
    )
    signal = MovingAverageRsiStrategy().generate_signal("SPY", data)
    assert signal.signal in VALID_SIGNALS
    assert signal.reason


def test_strategy_buys_valid_trend_continuation(monkeypatch) -> None:
    data = pd.DataFrame({"close": [100 for _ in range(60)]})
    frame = data.copy()
    frame["sma_20"] = [100 for _ in range(60)]
    frame["sma_50"] = [99 for _ in range(60)]
    frame["rsi"] = [55 for _ in range(60)]
    frame.loc[59, "close"] = 101

    monkeypatch.setattr(strategy_module, "add_indicators", lambda _: frame)

    signal = MovingAverageRsiStrategy().generate_signal("SPY", data)

    assert signal.signal == "BUY"
    assert "Trend continuation" in signal.reason
    assert signal.stop_loss == 98.98


def test_strategy_holds_when_trend_is_overextended(monkeypatch) -> None:
    data = pd.DataFrame({"close": [100 for _ in range(60)]})
    frame = data.copy()
    frame["sma_20"] = [100 for _ in range(60)]
    frame["sma_50"] = [99 for _ in range(60)]
    frame["rsi"] = [55 for _ in range(60)]
    frame.loc[59, "close"] = 103

    monkeypatch.setattr(strategy_module, "add_indicators", lambda _: frame)

    signal = MovingAverageRsiStrategy().generate_signal("SPY", data)

    assert signal.signal == "HOLD"
    assert "price extension=3.00%" in signal.reason
