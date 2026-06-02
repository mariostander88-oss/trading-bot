from __future__ import annotations

import json
import logging
import urllib.request
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

# Public S3 endpoints maintained by the House/Senate Stock Watcher projects
_HOUSE_URL = "https://house-stock-watcher-data.s3-us-east-2.amazonaws.com/data/all_transactions.json"
_SENATE_URL = "https://senate-stock-watcher-data.s3-us-east-2.amazonaws.com/data/all_transactions.json"


class CongressTracker:
    """Fetches recent congressional stock purchases from public STOCK-Act disclosure APIs.

    Results are cached for 4 hours so we don't hammer the S3 endpoint every cycle.
    """

    _CACHE_TTL_SECONDS = 4 * 3600

    def __init__(self, lookback_days: int = 30) -> None:
        self.lookback_days = lookback_days
        self._cache: dict[str, list[str]] | None = None
        self._cache_at: datetime | None = None

    def get_recent_purchases(self) -> dict[str, list[str]]:
        """Return {TICKER: [politician_name, ...]} for purchases in the last `lookback_days`."""
        now = datetime.now(UTC)
        if (
            self._cache is not None
            and self._cache_at is not None
            and (now - self._cache_at).total_seconds() < self._CACHE_TTL_SECONDS
        ):
            return self._cache

        since = now - timedelta(days=self.lookback_days)
        purchases: dict[str, list[str]] = {}

        for url, name_field in [(_HOUSE_URL, "representative"), (_SENATE_URL, "senator")]:
            try:
                txns = self._fetch(url)
                for txn in txns:
                    self._process(txn, since, name_field, purchases)
            except Exception as exc:
                logger.warning("Congress tracker could not fetch %s: %s", url, exc)

        self._cache = purchases
        self._cache_at = now
        logger.info(
            "Congress tracker: %d symbols bought by politicians in the last %d days",
            len(purchases),
            self.lookback_days,
        )
        return purchases

    def _fetch(self, url: str) -> list[dict]:
        req = urllib.request.Request(url, headers={"User-Agent": "TradingBot/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        return data if isinstance(data, list) else []

    def _process(
        self,
        txn: dict,
        since: datetime,
        name_field: str,
        purchases: dict[str, list[str]],
    ) -> None:
        txn_type = str(txn.get("type", "")).lower()
        if "purchase" not in txn_type:
            return

        date_str = str(txn.get("transaction_date") or txn.get("date") or "")
        try:
            txn_date = datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            return

        if txn_date < since:
            return

        ticker = str(txn.get("ticker") or txn.get("symbol") or "").strip().upper()
        if not ticker or ticker in {"N/A", "--", ""}:
            return

        name = str(
            txn.get(name_field)
            or txn.get("representative")
            or txn.get("senator")
            or "Unknown"
        ).strip()

        purchases.setdefault(ticker, []).append(name)
