from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.database import TradingDatabase


@dataclass(frozen=True)
class RiskCheckResult:
    allowed: bool
    reason: str
    quantity: float = 0.0
    risk_amount: float = 0.0


class RiskManager:
    def __init__(self, settings: Settings, database: TradingDatabase) -> None:
        self.settings = settings
        self.database = database

    def calculate_position_size(self, equity: float, entry_price: float, stop_loss: float) -> RiskCheckResult:
        if stop_loss is None:
            return RiskCheckResult(False, "Blocked: stop loss is required.")

        if entry_price <= 0:
            return RiskCheckResult(False, "Blocked: entry price must be positive.")

        stop_distance = entry_price - stop_loss
        if stop_distance <= 0:
            return RiskCheckResult(False, "Blocked: stop loss must be below entry for long trades.")

        max_risk_amount = equity * self.settings.max_risk_per_trade
        quantity = int(max_risk_amount // stop_distance)
        if quantity < 1:
            return RiskCheckResult(False, "Blocked: account equity and stop distance allow less than one share.")

        risk_amount = quantity * stop_distance
        if risk_amount > max_risk_amount:
            return RiskCheckResult(False, "Blocked: trade exceeds max risk per trade.", quantity, risk_amount)

        return RiskCheckResult(True, "Allowed: position size is within configured risk.", float(quantity), risk_amount)

    def cap_quantity_to_buying_power(
        self,
        *,
        quantity: float,
        entry_price: float,
        stop_loss: float,
        buying_power: float | None,
    ) -> RiskCheckResult:
        if buying_power is None:
            risk_amount = quantity * (entry_price - stop_loss)
            return RiskCheckResult(True, "Allowed: position size is within configured risk.", quantity, risk_amount)

        if buying_power <= 0:
            return RiskCheckResult(False, "Blocked: buying power is unavailable.")

        available_cash = buying_power * 0.98
        affordable_quantity = int(available_cash // entry_price)
        if affordable_quantity < 1:
            return RiskCheckResult(False, "Blocked: buying power allows less than one share.")

        if affordable_quantity >= quantity:
            risk_amount = quantity * (entry_price - stop_loss)
            return RiskCheckResult(True, "Allowed: position size is within configured risk.", quantity, risk_amount)

        capped_quantity = float(affordable_quantity)
        risk_amount = capped_quantity * (entry_price - stop_loss)
        return RiskCheckResult(
            True,
            "Allowed: quantity capped to available buying power.",
            capped_quantity,
            risk_amount,
        )

    def daily_loss_reached(self, current_equity: float) -> bool:
        starting_equity = self.database.set_daily_start_equity_if_needed(current_equity)
        if starting_equity <= 0:
            return False
        drawdown = (starting_equity - current_equity) / starting_equity
        return drawdown >= self.settings.max_daily_loss

    def daily_profit_target_reached(self, current_equity: float) -> bool:
        if self.settings.daily_profit_target <= 0:
            return False
        starting_equity = self.database.set_daily_start_equity_if_needed(current_equity)
        if starting_equity <= 0:
            return False
        gain = (current_equity - starting_equity) / starting_equity
        reached = gain >= self.settings.daily_profit_target
        self.database.set_status("daily_profit_pct", round(gain, 6))
        self.database.set_status("daily_goal_reached", "true" if reached else "false")
        return reached

    def check_trade(
        self,
        *,
        side: str,
        equity: float,
        entry_price: float,
        stop_loss: float | None,
        open_positions_count: int,
        has_existing_position: bool,
        buying_power: float | None = None,
        requested_quantity: float | None = None,
    ) -> RiskCheckResult:
        if self.database.get_status("emergency_stop", False):
            return RiskCheckResult(False, "Blocked: emergency stop is enabled.")

        if self.daily_loss_reached(equity):
            self.database.set_status("emergency_stop", "true")
            return RiskCheckResult(False, "Blocked: max daily loss reached; emergency stop enabled.")

        normalized_side = side.upper()
        if normalized_side == "SELL":
            if not has_existing_position:
                return RiskCheckResult(False, "Blocked: long-only mode prevents opening short positions.")
            return RiskCheckResult(True, "Allowed: SELL exits an existing long position.", quantity=0)

        if normalized_side != "BUY":
            return RiskCheckResult(False, f"Blocked: unsupported order side {side}.")

        if has_existing_position:
            return RiskCheckResult(False, "Blocked: existing long position already open for this symbol.")

        if self.settings.daily_goal_blocks_new_buys and self.daily_profit_target_reached(equity):
            return RiskCheckResult(False, "Blocked: daily profit target reached; no new buys today.")

        if open_positions_count >= self.settings.max_open_positions:
            return RiskCheckResult(False, "Blocked: max open positions reached.")

        if stop_loss is None:
            return RiskCheckResult(False, "Blocked: stop loss is required.")

        if requested_quantity is not None:
            stop_distance = entry_price - stop_loss
            requested_risk = requested_quantity * stop_distance
            max_risk = equity * self.settings.max_risk_per_trade
            if requested_risk > max_risk:
                return RiskCheckResult(
                    False,
                    "Blocked: requested quantity exceeds max risk per trade.",
                    requested_quantity,
                    requested_risk,
                )

        size_result = self.calculate_position_size(equity, entry_price, stop_loss)
        if not size_result.allowed:
            return size_result

        return self.cap_quantity_to_buying_power(
            quantity=size_result.quantity,
            entry_price=entry_price,
            stop_loss=stop_loss,
            buying_power=buying_power,
        )
