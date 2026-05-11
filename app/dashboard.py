from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from app.broker import AlpacaBroker
    from app.config import ConfigurationError, load_settings
    from app.data_provider import AlpacaDataProvider
    from app.database import TradingDatabase
    from app.logging_config import configure_logging
    from app.scheduler import TradingBot
except ModuleNotFoundError:
    from broker import AlpacaBroker
    from config import ConfigurationError, load_settings
    from data_provider import AlpacaDataProvider
    from database import TradingDatabase
    from logging_config import configure_logging
    from scheduler import TradingBot


configure_logging()
st.set_page_config(page_title="Paper Trading Bot", layout="wide")
st.title("Paper Trading Bot Monitor")
st.warning("This tool is for education and monitoring only. It is not financial advice.")


def require_dashboard_login() -> None:
    expected_password = os.getenv("DASHBOARD_PASSWORD", "").strip()
    if not expected_password:
        st.info("Dashboard password is not set. Local access is open.")
        return

    if st.session_state.get("dashboard_authenticated") is True:
        return

    with st.form("dashboard_login"):
        password = st.text_input("Dashboard password", type="password")
        submitted = st.form_submit_button("Log in")

    if submitted and secrets.compare_digest(password, expected_password):
        st.session_state["dashboard_authenticated"] = True
        st.rerun()

    if submitted:
        st.error("Incorrect password.")
    st.stop()


require_dashboard_login()


try:
    settings = load_settings()
    database = TradingDatabase(
        path=settings.database_path,
        database_url=settings.database_url,
    )
    broker = AlpacaBroker(settings, database)
    bot = TradingBot(settings, database, broker=broker, data_provider=AlpacaDataProvider(settings))
except ConfigurationError as exc:
    st.error(f"Configuration error: {exc}")
    st.stop()


left, middle, right = st.columns(3)
with left:
    st.metric("Trading mode", settings.trading_mode.upper())
with middle:
    st.metric("Watchlist", ", ".join(settings.watchlist))
with right:
    st.metric("Daily realized P&L", f"{database.daily_realized_pnl():.2f}")


status = database.status_snapshot()
emergency_stop = database.get_status("emergency_stop", False)
manual_live_gate = database.get_status("manual_live_trading_enabled", False)

control_left, control_right = st.columns(2)
with control_left:
    if st.button("Run Paper Cycle Now", disabled=not settings.is_paper):
        try:
            result = bot.run_cycle(force=True)
            st.success(f"Cycle result: {result.get('status')}")
        except Exception as exc:
            database.log_error("dashboard", f"Manual cycle failed: {exc}")
            st.error(f"Manual cycle failed: {exc}")
    if st.button("Enable Emergency Stop", type="primary", disabled=emergency_stop):
        database.set_status("emergency_stop", "true")
        st.rerun()
    if st.button("Clear Emergency Stop", disabled=not emergency_stop):
        database.set_status("emergency_stop", "false")
        st.rerun()
with control_right:
    live_gate_allowed = settings.is_live and settings.live_trading_confirmed
    st.checkbox(
        "Manual live trading dashboard gate",
        value=manual_live_gate,
        disabled=not live_gate_allowed,
        key="live_gate_display",
    )
    if st.button("Enable Live Gate", disabled=manual_live_gate or not live_gate_allowed):
        database.set_status("manual_live_trading_enabled", "true")
        st.rerun()
    if st.button("Disable Live Gate", disabled=not manual_live_gate):
        database.set_status("manual_live_trading_enabled", "false")
        st.rerun()


st.subheader("Bot Status")
st.dataframe(pd.DataFrame.from_dict(status, orient="index"), use_container_width=True)


st.subheader("Account")
try:
    account = broker.get_account_info()
    st.dataframe(pd.DataFrame([account]), use_container_width=True)
except Exception as exc:
    st.info(f"Account unavailable: {exc}")


st.subheader("Open Positions")
try:
    positions = broker.get_current_positions()
    st.dataframe(pd.DataFrame(positions), use_container_width=True)
except Exception as exc:
    st.info(f"Positions unavailable: {exc}")


signals_col, trades_col = st.columns(2)
with signals_col:
    st.subheader("Latest Signals")
    st.dataframe(pd.DataFrame(database.latest_rows("signals", 25)), use_container_width=True)
with trades_col:
    st.subheader("Latest Trades")
    st.dataframe(pd.DataFrame(database.latest_rows("trades", 25)), use_container_width=True)


st.subheader("Latest Errors")
st.dataframe(pd.DataFrame(database.latest_rows("errors", 10)), use_container_width=True)
