from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from app.indicators import add_indicators


SignalType = Literal["BUY", "SELL", "HOLD"]
VALID_SIGNALS = {"BUY", "SELL", "HOLD"}
SHORT_SIGNAL = "SHORT"   # open a new short position
COVER_SIGNAL = "COVER"   # close an existing short position


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
        continuation_min_rsi: float = 35,
        continuation_max_rsi: float = 75,
        max_price_extension_pct: float = 0.03,
        take_profit_pct: float = 0.06,
    ) -> None:
        self.stop_loss_pct = stop_loss_pct
        self.continuation_min_rsi = continuation_min_rsi
        self.continuation_max_rsi = continuation_max_rsi
        self.max_price_extension_pct = max_price_extension_pct
        self.take_profit_pct = take_profit_pct

    def check_position_exit(
        self,
        symbol: str,
        avg_entry_price: float,
        position_qty: float,
        current_price: float,
    ) -> StrategySignal | None:
        """Check if an existing position should be exited due to stop loss or take profit.
        Returns a SELL/COVER signal if triggered, otherwise None.
        """
        if avg_entry_price <= 0:
            return None

        if position_qty > 0:  # Long position
            stop_price = avg_entry_price * (1 - self.stop_loss_pct)
            tp_price = avg_entry_price * (1 + self.take_profit_pct)
            if current_price <= stop_price:
                return StrategySignal(
                    symbol, "SELL",
                    f"Stop loss triggered: ${current_price:.2f} <= ${stop_price:.2f} (entry ${avg_entry_price:.2f})",
                    current_price, None,
                )
            if current_price >= tp_price:
                return StrategySignal(
                    symbol, "SELL",
                    f"Take profit hit: ${current_price:.2f} >= ${tp_price:.2f} ({self.take_profit_pct*100:.0f}% gain)",
                    current_price, None,
                )

        elif position_qty < 0:  # Short position
            stop_price = avg_entry_price * (1 + self.stop_loss_pct)
            tp_price = avg_entry_price * (1 - self.take_profit_pct)
            if current_price >= stop_price:
                return StrategySignal(
                    symbol, COVER_SIGNAL,
                    f"Short stop loss: ${current_price:.2f} >= ${stop_price:.2f} (short entry ${avg_entry_price:.2f})",
                    current_price, None,
                )
            if current_price <= tp_price:
                return StrategySignal(
                    symbol, COVER_SIGNAL,
                    f"Short take profit: ${current_price:.2f} <= ${tp_price:.2f} ({self.take_profit_pct*100:.0f}% gain)",
                    current_price, None,
                )

        return None

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

        if bullish_cross and rsi < 80:
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
                f"20 SMA crossed below 50 SMA; exits long / triggers short entry.",
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


class StrategyB(MovingAverageRsiStrategy):
    """Faster SMA 10/20 crossover with wider RSI range — generates more signals than A.

    Used in the A/B Arena. When both A and B agree, position size is boosted.
    When they disagree, size is reduced (lower conviction).
    """

    def __init__(self, stop_loss_pct: float = 0.03, take_profit_pct: float = 0.06) -> None:
        super().__init__(
            stop_loss_pct=stop_loss_pct,
            continuation_min_rsi=30,
            continuation_max_rsi=80,
            max_price_extension_pct=0.05,
            take_profit_pct=take_profit_pct,
        )

    def generate_signal(self, symbol: str, market_data: pd.DataFrame) -> StrategySignal:
        if len(market_data) < 21:
            return StrategySignal(symbol, "HOLD", "[B] Not enough bars for SMA 10/20.", None, None)

        close = market_data["close"]
        sma_10 = close.rolling(10).mean()
        sma_20 = close.rolling(20).mean()

        current_close = float(close.iloc[-1])
        s10 = float(sma_10.iloc[-1])
        s20 = float(sma_20.iloc[-1])
        p10 = float(sma_10.iloc[-2])
        p20 = float(sma_20.iloc[-2])

        if pd.isna(s10) or pd.isna(s20):
            return StrategySignal(symbol, "HOLD", "[B] Warming up SMA 10/20.", current_close, None)

        rsi = float(add_indicators(market_data)["rsi"].iloc[-1])
        price_ext = (current_close - s10) / s10 if s10 > 0 else 0

        bullish_cross = p10 <= p20 and s10 > s20
        bearish_cross = p10 >= p20 and s10 < s20
        uptrend = s10 > s20

        if bullish_cross and rsi < 80:
            stop = round(current_close * (1 - self.stop_loss_pct), 2)
            return StrategySignal(
                symbol, "BUY",
                f"[B] SMA10 crossed above SMA20, RSI={rsi:.1f}",
                current_close, stop,
            )

        if uptrend and self.continuation_min_rsi <= rsi <= self.continuation_max_rsi and 0 <= price_ext <= self.max_price_extension_pct:
            stop = round(current_close * (1 - self.stop_loss_pct), 2)
            return StrategySignal(
                symbol, "BUY",
                f"[B] SMA10>SMA20 continuation, RSI={rsi:.1f}, ext={price_ext*100:.1f}%",
                current_close, stop,
            )

        if bearish_cross:
            return StrategySignal(
                symbol, "SELL",
                f"[B] SMA10 crossed below SMA20.",
                current_close, None,
            )

        return StrategySignal(
            symbol, "HOLD",
            f"[B] No signal. SMA10={s10:.2f} SMA20={s20:.2f} RSI={rsi:.1f}",
            current_close, None,
        )
