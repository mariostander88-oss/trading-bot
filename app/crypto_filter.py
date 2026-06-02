from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pandas as pd

logger = logging.getLogger(__name__)


class CryptoRegimeFilter:
    """Uses BTC/USD hourly SMA trend to return a position-size multiplier.

    uptrend  → 1.0 (full size)
    sideways → 0.7
    downtrend → 0.4
    unknown  → 1.0 (fail open)
    """

    _MULTIPLIERS = {"uptrend": 1.0, "sideways": 0.7, "downtrend": 0.4, "unknown": 1.0}

    def __init__(self) -> None:
        self._client = None

    @property
    def _crypto_client(self):
        if self._client is None:
            from alpaca.data.historical import CryptoHistoricalDataClient

            self._client = CryptoHistoricalDataClient()
        return self._client

    def get_regime(self) -> tuple[str, float]:
        """Returns (regime, size_multiplier). Never raises."""
        try:
            df = self._fetch_btc_hourly()
            if df.empty or len(df) < 50:
                return "unknown", 1.0

            sma20 = df["close"].rolling(20).mean().iloc[-1]
            sma50 = df["close"].rolling(50).mean().iloc[-1]

            if pd.isna(sma20) or pd.isna(sma50):
                return "unknown", 1.0

            if sma20 > sma50 * 1.002:
                regime = "uptrend"
            elif sma20 < sma50 * 0.998:
                regime = "downtrend"
            else:
                regime = "sideways"

            logger.info(
                "BTC regime: %s (SMA20=%.0f SMA50=%.0f multiplier=%.1f)",
                regime, sma20, sma50, self._MULTIPLIERS[regime],
            )
            return regime, self._MULTIPLIERS[regime]

        except Exception as exc:
            logger.warning("Crypto regime filter failed (defaulting to 1.0): %s", exc)
            return "unknown", 1.0

    def _fetch_btc_hourly(self) -> pd.DataFrame:
        from alpaca.data.requests import CryptoBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        end = datetime.now(UTC)
        start = end - timedelta(days=6)
        request = CryptoBarsRequest(
            symbol_or_symbols=["BTC/USD"],
            timeframe=TimeFrame(1, TimeFrameUnit.Hour),
            start=start,
            end=end,
        )
        bars = self._crypto_client.get_crypto_bars(request)
        df = bars.df
        if df.empty:
            return df

        if isinstance(df.index, pd.MultiIndex):
            levels = df.index.get_level_values(0)
            df = df.loc["BTC/USD"] if "BTC/USD" in levels else df.reset_index(level=0, drop=True)

        df.columns = [str(c).lower() for c in df.columns]
        return df.sort_index()
