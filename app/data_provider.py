from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from app.config import Settings


class AlpacaDataProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from alpaca.data.historical import StockHistoricalDataClient

            self._client = StockHistoricalDataClient(
                api_key=self.settings.alpaca_api_key,
                secret_key=self.settings.alpaca_secret_key,
            )
        return self._client

    def fetch_latest_bars(self, symbol: str, limit: int | None = None) -> pd.DataFrame:
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        bar_limit = limit or self.settings.historical_bars_limit
        end = datetime.now(UTC)
        start = end - timedelta(days=10)
        feed = DataFeed.IEX if self.settings.data_feed == "iex" else DataFeed.SIP
        request = StockBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=TimeFrame(self.settings.timeframe_minutes, TimeFrameUnit.Minute),
            start=start,
            end=end,
            limit=bar_limit,
            feed=feed,
        )
        bars = self.client.get_stock_bars(request)
        return normalize_bars_dataframe(bars.df, symbol=symbol)


def normalize_bars_dataframe(data: pd.DataFrame, symbol: str | None = None) -> pd.DataFrame:
    if data.empty:
        return data

    frame = data.copy()
    if isinstance(frame.index, pd.MultiIndex):
        if symbol is not None and symbol in frame.index.get_level_values(0):
            frame = frame.loc[symbol]
        else:
            frame = frame.reset_index(level=0, drop=True)

    rename_map = {column: str(column).lower() for column in frame.columns}
    frame = frame.rename(columns=rename_map)
    expected = ["open", "high", "low", "close", "volume"]
    available = [column for column in expected if column in frame.columns]
    return frame[available].sort_index()
