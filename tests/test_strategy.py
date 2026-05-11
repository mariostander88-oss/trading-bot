import pandas as pd

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
