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

    def daily_loss_reached(self, current_equity: float) -> bool:
        starting_equity = self.database.set_daily_start_equity_if_needed(current_equity)
        if starting_equity <= 0:
            return False
        drawdown = (starting_equity - current_equity) / starting_equity
        return drawdown >= self.settings.max_daily_loss

    def check_trade(
        self,
        *,
        side: str,
        equity: float,
        entry_price: float,
        stop_loss: float | None,
        open_positions_count: int,
        has_existing_position: bool,
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

        if open_positions_count >= self.settings.max_open_positions and not has_existing_position:
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

        return self.calculate_position_size(equity, entry_price, stop_loss)
