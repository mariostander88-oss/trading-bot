from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from app.indicators import add_indicators


SignalType = Literal["BUY", "SELL", "HOLD"]
VALID_SIGNALS = {"BUY", "SELL", "HOLD"}


@dataclass(frozen=True)
class StrategySignal:
    symbol: str
    signal: SignalType
    reason: str
    close_price: float | None
    stop_loss: float | None = None


class MovingAverageRsiStrategy:
    def __init__(
        self,
        stop_loss_pct: float = 0.02,
        continuation_min_rsi: float = 45,
        continuation_max_rsi: float = 68,
        max_price_extension_pct: float = 0.015,
    ) -> None:
        self.stop_loss_pct = stop_loss_pct
        self.continuation_min_rsi = continuation_min_rsi
        self.continuation_max_rsi = continuation_max_rsi
        self.max_price_extension_pct = max_price_extension_pct

    def generate_signal(self, symbol: str, market_data: pd.DataFrame) -> StrategySignal:
        if len(market_data) < 51:
            return StrategySignal(symbol, "HOLD", "Not enough bars for 20/50 SMA crossover.", None, None)

        frame = add_indicators(market_data)
        current = frame.iloc[-1]
        previous = frame.iloc[-2]

        close_price = float(current["close"])
        sma_20 = float(current["sma_20"])
        sma_50 = float(current["sma_50"])
        previous_sma_20 = float(previous["sma_20"])
        previous_sma_50 = float(previous["sma_50"])
        rsi = float(current["rsi"])

        if pd.isna(sma_20) or pd.isna(sma_50):
            return StrategySignal(symbol, "HOLD", "Waiting for SMA values to warm up.", close_price, None)

        bullish_cross = previous_sma_20 <= previous_sma_50 and sma_20 > sma_50
        bearish_cross = previous_sma_20 >= previous_sma_50 and sma_20 < sma_50
        uptrend_active = sma_20 > sma_50
        price_extension_pct = (close_price - sma_20) / sma_20 if sma_20 > 0 else 0

        if bullish_cross and rsi < 70:
            stop_loss = round(close_price * (1 - self.stop_loss_pct), 2)
            return StrategySignal(
                symbol,
                "BUY",
                f"20 SMA crossed above 50 SMA and RSI is {rsi:.1f}, below overbought filter.",
                close_price,
                stop_loss,
            )

        if (
            uptrend_active
            and self.continuation_min_rsi <= rsi <= self.continuation_max_rsi
            and 0 <= price_extension_pct <= self.max_price_extension_pct
        ):
            stop_loss = round(close_price * (1 - self.stop_loss_pct), 2)
            return StrategySignal(
                symbol,
                "BUY",
                "Trend continuation: SMA20 remains above SMA50, "
                f"RSI is {rsi:.1f}, and price is {price_extension_pct * 100:.2f}% above SMA20.",
                close_price,
                stop_loss,
            )

        if bearish_cross:
            return StrategySignal(
                symbol,
                "SELL",
                f"20 SMA crossed below 50 SMA; long-only strategy exits existing positions.",
                close_price,
                None,
            )

        return StrategySignal(
            symbol,
            "HOLD",
            "No actionable setup. "
            f"SMA20={sma_20:.2f}, SMA50={sma_50:.2f}, RSI={rsi:.1f}, "
            f"price extension={price_extension_pct * 100:.2f}%.",
            close_price,
            None,
        )
