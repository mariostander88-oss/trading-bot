from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.config import Settings
from app.database import TradingDatabase


class BrokerSafetyError(RuntimeError):
    """Raised when an order attempts to bypass trading safety rules."""


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: str
    quantity: float
    reason: str


class AlpacaBroker:
    def __init__(self, settings: Settings, database: TradingDatabase, trading_client: Any | None = None) -> None:
        self.settings = settings
        self.database = database
        self._trading_client = trading_client

    @property
    def trading_client(self) -> Any:
        if self._trading_client is None:
            from alpaca.trading.client import TradingClient

            paper = self.settings.is_paper
            self._trading_client = TradingClient(
                api_key=self.settings.alpaca_api_key,
                secret_key=self.settings.alpaca_secret_key,
                paper=paper,
                url_override=self.settings.alpaca_base_url,
            )
        return self._trading_client

    def ensure_ordering_allowed(self) -> None:
        manual_live_enabled = bool(self.database.get_status("manual_live_trading_enabled", False))
        if self.settings.is_paper:
            return
        if not self.settings.live_trading_allowed(manual_live_enabled):
            raise BrokerSafetyError(
                "Live trading blocked. Requires TRADING_MODE=live, LIVE_TRADING_CONFIRMED=true, "
                "and manual_live_trading_enabled=true in the dashboard."
            )

    def get_account_info(self) -> dict[str, Any]:
        account = self.trading_client.get_account()
        return _model_to_dict(account)

    def get_equity(self) -> float:
        account = self.get_account_info()
        return float(account.get("equity") or account.get("portfolio_value") or 0)

    def get_current_positions(self) -> list[dict[str, Any]]:
        return [_model_to_dict(position) for position in self.trading_client.get_all_positions()]

    def get_position(self, symbol: str) -> dict[str, Any] | None:
        try:
            return _model_to_dict(self.trading_client.get_open_position(symbol))
        except Exception:
            return None

    def is_market_open(self) -> bool:
        clock = self.trading_client.get_clock()
        return bool(getattr(clock, "is_open", False))

    def place_market_order(self, request: OrderRequest) -> dict[str, Any]:
        self.ensure_ordering_allowed()

        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        side = request.side.upper()
        if side not in {"BUY", "SELL"}:
            raise BrokerSafetyError(f"Unsupported order side: {request.side}")

        order_data = MarketOrderRequest(
            symbol=request.symbol,
            qty=request.quantity,
            side=OrderSide.BUY if side == "BUY" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        order = self.trading_client.submit_order(order_data=order_data)
        order_dict = _model_to_dict(order)
        self.database.log_order(
            symbol=request.symbol,
            side=side,
            quantity=request.quantity,
            status=str(order_dict.get("status", "submitted")),
            broker_order_id=str(order_dict.get("id", "")),
            reason=request.reason,
            metadata={"order": _json_safe(order_dict), "request": asdict(request)},
        )
        return order_dict

    def cancel_orders(self) -> list[Any]:
        return list(self.trading_client.cancel_orders())


def _model_to_dict(model: Any) -> dict[str, Any]:
    if isinstance(model, dict):
        return model
    if hasattr(model, "model_dump"):
        return model.model_dump()
    if hasattr(model, "dict"):
        return model.dict()
    return dict(getattr(model, "__dict__", {}))


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value) if not isinstance(value, (str, int, float, bool, type(None))) else value
