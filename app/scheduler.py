from __future__ import annotations

import logging
import traceback
from dataclasses import asdict
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from app.broker import AlpacaBroker, OrderRequest
from app.config import Settings
from app.congress_tracker import CongressTracker
from app.crypto_filter import CryptoRegimeFilter
from app.data_provider import AlpacaDataProvider
from app.database import TradingDatabase, utc_now
from app.notifier import NotificationService
from app.options_flow import OptionsFlowAnalyzer
from app.risk_manager import RiskManager
from app.strategy import COVER_SIGNAL, MovingAverageRsiStrategy, StrategyB, StrategySignal


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
        self.congress_tracker = CongressTracker(lookback_days=settings.congress_lookback_days)
        self.crypto_filter = CryptoRegimeFilter()
        self.options_flow = OptionsFlowAnalyzer(min_bullish_ratio=settings.options_flow_min_ratio)
        self.strategy_b = StrategyB(
            stop_loss_pct=settings.stop_loss_pct,
            take_profit_pct=settings.take_profit_pct,
        )
        # Rebuild primary strategy with take_profit_pct
        self.strategy = strategy or MovingAverageRsiStrategy(
            stop_loss_pct=settings.stop_loss_pct,
            take_profit_pct=settings.take_profit_pct,
        )

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
        buying_power = float(account.get("buying_power") or account.get("cash") or equity)
        if self.risk_manager.daily_loss_reached(equity):
            self.database.set_status("emergency_stop", "true")
            self.database.set_status("last_cycle_status", "max_daily_loss_reached")
            logger.error("Max daily loss reached; emergency stop enabled")
            return {"status": "blocked", "reason": "Max daily loss reached; emergency stop enabled."}

        daily_goal_reached = self.risk_manager.daily_profit_target_reached(equity)
        if daily_goal_reached:
            logger.info("Daily profit target reached; new BUY orders will be blocked")

        # --- BTC regime filter ---
        crypto_regime, crypto_multiplier = self.crypto_filter.get_regime()
        self.database.set_status("crypto_regime", crypto_regime)
        self.database.set_status("crypto_size_multiplier", crypto_multiplier)
        logger.info("Crypto regime: %s (multiplier=%.2f)", crypto_regime, crypto_multiplier)

        # --- Congressional purchase tracker ---
        congress_picks: dict[str, list[str]] = {}
        try:
            congress_picks = self.congress_tracker.get_recent_purchases()
            self.database.set_status("congress_symbols_count", len(congress_picks))
        except Exception as exc:
            logger.warning("Congress tracker skipped: %s", exc)

        positions = self.broker.get_current_positions()
        position_by_symbol = {str(position.get("symbol", "")).upper(): position for position in positions}
        results: list[dict[str, object]] = []

        # Main watchlist
        processed: set[str] = set()
        for symbol in self.settings.watchlist:
            processed.add(symbol.upper())
            try:
                result = self._process_symbol(
                    symbol, equity, buying_power, position_by_symbol,
                    crypto_multiplier=crypto_multiplier,
                    congress_picks=congress_picks,
                )
                results.append(result)
            except Exception as exc:
                logger.exception("Cycle failed for %s", symbol)
                self.database.log_error("scheduler", f"{symbol}: {exc}", traceback.format_exc())
                results.append({"symbol": symbol, "status": "error", "reason": str(exc)})

        # Bonus: congress symbols not already in the watchlist
        # If YOLO enabled → use chaos budget with no filters
        # Otherwise → run through normal strategy
        for symbol in congress_picks:
            if symbol.upper() in processed or not symbol.isalpha():
                continue
            processed.add(symbol.upper())
            try:
                if self.settings.yolo_enabled:
                    result = self._process_yolo(symbol, buying_power, position_by_symbol, congress_picks)
                else:
                    result = self._process_symbol(
                        symbol, equity, buying_power, position_by_symbol,
                        crypto_multiplier=crypto_multiplier,
                        congress_picks=congress_picks,
                    )
                result["congress_bonus"] = True
                results.append(result)
            except Exception as exc:
                logger.exception("Congress bonus cycle failed for %s", symbol)
                self.database.log_error("scheduler", f"congress/{symbol}: {exc}", traceback.format_exc())
                results.append({"symbol": symbol, "status": "error", "reason": str(exc), "congress_bonus": True})

        self.database.set_status("last_cycle_at", utc_now())
        self.database.set_status("last_cycle_status", "completed")
        logger.info("Trading cycle completed with %s symbol results", len(results))
        return {"status": "completed", "results": results}

    def _process_symbol(
        self,
        symbol: str,
        equity: float,
        buying_power: float,
        position_by_symbol: dict[str, dict[str, object]],
        *,
        crypto_multiplier: float = 1.0,
        congress_picks: dict[str, list[str]] | None = None,
    ) -> dict[str, object]:
        market_data = self.data_provider.fetch_latest_bars(symbol)
        signal = self.strategy.generate_signal(symbol, market_data)

        # --- Position data ---
        position = position_by_symbol.get(symbol.upper())
        position_qty = float(position.get("qty", 0) or 0) if position else 0
        has_long = position_qty > 0
        has_short = position_qty < 0
        has_position = has_long or has_short

        # --- Take profit / Stop loss check (overrides strategy signal) ---
        if has_position and signal.close_price is not None:
            avg_entry = float(position.get("avg_entry_price", 0) or 0)
            exit_signal = self.strategy.check_position_exit(
                symbol, avg_entry, position_qty, float(signal.close_price)
            )
            if exit_signal is not None:
                signal = exit_signal

        # --- Short selling: if bearish signal, no position, shorts enabled → go short ---
        if (
            signal.signal == "SELL"
            and not has_long
            and not has_short
            and self.settings.short_selling_enabled
        ):
            stop = round(float(signal.close_price or 0) * (1 + self.settings.stop_loss_pct), 2)
            signal = StrategySignal(
                symbol, "SELL",
                signal.reason + " [SHORT ENTRY]",
                signal.close_price, stop,
            )
        elif signal.signal == "BUY" and has_short:
            # Cover short on bullish signal
            signal = StrategySignal(
                symbol, COVER_SIGNAL,
                signal.reason + " [COVER SHORT]",
                signal.close_price, None,
            )

        # --- A/B Arena: run Strategy B and adjust conviction ---
        ab_multiplier = 1.0
        if self.settings.ab_testing_enabled and signal.signal in ("BUY", "SELL"):
            try:
                sig_b = self.strategy_b.generate_signal(symbol, market_data)
                if sig_b.signal == signal.signal:
                    ab_multiplier = 1.2
                    signal = StrategySignal(
                        symbol, signal.signal,
                        signal.reason + f" [A+B agree ✓]",
                        signal.close_price, signal.stop_loss,
                    )
                    self.database.set_status(f"ab_wins_{signal.signal.lower()}",
                        int(self.database.get_status(f"ab_wins_{signal.signal.lower()}", 0) or 0) + 1)
                else:
                    ab_multiplier = 0.7
                    signal = StrategySignal(
                        symbol, signal.signal,
                        signal.reason + f" [A only, B says {sig_b.signal}]",
                        signal.close_price, signal.stop_loss,
                    )
            except Exception as exc:
                logger.warning("Strategy B failed for %s: %s", symbol, exc)

        # --- Options flow: size multiplier from call/put ratio ---
        options_multiplier = 1.0
        if self.settings.options_flow_enabled and signal.signal in ("BUY", "SELL"):
            try:
                options_multiplier = self.options_flow.size_multiplier(symbol, signal.signal)
                ratio = self.options_flow.get_call_put_ratio(symbol)
                if options_multiplier != 1.0:
                    signal = StrategySignal(
                        symbol, signal.signal,
                        signal.reason + f" [Options C/P={ratio:.1f}x]",
                        signal.close_price, signal.stop_loss,
                    )
            except Exception as exc:
                logger.warning("Options flow failed for %s: %s", symbol, exc)

        # --- Congress annotation ---
        congress_politicians = (congress_picks or {}).get(symbol.upper(), [])
        if congress_politicians and signal.signal in ("BUY", "HOLD"):
            unique_names = list(dict.fromkeys(congress_politicians))[:3]
            names_str = ", ".join(unique_names)
            extra = f" +{len(congress_politicians) - 3} more" if len(congress_politicians) > 3 else ""
            signal = StrategySignal(
                symbol, signal.signal,
                signal.reason + f" [Congress: {names_str}{extra}]",
                signal.close_price, signal.stop_loss,
            )

        self._log_signal(signal)

        if signal.signal == "HOLD":
            logger.info("%s HOLD: %s", symbol, signal.reason)
            return {"symbol": symbol, "signal": "HOLD", "status": "logged", "reason": signal.reason}

        # --- Risk check ---
        risk_result = self.risk_manager.check_trade(
            side=signal.signal,
            equity=equity,
            entry_price=float(signal.close_price or 0),
            stop_loss=signal.stop_loss,
            open_positions_count=len(position_by_symbol),
            has_existing_position=has_long,
            buying_power=buying_power,
        )

        if not risk_result.allowed and signal.signal not in (COVER_SIGNAL,):
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

        # --- Determine quantity ---
        broker_side = "BUY"
        if signal.signal == "SELL":
            if has_long:
                quantity = position_qty   # close long
            elif self.settings.short_selling_enabled:
                quantity = max(1.0, risk_result.quantity)  # open short
            else:
                return {"symbol": symbol, "signal": "SELL", "status": "skipped", "reason": "No long position to exit."}
            broker_side = "SELL"
        elif signal.signal == COVER_SIGNAL:
            quantity = abs(position_qty)  # buy back the short
            broker_side = "BUY"
        else:  # BUY
            combined_mult = crypto_multiplier * ab_multiplier * options_multiplier
            quantity = max(1.0, int(risk_result.quantity * combined_mult))
            broker_side = "BUY"
            if combined_mult != 1.0:
                logger.info("%s BUY size multiplier=%.2f → %d shares", symbol, combined_mult, int(quantity))

        order = self.broker.place_market_order(
            OrderRequest(symbol=symbol, side=broker_side, quantity=quantity, reason=signal.reason)
        )
        order_id = str(order.get("id", ""))
        self.database.log_trade(
            symbol=symbol,
            side=broker_side,
            quantity=quantity,
            price=signal.close_price,
            broker_order_id=order_id,
            metadata={"signal": asdict(signal), "order": order},
        )
        logger.info("%s %s submitted: qty=%s order_id=%s", symbol, broker_side, quantity, order_id)
        return {"symbol": symbol, "signal": signal.signal, "status": "submitted", "order_id": order_id}

    def _process_yolo(
        self,
        symbol: str,
        buying_power: float,
        position_by_symbol: dict[str, dict[str, object]],
        congress_picks: dict[str, list[str]],
    ) -> dict[str, object]:
        """Auto YOLO: congressional picks get a fixed budget, no strategy filter, max chaos."""
        if buying_power < self.settings.yolo_budget:
            return {"symbol": symbol, "signal": "YOLO", "status": "skipped",
                    "reason": f"Buying power ${buying_power:.0f} < YOLO budget ${self.settings.yolo_budget:.0f}"}

        if self.database.get_status("emergency_stop", False):
            return {"symbol": symbol, "signal": "YOLO", "status": "blocked", "reason": "Emergency stop active."}

        position = position_by_symbol.get(symbol.upper())
        if position and float(position.get("qty", 0) or 0) > 0:
            return {"symbol": symbol, "signal": "YOLO", "status": "skipped", "reason": "Already holding this."}

        try:
            df = self.data_provider.fetch_latest_bars(symbol)
        except Exception as exc:
            return {"symbol": symbol, "signal": "YOLO", "status": "error", "reason": str(exc)}

        if df.empty:
            return {"symbol": symbol, "signal": "YOLO", "status": "skipped", "reason": "No market data."}

        current_price = float(df["close"].iloc[-1])
        if current_price <= 0:
            return {"symbol": symbol, "signal": "YOLO", "status": "skipped", "reason": "Invalid price."}

        quantity = max(1, int(self.settings.yolo_budget / current_price))
        politicians = ", ".join(list(dict.fromkeys(congress_picks.get(symbol.upper(), [])))[:2])
        reason = f"YOLO: Congress buy ({politicians}) @ ${current_price:.2f} — no strategy filter"

        order = self.broker.place_market_order(
            OrderRequest(symbol=symbol, side="BUY", quantity=quantity, reason=reason)
        )
        order_id = str(order.get("id", ""))
        self.database.log_trade(
            symbol=symbol, side="BUY", quantity=quantity, price=current_price,
            broker_order_id=order_id,
            metadata={"yolo": True, "congress": politicians},
        )
        logger.info("YOLO %s: %d shares @ $%.2f (Congress: %s)", symbol, quantity, current_price, politicians)
        return {"symbol": symbol, "signal": "YOLO", "status": "submitted", "order_id": order_id, "yolo": True}

    def _log_signal(self, signal: StrategySignal) -> None:
        self.database.log_signal(
            symbol=signal.symbol,
            signal=signal.signal,
            reason=signal.reason,
            close_price=signal.close_price,
            stop_loss=signal.stop_loss,
        )


def create_scheduler(bot: TradingBot, notifier: NotificationService | None = None) -> BackgroundScheduler:
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
    if notifier and bot.settings.notifications_enabled:
        timezone = ZoneInfo(bot.settings.notification_timezone)
        for index, report_time in enumerate(bot.settings.notification_times):
            hour_text, minute_text = report_time.split(":")
            scheduler.add_job(
                notifier.send_status_report,
                "cron",
                hour=int(hour_text),
                minute=int(minute_text),
                timezone=timezone,
                id=f"status_report_{index}",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
    return scheduler
