from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.broker import AlpacaBroker
    from app.config import Settings
    from app.congress_tracker import CongressTracker
    from app.database import TradingDatabase
    from app.scheduler import TradingBot

logger = logging.getLogger(__name__)

_HELP = (
    "TRADING BOT COMMANDS\n"
    "STATUS / S    - portfolio summary\n"
    "POSITIONS / P - open positions\n"
    "BUY <sym>     - manual buy\n"
    "SELL <sym>    - manual sell\n"
    "PAUSE         - halt trading\n"
    "RESUME        - resume trading\n"
    "CYCLE         - run a cycle now\n"
    "WATCHLIST / W - show symbols\n"
    "CONGRESS      - recent congress buys\n"
    "CRYPTO        - BTC regime\n"
    "HELP          - this list"
)


def handle_command(
    body: str,
    from_number: str,
    *,
    settings: Settings,
    database: TradingDatabase,
    broker: AlpacaBroker,
    bot: TradingBot,
    congress_tracker: CongressTracker,
) -> str:
    parts = body.strip().upper().split()
    cmd = parts[0] if parts else ""
    arg = parts[1] if len(parts) >= 2 else ""

    try:
        if cmd in ("STATUS", "S"):
            return _status(database, broker)
        if cmd in ("POSITIONS", "P"):
            return _positions(broker)
        if cmd == "BUY" and arg:
            return _buy(arg, settings, database, broker, bot)
        if cmd == "SELL" and arg:
            return _sell(arg, database, broker)
        if cmd == "PAUSE":
            database.set_status("emergency_stop", "true")
            return "Emergency stop ENABLED. Bot paused."
        if cmd == "RESUME":
            database.set_status("emergency_stop", "false")
            return "Emergency stop CLEARED. Bot resumed."
        if cmd == "CYCLE":
            result = bot.run_cycle(force=True)
            count = len(result.get("results", []))
            return f"Cycle complete. Processed {count} symbols."
        if cmd in ("WATCHLIST", "W"):
            return "Watchlist: " + ", ".join(settings.watchlist)
        if cmd == "CONGRESS":
            return _congress(congress_tracker)
        if cmd == "CRYPTO":
            return _crypto(database)
        if cmd in ("HELP", "H", "?"):
            return _HELP
    except Exception as exc:
        logger.exception("WhatsApp command error: %s", exc)
        return f"Error: {exc}"

    return f"Unknown command '{body}'. Send HELP."


def _is_authorized(from_number: str, settings: Settings) -> bool:
    if not settings.twilio_whatsapp_to:
        return True
    authorized = set()
    for n in settings.twilio_whatsapp_to.split(","):
        n = n.strip().removeprefix("whatsapp:")
        authorized.add(n)
        authorized.add(f"whatsapp:{n}")
    return from_number in authorized or from_number.removeprefix("whatsapp:") in authorized


def _status(database: TradingDatabase, broker: AlpacaBroker) -> str:
    try:
        account = broker.get_account_info()
        equity = float(account.get("equity") or account.get("portfolio_value") or 0)
        bp = float(account.get("buying_power") or account.get("cash") or 0)
    except Exception:
        equity, bp = 0.0, 0.0

    positions = []
    try:
        positions = [p for p in broker.get_current_positions() if float(p.get("qty", 0) or 0) > 0]
    except Exception:
        pass

    e_stop = database.get_status("emergency_stop", False)
    daily_pnl = database.daily_realized_pnl()
    regime = database.get_status("crypto_regime", "unknown")
    congress_boost = database.get_status("congress_symbols_count", 0)

    return (
        f"TRADING BOT\n"
        f"Equity:      ${equity:,.2f}\n"
        f"Buying pwr:  ${bp:,.2f}\n"
        f"Positions:   {len(positions)}\n"
        f"Daily P&L:   ${daily_pnl:+,.2f}\n"
        f"BTC regime:  {regime}\n"
        f"Congress:    {congress_boost} symbols tracked\n"
        f"Paused:      {e_stop}"
    )


def _positions(broker: AlpacaBroker) -> str:
    try:
        positions = [p for p in broker.get_current_positions() if float(p.get("qty", 0) or 0) > 0]
    except Exception as exc:
        return f"Could not fetch positions: {exc}"

    if not positions:
        return "No open positions."

    lines = ["OPEN POSITIONS"]
    for p in positions:
        sym = p.get("symbol", "?")
        qty = p.get("qty", "?")
        mv = float(p.get("market_value") or 0)
        upl = float(p.get("unrealized_pl") or 0)
        lines.append(f"  {sym}: {qty} shares, ${mv:,.2f} ({upl:+,.2f})")
    return "\n".join(lines)


def _buy(
    symbol: str,
    settings: Settings,
    database: TradingDatabase,
    broker: AlpacaBroker,
    bot: TradingBot,
) -> str:
    from app.broker import OrderRequest

    df = bot.data_provider.fetch_latest_bars(symbol)
    if df.empty:
        return f"No market data for {symbol}."

    entry_price = float(df["close"].iloc[-1])
    stop_loss = round(entry_price * (1 - settings.stop_loss_pct), 2)

    account = broker.get_account_info()
    equity = float(account.get("equity") or account.get("portfolio_value") or 0)
    buying_power = float(account.get("buying_power") or account.get("cash") or equity)

    positions = broker.get_current_positions()
    position_by_symbol = {str(p.get("symbol", "")).upper(): p for p in positions}
    has_position = symbol.upper() in position_by_symbol

    risk = bot.risk_manager.check_trade(
        side="BUY",
        equity=equity,
        entry_price=entry_price,
        stop_loss=stop_loss,
        open_positions_count=len(position_by_symbol),
        has_existing_position=has_position,
        buying_power=buying_power,
    )
    if not risk.allowed:
        return f"BUY {symbol} blocked: {risk.reason}"

    order = broker.place_market_order(
        OrderRequest(symbol=symbol, side="BUY", quantity=risk.quantity, reason="WhatsApp manual buy")
    )
    database.log_trade(
        symbol=symbol,
        side="BUY",
        quantity=risk.quantity,
        price=entry_price,
        broker_order_id=str(order.get("id", "")),
        metadata={"source": "whatsapp"},
    )
    return (
        f"BUY {symbol} submitted\n"
        f"Shares: {int(risk.quantity)}\n"
        f"Price:  ~${entry_price:.2f}\n"
        f"Stop:   ${stop_loss:.2f}"
    )


def _sell(symbol: str, database: TradingDatabase, broker: AlpacaBroker) -> str:
    from app.broker import OrderRequest

    positions = broker.get_current_positions()
    position = next(
        (p for p in positions if str(p.get("symbol", "")).upper() == symbol.upper()),
        None,
    )
    if not position or float(position.get("qty", 0) or 0) <= 0:
        return f"No open position for {symbol}."

    qty = float(position["qty"])
    order = broker.place_market_order(
        OrderRequest(symbol=symbol, side="SELL", quantity=qty, reason="WhatsApp manual sell")
    )
    database.log_trade(
        symbol=symbol,
        side="SELL",
        quantity=qty,
        price=None,
        broker_order_id=str(order.get("id", "")),
        metadata={"source": "whatsapp"},
    )
    return f"SELL {symbol} submitted: {int(qty)} shares."


def _congress(congress_tracker: CongressTracker) -> str:
    try:
        picks = congress_tracker.get_recent_purchases()
    except Exception as exc:
        return f"Congress data error: {exc}"

    if not picks:
        return "No recent congressional purchases found."

    lines = [f"CONGRESS BUYS (last {congress_tracker.lookback_days}d)"]
    for symbol, politicians in list(picks.items())[:12]:
        unique_names = list(dict.fromkeys(politicians))[:2]
        names = ", ".join(unique_names)
        extra = f" +{len(politicians) - 2}" if len(politicians) > 2 else ""
        lines.append(f"  {symbol}: {names}{extra}")
    return "\n".join(lines)


def _crypto(database: TradingDatabase) -> str:
    regime = database.get_status("crypto_regime", "unknown")
    multipliers = {"uptrend": "1.0x (full size)", "sideways": "0.7x (reduced)", "downtrend": "0.4x (minimal)", "unknown": "1.0x (default)"}
    label = multipliers.get(str(regime), "1.0x")
    return f"BTC Regime: {regime}\nSize mult: {label}"
