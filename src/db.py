"""SQLite storage for user bindings, monthly tables and processed events."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from config import PROJECT_ROOT, Settings
from utils import beijing_now_iso

DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "accounting.db"


@dataclass(frozen=True)
class UserBinding:
    id: int
    tenant_key: str
    open_id: str
    union_id: str
    user_name: str
    bitable_app_token: str
    default_table_id: str
    bitable_view_url: str
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class MonthlyTable:
    id: int
    user_id: int
    month_key: str
    table_id: str
    table_name: str
    created_at: str


class AccountingDB:
    """Small SQLite repository used by the accounting service."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or _default_db_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_key TEXT NOT NULL,
                    open_id TEXT NOT NULL,
                    union_id TEXT NOT NULL DEFAULT '',
                    user_name TEXT NOT NULL DEFAULT '',
                    bitable_app_token TEXT NOT NULL,
                    default_table_id TEXT NOT NULL,
                    bitable_view_url TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(tenant_key, open_id)
                );

                CREATE TABLE IF NOT EXISTS monthly_tables (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    month_key TEXT NOT NULL,
                    table_id TEXT NOT NULL,
                    table_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(user_id, month_key),
                    FOREIGN KEY(user_id) REFERENCES users(id)
                );

                CREATE TABLE IF NOT EXISTS processed_events (
                    event_id TEXT PRIMARY KEY,
                    tenant_key TEXT NOT NULL DEFAULT '',
                    open_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'processed',
                    processed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS monthly_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    month_key TEXT NOT NULL,
                    summary_text TEXT NOT NULL,
                    sent_at TEXT NOT NULL,
                    UNIQUE(user_id, month_key),
                    FOREIGN KEY(user_id) REFERENCES users(id)
                );
                """
            )

    def upsert_user_binding(
        self,
        *,
        tenant_key: str,
        open_id: str,
        union_id: str,
        user_name: str,
        bitable_app_token: str,
        default_table_id: str,
        bitable_view_url: str,
    ) -> UserBinding:
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    tenant_key, open_id, union_id, user_name, bitable_app_token,
                    default_table_id, bitable_view_url, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                ON CONFLICT(tenant_key, open_id) DO UPDATE SET
                    union_id = excluded.union_id,
                    user_name = excluded.user_name,
                    bitable_app_token = excluded.bitable_app_token,
                    default_table_id = excluded.default_table_id,
                    bitable_view_url = excluded.bitable_view_url,
                    status = 'active',
                    updated_at = excluded.updated_at
                """,
                (
                    tenant_key,
                    open_id,
                    union_id,
                    user_name,
                    bitable_app_token,
                    default_table_id,
                    bitable_view_url,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM users WHERE tenant_key = ? AND open_id = ?",
                (tenant_key, open_id),
            ).fetchone()
        return _user_from_row(row)

    def get_user_binding(self, tenant_key: str, open_id: str) -> UserBinding | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE tenant_key = ? AND open_id = ? AND status = 'active'",
                (tenant_key, open_id),
            ).fetchone()
        return _user_from_row(row) if row else None

    def list_active_users(self) -> list[UserBinding]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM users WHERE status = 'active' ORDER BY id").fetchall()
        return [_user_from_row(row) for row in rows]

    def upsert_monthly_table(self, *, user_id: int, month_key: str, table_id: str, table_name: str) -> MonthlyTable:
        now = _now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO monthly_tables (user_id, month_key, table_id, table_name, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, month_key) DO UPDATE SET
                    table_id = excluded.table_id,
                    table_name = excluded.table_name
                """,
                (user_id, month_key, table_id, table_name, now),
            )
            row = conn.execute(
                "SELECT * FROM monthly_tables WHERE user_id = ? AND month_key = ?",
                (user_id, month_key),
            ).fetchone()
        return _monthly_table_from_row(row)

    def get_monthly_table(self, user_id: int, month_key: str) -> MonthlyTable | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM monthly_tables WHERE user_id = ? AND month_key = ?",
                (user_id, month_key),
            ).fetchone()
        return _monthly_table_from_row(row) if row else None

    def has_processed_event(self, event_id: str) -> bool:
        if not event_id:
            return False
        with self.connect() as conn:
            row = conn.execute("SELECT event_id FROM processed_events WHERE event_id = ?", (event_id,)).fetchone()
        return row is not None

    def mark_event_processed(self, event_id: str, *, tenant_key: str = "", open_id: str = "", status: str = "processed") -> bool:
        """Persist processed event id. Returns False if it already exists."""
        if not event_id:
            return True
        try:
            with self.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO processed_events (event_id, tenant_key, open_id, status, processed_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (event_id, tenant_key, open_id, status, _now()),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def has_monthly_summary(self, user_id: int, month_key: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM monthly_summaries WHERE user_id = ? AND month_key = ?",
                (user_id, month_key),
            ).fetchone()
        return row is not None

    def save_monthly_summary(self, user_id: int, month_key: str, summary_text: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO monthly_summaries (user_id, month_key, summary_text, sent_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, month_key) DO UPDATE SET
                    summary_text = excluded.summary_text,
                    sent_at = excluded.sent_at
                """,
                (user_id, month_key, summary_text, _now()),
            )


def _default_db_path() -> Path:
    explicit_path = os.getenv("ACCOUNTING_DB_PATH", "").strip()
    if explicit_path:
        return Path(explicit_path)
    volume_mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
    if volume_mount:
        return Path(volume_mount) / "accounting.db"
    return DEFAULT_DB_PATH


def ensure_default_user_binding(settings: Settings, db: AccountingDB) -> UserBinding:
    """Create a pseudo binding for local CLI compatibility."""
    return db.upsert_user_binding(
        tenant_key="local",
        open_id="local-cli",
        union_id="",
        user_name="本地命令行",
        bitable_app_token=settings.bitable_app_token,
        default_table_id=settings.table_id,
        bitable_view_url=settings.bitable_view_url,
    )


def _now() -> str:
    return beijing_now_iso()


def _user_from_row(row: sqlite3.Row) -> UserBinding:
    return UserBinding(
        id=int(row["id"]),
        tenant_key=str(row["tenant_key"]),
        open_id=str(row["open_id"]),
        union_id=str(row["union_id"]),
        user_name=str(row["user_name"]),
        bitable_app_token=str(row["bitable_app_token"]),
        default_table_id=str(row["default_table_id"]),
        bitable_view_url=str(row["bitable_view_url"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _monthly_table_from_row(row: sqlite3.Row) -> MonthlyTable:
    return MonthlyTable(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        month_key=str(row["month_key"]),
        table_id=str(row["table_id"]),
        table_name=str(row["table_name"]),
        created_at=str(row["created_at"]),
    )
