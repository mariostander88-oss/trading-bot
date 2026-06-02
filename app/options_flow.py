from __future__ import annotations

import json
import logging
import urllib.request
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


class OptionsFlowAnalyzer:
    """Detects unusual options activity via Yahoo Finance's free public options API.

    A high call/put volume ratio means smart money is buying calls — bullish signal.
    A low ratio (put-heavy) means smart money is hedging or shorting — bearish signal.
    Results are cached for 30 minutes to avoid hammering Yahoo on every cycle.
    """

    _CACHE_TTL_SECONDS = 1800

    def __init__(self, min_bullish_ratio: float = 2.0) -> None:
        self.min_bullish_ratio = min_bullish_ratio
        self._cache: dict[str, tuple[float, datetime]] = {}

    def get_call_put_ratio(self, symbol: str) -> float:
        """Returns call/put volume ratio. 1.0 = neutral, >2.0 = unusual call buying."""
        now = datetime.now(UTC)
        if symbol in self._cache:
            ratio, cached_at = self._cache[symbol]
            if (now - cached_at).total_seconds() < self._CACHE_TTL_SECONDS:
                return ratio

        try:
            ratio = self._fetch_ratio(symbol)
            self._cache[symbol] = (ratio, now)
            logger.info("Options flow %s: call/put ratio = %.2f", symbol, ratio)
            return ratio
        except Exception as exc:
            logger.warning("Options flow unavailable for %s: %s", symbol, exc)
            return 1.0

    def is_unusual_bullish(self, symbol: str) -> bool:
        return self.get_call_put_ratio(symbol) >= self.min_bullish_ratio

    def is_unusual_bearish(self, symbol: str) -> bool:
        ratio = self.get_call_put_ratio(symbol)
        return ratio > 0 and ratio <= (1 / self.min_bullish_ratio)

    def size_multiplier(self, symbol: str, signal: str) -> float:
        """Return a position-size multiplier based on options conviction."""
        ratio = self.get_call_put_ratio(symbol)
        if signal == "BUY":
            if ratio >= self.min_bullish_ratio:
                return 1.25   # Unusual call buying — boost size
            if ratio <= (1 / self.min_bullish_ratio):
                return 0.5    # Unusual put buying — reduce size on BUY
        elif signal == "SELL":
            if ratio <= (1 / self.min_bullish_ratio):
                return 1.25   # Bearish options — boost short confidence
        return 1.0

    def _fetch_ratio(self, symbol: str) -> float:
        url = f"https://query1.finance.yahoo.com/v7/finance/options/{symbol}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        result = data.get("optionChain", {}).get("result", [])
        if not result:
            return 1.0

        options = result[0].get("options", [])
        if not options:
            return 1.0

        calls = options[0].get("calls", [])
        puts = options[0].get("puts", [])

        call_vol = sum(int(c.get("volume", 0) or 0) for c in calls)
        put_vol = sum(int(p.get("volume", 0) or 0) for p in puts)

        if put_vol == 0:
            return 3.0 if call_vol > 0 else 1.0
        if call_vol == 0:
            return 0.1

        return round(call_vol / put_vol, 3)
