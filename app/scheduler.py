from __future__ import annotations

import logging
import traceback
from dataclasses import asdict

from apscheduler.schedulers.background import BackgroundScheduler

from app.broker import AlpacaBroker, OrderRequest
from app.config import Settings
from app.data_provider import AlpacaDataProvider
from app.database import TradingDatabase, utc_now
from app.risk_manager import RiskManager
from app.strategy import MovingAverageRsiStrategy, StrategySignal


logger = logging.getLogger(__name__)


class TradingBot:
    def __init__(
        self,
        settings: Settings,
        database: TradingDatabase,
        broker: AlpacaBroker | None = None,
        data_provider: AlpacaDataProvider | None = None,
        strategy: MovingAverageRsiStrategy | None = None,
        risk_manager: RiskManager | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.broker = broker or AlpacaBroker(settings, database)
        self.data_provider = data_provider or AlpacaDataProvider(settings)
        self.strategy = strategy or MovingAverageRsiStrategy(settings.stop_loss_pct)
        self.risk_manager = risk_manager or RiskManager(settings, database)

    def run_cycle(self, force: bool = False) -> dict[str, object]:
        logger.info("Starting trading cycle; force=%s", force)
        if self.database.get_status("emergency_stop", False):
            self.database.set_status("last_cycle_status", "blocked_emergency_stop")
            logger.warning("Trading cycle blocked because emergency stop is enabled")
            return {"status": "blocked", "reason": "Emergency stop is enabled."}

        if not force and not self.broker.is_market_open():
            self.database.set_status("last_cycle_status", "market_closed")
            logger.info("Trading cycle skipped because market is closed")
            return {"status": "skipped", "reason": "Market is closed."}

        account = self.broker.get_account_info()
        equity = float(account.get("equity") or account.get("portfolio_value") or 0)
        if self.risk_manager.daily_loss_reached(equity):
            self.database.set_status("emergency_stop", "true")
            self.database.set_status("last_cycle_status", "max_daily_loss_reached")
            logger.error("Max daily loss reached; emergency stop enabled")
            return {"status": "blocked", "reason": "Max daily loss reached; emergency stop enabled."}

        positions = self.broker.get_current_positions()
        position_by_symbol = {str(position.get("symbol", "")).upper(): position for position in positions}
        results: list[dict[str, object]] = []

        for symbol in self.settings.watchlist:
            try:
                result = self._process_symbol(symbol, equity, position_by_symbol)
                results.append(result)
            except Exception as exc:
                logger.exception("Cycle failed for %s", symbol)
                self.database.log_error("scheduler", f"{symbol}: {exc}", traceback.format_exc())
                results.append({"symbol": symbol, "status": "error", "reason": str(exc)})

        self.database.set_status("last_cycle_at", utc_now())
        self.database.set_status("last_cycle_status", "completed")
        logger.info("Trading cycle completed with %s symbol results", len(results))
        return {"status": "completed", "results": results}

    def _process_symbol(
        self,
        symbol: str,
        equity: float,
        position_by_symbol: dict[str, dict[str, object]],
    ) -> dict[str, object]:
        market_data = self.data_provider.fetch_latest_bars(symbol)
        signal = self.strategy.generate_signal(symbol, market_data)
        self._log_signal(signal)

        if signal.signal == "HOLD":
            logger.info("%s HOLD: %s", symbol, signal.reason)
            return {"symbol": symbol, "signal": "HOLD", "status": "logged", "reason": signal.reason}

        position = position_by_symbol.get(symbol.upper())
        has_position = position is not None and float(position.get("qty", 0) or 0) > 0
        risk_result = self.risk_manager.check_trade(
            side=signal.signal,
            equity=equity,
            entry_price=float(signal.close_price or 0),
            stop_loss=signal.stop_loss,
            open_positions_count=len(position_by_symbol),
            has_existing_position=has_position,
        )

        if not risk_result.allowed:
            logger.warning("%s %s blocked: %s", symbol, signal.signal, risk_result.reason)
            self.database.log_order(
                symbol=symbol,
                side=signal.signal,
                quantity=0,
                status="blocked",
                reason=risk_result.reason,
                metadata={"signal": asdict(signal)},
            )
            return {"symbol": symbol, "signal": signal.signal, "status": "blocked", "reason": risk_result.reason}

        quantity = risk_result.quantity
        if signal.signal == "SELL":
            quantity = float(position.get("qty", 0)) if position else 0
            if quantity <= 0:
                return {"symbol": symbol, "signal": "SELL", "status": "skipped", "reason": "No long position to exit."}

        order = self.broker.place_market_order(
            OrderRequest(symbol=symbol, side=signal.signal, quantity=quantity, reason=signal.reason)
        )
        order_id = str(order.get("id", ""))
        self.database.log_trade(
            symbol=symbol,
            side=signal.signal,
            quantity=quantity,
            price=signal.close_price,
            broker_order_id=order_id,
            metadata={"signal": asdict(signal), "order": order},
        )
        logger.info("%s %s submitted: quantity=%s order_id=%s", symbol, signal.signal, quantity, order_id)
        return {"symbol": symbol, "signal": signal.signal, "status": "submitted", "order_id": order_id}

    def _log_signal(self, signal: StrategySignal) -> None:
        self.database.log_signal(
            symbol=signal.symbol,
            signal=signal.signal,
            reason=signal.reason,
            close_price=signal.close_price,
            stop_loss=signal.stop_loss,
        )


def create_scheduler(bot: TradingBot) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        bot.run_cycle,
        "interval",
        minutes=bot.settings.timeframe_minutes,
        kwargs={"force": False},
        id="trading_cycle",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return scheduler
