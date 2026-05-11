from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from app.strategy import MovingAverageRsiStrategy


def run_backtest(input_csv: str | Path, output_csv: str | Path, symbol: str = "SPY") -> dict[str, float | int]:
    data = pd.read_csv(input_csv)
    if "timestamp" in data.columns:
        data["timestamp"] = pd.to_datetime(data["timestamp"])
        data = data.set_index("timestamp")

    strategy = MovingAverageRsiStrategy()
    cash = 10_000.0
    position = 0.0
    entry_price = 0.0
    equity_curve: list[float] = []
    events: list[dict[str, object]] = []
    wins = 0
    completed_trades = 0

    for idx in range(51, len(data)):
        window = data.iloc[: idx + 1]
        signal = strategy.generate_signal(symbol, window)
        price = float(window.iloc[-1]["close"])

        if signal.signal == "BUY" and position == 0:
            position = cash / price
            entry_price = price
            cash = 0.0
            events.append({**asdict(signal), "timestamp": str(window.index[-1]), "action_price": price})
        elif signal.signal == "SELL" and position > 0:
            cash = position * price
            pnl = price - entry_price
            wins += int(pnl > 0)
            completed_trades += 1
            position = 0.0
            entry_price = 0.0
            events.append({**asdict(signal), "timestamp": str(window.index[-1]), "action_price": price, "pnl": pnl})
        else:
            events.append({**asdict(signal), "timestamp": str(window.index[-1]), "action_price": price})

        equity_curve.append(cash + position * price)

    final_price = float(data.iloc[-1]["close"])
    final_equity = cash + position * final_price
    total_return = (final_equity - 10_000.0) / 10_000.0
    equity = pd.Series(equity_curve) if equity_curve else pd.Series([10_000.0])
    drawdown = equity / equity.cummax() - 1

    results = {
        "total_return": round(total_return, 6),
        "win_rate": round(wins / completed_trades, 6) if completed_trades else 0,
        "max_drawdown": round(float(drawdown.min()), 6),
        "number_of_trades": completed_trades,
    }
    pd.DataFrame(events).to_csv(output_csv, index=False)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a moving-average RSI backtest on OHLCV CSV data.")
    parser.add_argument("input_csv", help="CSV with at least open, high, low, close, volume columns.")
    parser.add_argument("--output", default="backtest_results.csv", help="Where to write signal/trade events.")
    parser.add_argument("--symbol", default="SPY", help="Symbol label for the report.")
    args = parser.parse_args()
    print(run_backtest(args.input_csv, args.output, args.symbol))


if __name__ == "__main__":
    main()
