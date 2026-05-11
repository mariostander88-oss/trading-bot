from __future__ import annotations

import pandas as pd


def add_indicators(data: pd.DataFrame) -> pd.DataFrame:
    required = {"close"}
    missing = required.difference(data.columns)
    if missing:
        raise ValueError(f"Market data is missing columns: {sorted(missing)}")

    frame = data.copy()
    frame["sma_20"] = frame["close"].rolling(window=20, min_periods=20).mean()
    frame["sma_50"] = frame["close"].rolling(window=50, min_periods=50).mean()
    frame["rsi"] = calculate_rsi(frame["close"], period=14)
    return frame


def calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    average_gain = gain.rolling(window=period, min_periods=period).mean()
    average_loss = loss.rolling(window=period, min_periods=period).mean()

    relative_strength = average_gain / average_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + relative_strength))
    return rsi.fillna(50)
