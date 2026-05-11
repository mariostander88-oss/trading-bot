from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.broker import AlpacaBroker
from app.config import ConfigurationError, Settings, load_settings
from app.database import TradingDatabase
from app.logging_config import configure_logging
from app.scheduler import TradingBot, create_scheduler


configure_logging()


class AppState:
    settings: Settings | None = None
    database: TradingDatabase | None = None
    broker: AlpacaBroker | None = None
    bot: TradingBot | None = None
    scheduler: Any | None = None
    config_error: str | None = None


state = AppState()


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        state.settings = load_settings()
        state.database = TradingDatabase(
            path=state.settings.database_path,
            database_url=state.settings.database_url,
        )
        state.broker = AlpacaBroker(state.settings, state.database)
        state.bot = TradingBot(state.settings, state.database, broker=state.broker)
        state.scheduler = create_scheduler(state.bot)
        state.scheduler.start()
    except ConfigurationError as exc:
        state.config_error = str(exc)
        logging.getLogger(__name__).error("Configuration failed: %s", exc)
    yield
    if state.scheduler and state.scheduler.running:
        state.scheduler.shutdown(wait=False)


app = FastAPI(title="Paper Trading Bot MVP", version="0.1.0", lifespan=lifespan)
bearer_scheme = HTTPBearer(auto_error=False)


def require_ready() -> tuple[Settings, TradingDatabase, AlpacaBroker, TradingBot]:
    if state.config_error:
        raise HTTPException(status_code=503, detail=state.config_error)
    if not state.settings or not state.database or not state.broker or not state.bot:
        raise HTTPException(status_code=503, detail="Application is not initialized.")
    return state.settings, state.database, state.broker, state.bot


def require_api_auth(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> None:
    settings = state.settings
    if not settings or not settings.api_admin_token:
        return
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Missing API bearer token.")
    if not secrets.compare_digest(credentials.credentials, settings.api_admin_token):
        raise HTTPException(status_code=403, detail="Invalid API bearer token.")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": state.config_error is None,
        "config_error": state.config_error,
        "scheduler_running": bool(state.scheduler and state.scheduler.running),
    }


@app.get("/status", dependencies=[Depends(require_api_auth)])
def status() -> dict[str, Any]:
    settings, database, _, _ = require_ready()
    manual_live_gate = bool(database.get_status("manual_live_trading_enabled", False))
    return {
        "trading_mode": settings.trading_mode,
        "paper_trading": settings.is_paper,
        "live_trading_effectively_enabled": settings.live_trading_allowed(manual_live_gate),
        "watchlist": settings.watchlist,
        "status": database.status_snapshot(),
        "daily_realized_pnl": database.daily_realized_pnl(),
    }


@app.get("/account", dependencies=[Depends(require_api_auth)])
def account() -> dict[str, Any]:
    _, _, broker, _ = require_ready()
    return broker.get_account_info()


@app.get("/positions", dependencies=[Depends(require_api_auth)])
def positions() -> list[dict[str, Any]]:
    _, _, broker, _ = require_ready()
    return broker.get_current_positions()


@app.get("/signals", dependencies=[Depends(require_api_auth)])
def latest_signals(limit: int = 20) -> list[dict[str, Any]]:
    _, database, _, _ = require_ready()
    return database.latest_rows("signals", limit)


@app.get("/trades", dependencies=[Depends(require_api_auth)])
def latest_trades(limit: int = 20) -> list[dict[str, Any]]:
    _, database, _, _ = require_ready()
    return database.latest_rows("trades", limit)


@app.post("/cycle", dependencies=[Depends(require_api_auth)])
def run_manual_paper_cycle() -> dict[str, Any]:
    settings, _, _, bot = require_ready()
    if not settings.is_paper:
        raise HTTPException(status_code=403, detail="Manual API cycles are only allowed in paper mode.")
    return bot.run_cycle(force=True)


@app.post("/emergency-stop", dependencies=[Depends(require_api_auth)])
def set_emergency_stop(enabled: bool = True) -> dict[str, Any]:
    _, database, _, _ = require_ready()
    database.set_status("emergency_stop", "true" if enabled else "false")
    return {"emergency_stop": enabled}


@app.post("/manual-live-trading-gate", dependencies=[Depends(require_api_auth)])
def set_manual_live_trading_gate(enabled: bool) -> dict[str, Any]:
    settings, database, _, _ = require_ready()
    if enabled and not (settings.is_live and settings.live_trading_confirmed):
        raise HTTPException(
            status_code=403,
            detail="Cannot enable dashboard live gate unless TRADING_MODE=live and LIVE_TRADING_CONFIRMED=true.",
        )
    database.set_status("manual_live_trading_enabled", "true" if enabled else "false")
    return {"manual_live_trading_enabled": enabled}
