from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import (
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    desc,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Connection, Engine


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


metadata = MetaData()

signals = Table(
    "signals",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", String(64), nullable=False),
    Column("symbol", String(32), nullable=False),
    Column("signal", String(16), nullable=False),
    Column("reason", Text, nullable=False),
    Column("close_price", Float),
    Column("stop_loss", Float),
    Column("metadata", Text),
)

orders = Table(
    "orders",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", String(64), nullable=False),
    Column("symbol", String(32), nullable=False),
    Column("side", String(16), nullable=False),
    Column("quantity", Float, nullable=False),
    Column("status", String(32), nullable=False),
    Column("broker_order_id", String(128)),
    Column("reason", Text),
    Column("metadata", Text),
)

trades = Table(
    "trades",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", String(64), nullable=False),
    Column("symbol", String(32), nullable=False),
    Column("side", String(16), nullable=False),
    Column("quantity", Float, nullable=False),
    Column("price", Float),
    Column("realized_pnl", Float, default=0),
    Column("broker_order_id", String(128)),
    Column("metadata", Text),
)

errors = Table(
    "errors",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", String(64), nullable=False),
    Column("component", String(64), nullable=False),
    Column("message", Text, nullable=False),
    Column("traceback", Text),
)

bot_status = Table(
    "bot_status",
    metadata,
    Column("key", String(128), primary_key=True),
    Column("value", Text, nullable=False),
    Column("updated_at", String(64), nullable=False),
)

LOG_TABLES = {
    "signals": signals,
    "orders": orders,
    "trades": trades,
    "errors": errors,
}


class TradingDatabase:
    def __init__(
        self,
        path: str | Path = "trading_bot.db",
        database_url: str | None = None,
    ) -> None:
        self.database_url = _normalize_database_url(database_url)
        self.path = Path(path) if not self.database_url else None
        self.engine = self._create_engine()
        self.initialize()

    @property
    def backend(self) -> str:
        if not self.database_url or self.database_url.startswith("sqlite"):
            return "sqlite"
        if self.database_url.startswith("postgresql"):
            return "postgres"
        return "external"

    def _create_engine(self) -> Engine:
        if self.database_url:
            connect_args = {"prepare_threshold": None} if self.backend == "postgres" else {}
            return create_engine(self.database_url, pool_pre_ping=True, connect_args=connect_args)

        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return create_engine(
            f"sqlite:///{self.path.as_posix()}",
            connect_args={"check_same_thread": False},
        )

    @contextmanager
    def connect(self) -> Iterator[Connection]:
        with self.engine.begin() as conn:
            yield conn

    def initialize(self) -> None:
        metadata.create_all(self.engine)
        defaults = {
            "emergency_stop": "false",
            "manual_live_trading_enabled": "false",
            "last_cycle_at": "",
            "last_cycle_status": "never_run",
            "daily_start_equity": "",
            "daily_start_equity_date": "",
        }
        with self.connect() as conn:
            for key, value in defaults.items():
                existing = conn.execute(select(bot_status.c.key).where(bot_status.c.key == key)).first()
                if existing is None:
                    conn.execute(
                        insert(bot_status).values(
                            key=key,
                            value=value,
                            updated_at=utc_now(),
                        )
                    )

    def set_status(self, key: str, value: Any) -> None:
        encoded = json.dumps(value, default=str) if not isinstance(value, str) else value
        now = utc_now()
        with self.connect() as conn:
            result = conn.execute(
                update(bot_status)
                .where(bot_status.c.key == key)
                .values(value=encoded, updated_at=now)
            )
            if result.rowcount == 0:
                conn.execute(insert(bot_status).values(key=key, value=encoded, updated_at=now))

    def get_status(self, key: str, default: Any = None) -> Any:
        with self.connect() as conn:
            row = conn.execute(select(bot_status.c.value).where(bot_status.c.key == key)).mappings().first()
        if row is None:
            return default
        value = row["value"]
        if value in {"true", "false"}:
            return value == "true"
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value

    def status_snapshot(self) -> dict[str, Any]:
        with self.connect() as conn:
            rows = conn.execute(select(bot_status).order_by(bot_status.c.key)).mappings().all()
        return {row["key"]: {"value": row["value"], "updated_at": row["updated_at"]} for row in rows}

    def log_signal(
        self,
        symbol: str,
        signal: str,
        reason: str,
        close_price: float | None = None,
        stop_loss: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        return self._insert_row(
            signals,
            {
                "created_at": utc_now(),
                "symbol": symbol,
                "signal": signal,
                "reason": reason,
                "close_price": close_price,
                "stop_loss": stop_loss,
                "metadata": json.dumps(metadata or {}, default=str),
            },
        )

    def log_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        status: str,
        broker_order_id: str | None = None,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        return self._insert_row(
            orders,
            {
                "created_at": utc_now(),
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "status": status,
                "broker_order_id": broker_order_id,
                "reason": reason,
                "metadata": json.dumps(metadata or {}, default=str),
            },
        )

    def log_trade(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float | None,
        realized_pnl: float = 0,
        broker_order_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        return self._insert_row(
            trades,
            {
                "created_at": utc_now(),
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "price": price,
                "realized_pnl": realized_pnl,
                "broker_order_id": broker_order_id,
                "metadata": json.dumps(metadata or {}, default=str),
            },
        )

    def log_error(self, component: str, message: str, traceback: str | None = None) -> int:
        return self._insert_row(
            errors,
            {
                "created_at": utc_now(),
                "component": component,
                "message": message,
                "traceback": traceback,
            },
        )

    def latest_rows(self, table: str, limit: int = 20) -> list[dict[str, Any]]:
        if table not in LOG_TABLES:
            raise ValueError(f"Unsupported table: {table}")
        selected_table = LOG_TABLES[table]
        with self.connect() as conn:
            rows = (
                conn.execute(
                    select(selected_table)
                    .order_by(desc(selected_table.c.created_at), desc(selected_table.c.id))
                    .limit(limit)
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows]

    def daily_realized_pnl(self) -> float:
        today = date.today().isoformat()
        with self.connect() as conn:
            row = conn.execute(
                select(func.coalesce(func.sum(trades.c.realized_pnl), 0).label("pnl")).where(
                    func.substr(trades.c.created_at, 1, 10) == today
                )
            ).mappings().first()
        return float(row["pnl"] if row else 0)

    def set_daily_start_equity_if_needed(self, equity: float) -> float:
        today = date.today().isoformat()
        existing_date = self.get_status("daily_start_equity_date", "")
        existing_equity = self.get_status("daily_start_equity", "")
        if existing_date != today or existing_equity in {"", None}:
            self.set_status("daily_start_equity_date", today)
            self.set_status("daily_start_equity", str(float(equity)))
            return float(equity)
        return float(existing_equity)

    def _insert_row(self, table: Table, values: dict[str, Any]) -> int:
        with self.connect() as conn:
            result = conn.execute(insert(table).values(**values))
            primary_key = result.inserted_primary_key
            return int(primary_key[0]) if primary_key else 0


def _normalize_database_url(database_url: str | None) -> str | None:
    if not database_url:
        return None
    url = database_url.strip()
    if not url:
        return None
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url
