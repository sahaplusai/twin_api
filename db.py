"""
Multi-user twin store.

Default: SQLite file (TWIN_DB_PATH, or twin.db next to this module).
PostgreSQL: set TWIN_DB_URL to postgresql://user:pass@host:5432/dbname
            (or postgres://). Requires psycopg2.

Copyright (c) 2026 SVT Systems
All rights reserved.            
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

DB_PATH = Path(os.environ.get("TWIN_DB_PATH", Path(__file__).resolve().parent / "twin.db"))
_URL = os.environ.get("TWIN_DB_URL", "").strip()
_IS_PG = _URL.startswith("postgres")
_LOCK = threading.Lock()
_READY = False


def backend_name() -> str:
    return "postgresql" if _IS_PG else "sqlite"


def _adapt(sql: str) -> str:
    """SQL is written with %s placeholders. SQLite wants ?."""
    return sql if _IS_PG else sql.replace("%s", "?")


def _connect():
    if _IS_PG:
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
        except ImportError as exc:
            raise RuntimeError(
                "TWIN_DB_URL is set but psycopg2 is not installed. "
                "pip install 'psycopg2-binary==2.9.9'"
            ) from exc
        con = psycopg2.connect(_URL)
        con.cursor_factory = RealDictCursor
        return con
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


class _Cur:
    """Normalize sqlite3.Row and RealDictCursor so callers use row['col']."""

    def __init__(self, raw):
        self._c = raw

    @property
    def lastrowid(self) -> int:
        return int(self._c.lastrowid)

    def _as_dict(self, row):
        if row is None:
            return None
        if isinstance(row, dict):
            return row
        if hasattr(row, "keys"):
            return {k: row[k] for k in row.keys()}
        return row

    def fetchone(self):
        return self._as_dict(self._c.fetchone())

    def fetchall(self) -> List[Dict[str, Any]]:
        return [self._as_dict(r) for r in self._c.fetchall()]


class _Conn:
    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql: str, params: tuple = ()) -> _Cur:
        sql = _adapt(sql)
        if _IS_PG:
            cur = self.raw.cursor()
            cur.execute(sql, params)
            return _Cur(cur)
        return _Cur(self.raw.execute(sql, params))

    def executescript(self, script: str) -> None:
        self.raw.executescript(script)


_SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS profiles (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    payload TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS meals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    time_minutes INTEGER NOT NULL,
    doses TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS twin_state (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    initial_C1 TEXT NOT NULL DEFAULT '{}',
    initial_C2 TEXT NOT NULL DEFAULT '{}',
    initial_Z TEXT NOT NULL DEFAULT '{}',
    last_result TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_PG_DDL = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS profiles (
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        payload TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS meals (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        time_minutes INTEGER NOT NULL,
        doses TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS twin_state (
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        initial_C1 TEXT NOT NULL DEFAULT '{}',
        initial_C2 TEXT NOT NULL DEFAULT '{}',
        initial_Z TEXT NOT NULL DEFAULT '{}',
        last_result TEXT NOT NULL DEFAULT '{}',
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
]


def init_db() -> None:
    global _READY
    if _READY:
        return
    with _LOCK:
        if _READY:
            return
        con = _connect()
        try:
            if _IS_PG:
                cur = con.cursor()
                for stmt in _PG_DDL:
                    cur.execute(stmt)
            else:
                con.executescript(_SQLITE_DDL)
            con.commit()
            _READY = True
        finally:
            con.close()


@contextmanager
def tx() -> Iterator[_Conn]:
    init_db()
    with _LOCK:
        raw = _connect()
        try:
            yield _Conn(raw)
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()


def _now_sql() -> str:
    return "NOW()" if _IS_PG else "datetime('now')"


def create_user(email: str, password_hash: str) -> int:
    email = email.strip().lower()
    with tx() as con:
        if _IS_PG:
            row = con.execute(
                "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id",
                (email, password_hash),
            ).fetchone()
            uid = int(row["id"])
        else:
            cur = con.execute(
                "INSERT INTO users (email, password_hash) VALUES (%s, %s)",
                (email, password_hash),
            )
            uid = int(cur.lastrowid)
        con.execute(
            "INSERT INTO profiles (user_id, payload) VALUES (%s, '{}')",
            (uid,),
        )
        con.execute("INSERT INTO twin_state (user_id) VALUES (%s)", (uid,))
        return uid


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    email = email.strip().lower()
    with tx() as con:
        return con.execute(
            "SELECT * FROM users WHERE email = %s",
            (email,),
        ).fetchone()


def get_user(uid: int) -> Optional[Dict[str, Any]]:
    with tx() as con:
        return con.execute(
            "SELECT * FROM users WHERE id = %s",
            (uid,),
        ).fetchone()


def get_profile(uid: int) -> Dict[str, Any]:
    with tx() as con:
        row = con.execute(
            "SELECT payload FROM profiles WHERE user_id = %s",
            (uid,),
        ).fetchone()
    if not row:
        return {}
    return json.loads(row["payload"] or "{}")


def set_profile(uid: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    raw = json.dumps(payload)
    with tx() as con:
        con.execute(
            "INSERT INTO profiles (user_id, payload) VALUES (%s, %s) "
            "ON CONFLICT (user_id) DO UPDATE SET payload = excluded.payload",
            (uid, raw),
        )
    return payload


def add_meal(uid: int, time_minutes: int, doses: Dict[str, float]) -> int:
    with tx() as con:
        if _IS_PG:
            row = con.execute(
                "INSERT INTO meals (user_id, time_minutes, doses) VALUES (%s, %s, %s) RETURNING id",
                (uid, int(time_minutes), json.dumps(doses)),
            ).fetchone()
            return int(row["id"])
        cur = con.execute(
            "INSERT INTO meals (user_id, time_minutes, doses) VALUES (%s, %s, %s)",
            (uid, int(time_minutes), json.dumps(doses)),
        )
        return int(cur.lastrowid)


def list_meals(uid: int) -> List[Dict[str, Any]]:
    with tx() as con:
        rows = con.execute(
            "SELECT id, time_minutes, doses, created_at FROM meals "
            "WHERE user_id = %s ORDER BY time_minutes, id",
            (uid,),
        ).fetchall()
    out = []
    for r in rows:
        created = r["created_at"]
        out.append(
            {
                "id": r["id"],
                "time_minutes": r["time_minutes"],
                "doses": json.loads(r["doses"] or "{}"),
                "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
            }
        )
    return out


def clear_meals(uid: int) -> None:
    with tx() as con:
        con.execute("DELETE FROM meals WHERE user_id = %s", (uid,))


def get_state(uid: int) -> Dict[str, Any]:
    with tx() as con:
        row = con.execute(
            "SELECT * FROM twin_state WHERE user_id = %s",
            (uid,),
        ).fetchone()
    if not row:
        return {"initial_C1": {}, "initial_C2": {}, "initial_Z": {}, "last_result": {}}
    updated = row["updated_at"]
    return {
        "initial_C1": json.loads(row["initial_C1"] or "{}"),
        "initial_C2": json.loads(row["initial_C2"] or "{}"),
        "initial_Z": json.loads(row["initial_Z"] or "{}"),
        "last_result": json.loads(row["last_result"] or "{}"),
        "updated_at": updated.isoformat() if hasattr(updated, "isoformat") else updated,
    }


def save_state(uid: int, next_state: Dict[str, Any], last_result: Dict[str, Any]) -> None:
    now = _now_sql()
    with tx() as con:
        con.execute(
            "INSERT INTO twin_state "
            "(user_id, initial_C1, initial_C2, initial_Z, last_result, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, " + now + ") "
            "ON CONFLICT (user_id) DO UPDATE SET "
            "initial_C1 = excluded.initial_C1, "
            "initial_C2 = excluded.initial_C2, "
            "initial_Z = excluded.initial_Z, "
            "last_result = excluded.last_result, "
            "updated_at = excluded.updated_at",
            (
                uid,
                json.dumps(next_state.get("initial_C1") or {}),
                json.dumps(next_state.get("initial_C2") or {}),
                json.dumps(next_state.get("initial_Z") or {}),
                json.dumps(last_result),
            ),
        )
