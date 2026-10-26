from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import StateError
from .models import Instrument
from .utils import iso_utc, json_dumps, json_loads, parse_iso_datetime, utc_now

ACTIVE_STATUSES = {"PENDING_ENTRY", "ACTIVE", "EXITING"}
TERMINAL_STATUSES = {"CLOSED", "ABORTED"}
SCHEMA_VERSION = 1


@dataclass
class CycleState:
    cycle_id: str
    session_date: str
    status: str = "PENDING_ENTRY"
    direction: str = ""
    product_id: int | None = None
    product_symbol: str = ""
    instrument_json: str = ""
    entry_at: str = ""
    add_at: str = ""
    exit_at: str = ""
    entry_spot: str = ""
    ema21: str = ""
    target_price: str = ""
    initial_capital: str = ""
    capital_asset: str = ""
    initial_budget: str = ""
    initial_quantity: int = 0
    additional_quantity: int = 0
    filled_quantity: int = 0
    closed_quantity: int = 0
    avg_entry_premium: str = ""
    initial_limit_price: str = ""
    additional_limit_price: str = ""
    entry_client_order_id: str = ""
    additional_client_order_id: str = ""
    exit_reason: str = ""
    exit_order_ids: list[str] = field(default_factory=list)
    additional_started: bool = False
    additional_done: bool = False
    additional_skip_reason: str = ""
    close_reason: str = ""
    realized_pnl: str = ""
    created_at: str = ""
    updated_at: str = ""

    def set_instrument(self, instrument: Instrument) -> None:
        self.instrument_json = json_dumps(instrument.to_dict())

    def instrument(self) -> Instrument:
        if not self.instrument_json:
            raise StateError(f"cycle {self.cycle_id} has no instrument metadata")
        return Instrument.from_dict(json_loads(self.instrument_json, {}))

    def add_exit_order_id(self, client_order_id: str) -> None:
        if client_order_id not in self.exit_order_ids:
            self.exit_order_ids.append(client_order_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "session_date": self.session_date,
            "status": self.status,
            "direction": self.direction,
            "product_id": self.product_id,
            "product_symbol": self.product_symbol,
            "instrument_json": self.instrument_json,
            "entry_at": self.entry_at,
            "add_at": self.add_at,
            "exit_at": self.exit_at,
            "entry_spot": self.entry_spot,
            "ema21": self.ema21,
            "target_price": self.target_price,
            "initial_capital": self.initial_capital,
            "capital_asset": self.capital_asset,
            "initial_budget": self.initial_budget,
            "initial_quantity": self.initial_quantity,
            "additional_quantity": self.additional_quantity,
            "filled_quantity": self.filled_quantity,
            "closed_quantity": self.closed_quantity,
            "avg_entry_premium": self.avg_entry_premium,
            "initial_limit_price": self.initial_limit_price,
            "additional_limit_price": self.additional_limit_price,
            "entry_client_order_id": self.entry_client_order_id,
            "additional_client_order_id": self.additional_client_order_id,
            "exit_reason": self.exit_reason,
            "exit_order_ids": list(self.exit_order_ids),
            "additional_started": self.additional_started,
            "additional_done": self.additional_done,
            "additional_skip_reason": self.additional_skip_reason,
            "close_reason": self.close_reason,
            "realized_pnl": self.realized_pnl,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CycleState:
        return cls(
            cycle_id=str(raw["cycle_id"]),
            session_date=str(raw["session_date"]),
            status=str(raw.get("status", "PENDING_ENTRY")),
            direction=str(raw.get("direction", "")),
            product_id=(int(raw["product_id"]) if raw.get("product_id") is not None else None),
            product_symbol=str(raw.get("product_symbol", "")),
            instrument_json=str(raw.get("instrument_json", "")),
            entry_at=str(raw.get("entry_at", "")),
            add_at=str(raw.get("add_at", "")),
            exit_at=str(raw.get("exit_at", "")),
            entry_spot=str(raw.get("entry_spot", "")),
            ema21=str(raw.get("ema21", "")),
            target_price=str(raw.get("target_price", "")),
            initial_capital=str(raw.get("initial_capital", "")),
            capital_asset=str(raw.get("capital_asset", "")),
            initial_budget=str(raw.get("initial_budget", "")),
            initial_quantity=int(raw.get("initial_quantity", 0)),
            additional_quantity=int(raw.get("additional_quantity", 0)),
            filled_quantity=int(raw.get("filled_quantity", 0)),
            closed_quantity=int(raw.get("closed_quantity", 0)),
            avg_entry_premium=str(raw.get("avg_entry_premium", "")),
            initial_limit_price=str(raw.get("initial_limit_price", "")),
            additional_limit_price=str(raw.get("additional_limit_price", "")),
            entry_client_order_id=str(raw.get("entry_client_order_id", "")),
            additional_client_order_id=str(raw.get("additional_client_order_id", "")),
            exit_reason=str(raw.get("exit_reason", "")),
            exit_order_ids=list(raw.get("exit_order_ids", [])),
            additional_started=bool(raw.get("additional_started", False)),
            additional_done=bool(raw.get("additional_done", False)),
            additional_skip_reason=str(raw.get("additional_skip_reason", "")),
            close_reason=str(raw.get("close_reason", "")),
            realized_pnl=str(raw.get("realized_pnl", "")),
            created_at=str(raw.get("created_at", "")),
            updated_at=str(raw.get("updated_at", "")),
        )


@dataclass
class OrderRecord:
    client_order_id: str
    cycle_id: str
    action: str
    status: str = "INTENT"
    exchange_order_id: str = ""
    product_id: int = 0
    product_symbol: str = ""
    side: str = ""
    size: int = 0
    filled_size: int = 0
    average_fill_price: str = ""
    limit_price: str = ""
    order_type: str = "limit_order"
    reduce_only: bool = False
    attempt: int = 0
    error: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.status in {"FILLED", "CANCELLED", "FAILED"}

    @property
    def is_working(self) -> bool:
        return self.status in {"INTENT", "SUBMITTED", "OPEN", "PARTIALLY_FILLED"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "client_order_id": self.client_order_id,
            "cycle_id": self.cycle_id,
            "action": self.action,
            "status": self.status,
            "exchange_order_id": self.exchange_order_id,
            "product_id": self.product_id,
            "product_symbol": self.product_symbol,
            "side": self.side,
            "size": self.size,
            "filled_size": self.filled_size,
            "average_fill_price": self.average_fill_price,
            "limit_price": self.limit_price,
            "order_type": self.order_type,
            "reduce_only": self.reduce_only,
            "attempt": self.attempt,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> OrderRecord:
        return cls(
            client_order_id=str(raw["client_order_id"]),
            cycle_id=str(raw["cycle_id"]),
            action=str(raw["action"]),
            status=str(raw.get("status", "INTENT")),
            exchange_order_id=str(raw.get("exchange_order_id", "")),
            product_id=int(raw.get("product_id", 0)),
            product_symbol=str(raw.get("product_symbol", "")),
            side=str(raw.get("side", "")),
            size=int(raw.get("size", 0)),
            filled_size=int(raw.get("filled_size", 0)),
            average_fill_price=str(raw.get("average_fill_price", "")),
            limit_price=str(raw.get("limit_price", "")),
            order_type=str(raw.get("order_type", "limit_order")),
            reduce_only=bool(raw.get("reduce_only", False)),
            attempt=int(raw.get("attempt", 0)),
            error=str(raw.get("error", "")),
            created_at=str(raw.get("created_at", "")),
            updated_at=str(raw.get("updated_at", "")),
        )


class StateStore:
    """Small, transactional SQLite state store with WAL and JSON records."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.RLock()
        self.initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.db_path,
            timeout=30.0,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA busy_timeout=30000")
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def initialize(self) -> None:
        # executescript() performs an implicit COMMIT, so schema creation uses
        # autocommit mode instead of the explicit transaction helper.
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cycles (
                    cycle_id TEXT PRIMARY KEY,
                    session_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_cycles_status
                    ON cycles(status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_cycles_session
                    ON cycles(session_date);
                CREATE TABLE IF NOT EXISTS orders (
                    client_order_id TEXT PRIMARY KEY,
                    cycle_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    exchange_order_id TEXT,
                    updated_at TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_orders_cycle
                    ON orders(cycle_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_orders_status
                    ON orders(status);
                CREATE TABLE IF NOT EXISTS decisions (
                    session_date TEXT PRIMARY KEY,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    level TEXT NOT NULL,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    context TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
                CREATE TABLE IF NOT EXISTS runtime (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES(?, ?)",
                ("schema_version", str(SCHEMA_VERSION)),
            )

    # ------------------------------------------------------------------ cycles
    def save_cycle(self, cycle: CycleState) -> None:
        now = iso_utc(utc_now())
        if not cycle.created_at:
            cycle.created_at = now
        cycle.updated_at = now
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO cycles(cycle_id, session_date, status, updated_at, data)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(cycle_id) DO UPDATE SET
                    session_date=excluded.session_date,
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    data=excluded.data
                """,
                (
                    cycle.cycle_id,
                    cycle.session_date,
                    cycle.status,
                    cycle.updated_at,
                    json_dumps(cycle.to_dict(), sort_keys=True),
                ),
            )

    def get_cycle(self, cycle_id: str) -> CycleState | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT data FROM cycles WHERE cycle_id = ?", (cycle_id,)
            ).fetchone()
        if row is None:
            return None
        return CycleState.from_dict(json_loads(row["data"], {}))

    def list_active_cycles(self) -> list[CycleState]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT data FROM cycles WHERE status NOT IN ('CLOSED', 'ABORTED') "
                "ORDER BY session_date ASC"
            ).fetchall()
        return [CycleState.from_dict(json_loads(row["data"], {})) for row in rows]

    def list_cycles(self, limit: int = 20) -> list[CycleState]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT data FROM cycles ORDER BY session_date DESC LIMIT ?", (limit,)
            ).fetchall()
        return [CycleState.from_dict(json_loads(row["data"], {})) for row in rows]

    # ------------------------------------------------------------------ orders
    def save_order(self, order: OrderRecord) -> None:
        now = iso_utc(utc_now())
        if not order.created_at:
            order.created_at = now
        order.updated_at = now
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO orders(
                    client_order_id, cycle_id, action, status,
                    exchange_order_id, updated_at, data
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(client_order_id) DO UPDATE SET
                    cycle_id=excluded.cycle_id,
                    action=excluded.action,
                    status=excluded.status,
                    exchange_order_id=excluded.exchange_order_id,
                    updated_at=excluded.updated_at,
                    data=excluded.data
                """,
                (
                    order.client_order_id,
                    order.cycle_id,
                    order.action,
                    order.status,
                    order.exchange_order_id,
                    order.updated_at,
                    json_dumps(order.to_dict(), sort_keys=True),
                ),
            )

    def get_order(self, client_order_id: str) -> OrderRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT data FROM orders WHERE client_order_id = ?", (client_order_id,)
            ).fetchone()
        if row is None:
            return None
        return OrderRecord.from_dict(json_loads(row["data"], {}))

    def list_orders_for_cycle(self, cycle_id: str) -> list[OrderRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT data FROM orders WHERE cycle_id = ? ORDER BY updated_at ASC",
                (cycle_id,),
            ).fetchall()
        return [OrderRecord.from_dict(json_loads(row["data"], {})) for row in rows]

    def list_working_orders(self) -> list[OrderRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT data FROM orders WHERE status IN "
                "('INTENT', 'SUBMITTED', 'OPEN', 'PARTIALLY_FILLED')"
            ).fetchall()
        return [OrderRecord.from_dict(json_loads(row["data"], {})) for row in rows]

    # --------------------------------------------------------------- decisions
    def set_decision_if_absent(self, session_date: str, decision: str, reason: str) -> bool:
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO decisions(session_date, decision, reason, created_at) "
                "VALUES(?, ?, ?, ?)",
                (session_date, decision, reason, iso_utc(utc_now())),
            )
            return cursor.rowcount == 1

    def set_decision(self, session_date: str, decision: str, reason: str) -> bool:
        """Compatibility alias for deterministic, idempotent session decisions."""
        return self.set_decision_if_absent(session_date, decision, reason)

    def get_decision(self, session_date: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT decision, reason, created_at FROM decisions WHERE session_date = ?",
                (session_date,),
            ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------ events
    def add_event(
        self,
        level: str,
        title: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO events(ts, level, title, message, context) VALUES(?, ?, ?, ?, ?)",
                (
                    iso_utc(utc_now()),
                    level.upper(),
                    title,
                    message,
                    json_dumps(context or {}, sort_keys=True),
                ),
            )
            connection.execute(
                "DELETE FROM events WHERE id NOT IN "
                "(SELECT id FROM events ORDER BY id DESC LIMIT 10000)"
            )

    def list_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT ts, level, title, message, context FROM events "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["context"] = json_loads(item["context"], {})
            result.append(item)
        return result

    # ----------------------------------------------------------------- runtime
    def set_runtime(self, key: str, value: Any) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO runtime(key, value, updated_at) VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, json_dumps({"value": value}), iso_utc(utc_now())),
            )

    def get_runtime(self, key: str, default: Any = None) -> Any:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM runtime WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return default
        payload = json_loads(row["value"], {})
        return payload.get("value", default)

    def runtime_dict(self) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute("SELECT key, value FROM runtime").fetchall()
        output: dict[str, Any] = {}
        for row in rows:
            output[row["key"]] = json_loads(row["value"], {}).get("value")
        return output

    # ---------------------------------------------------------------- utilities
    def backup(self, destination: str | Path) -> Path:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as source, sqlite3.connect(target) as sink:
            source.backup(sink)
        return target

    def health_check(self) -> bool:
        try:
            with self._connect() as connection:
                connection.execute("SELECT COUNT(*) FROM cycles").fetchone()
            return True
        except sqlite3.Error as exc:
            raise StateError(f"state database health check failed: {exc}") from exc

    @staticmethod
    def parse_time(value: str) -> datetime | None:
        return parse_iso_datetime(value)
