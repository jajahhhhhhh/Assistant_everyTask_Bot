"""
Storage layer — SQLite-backed persistence for the assistant.

Tables:
    messages        — conversation history per user
    tasks           — extracted / manually created tasks
    reminders       — scheduled reminders linked to tasks or standalone
    calendar_events — calendar events (also exportable as iCal)
"""

import sqlite3
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger(__name__)


# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    role        TEXT    NOT NULL DEFAULT 'user',   -- 'user' | 'assistant'
    text        TEXT    NOT NULL,
    timestamp   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    title       TEXT    NOT NULL,
    description TEXT,
    category    TEXT    NOT NULL DEFAULT 'general', -- business | personal | urgent | general
    priority    INTEGER NOT NULL DEFAULT 2,          -- 1=high 2=medium 3=low
    deadline    TEXT,                                -- ISO-8601 or NULL
    status      TEXT    NOT NULL DEFAULT 'pending',  -- pending | in_progress | done | cancelled
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS reminders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    task_id     INTEGER,                             -- optional FK → tasks.id
    message     TEXT    NOT NULL,
    remind_at   TEXT    NOT NULL,                    -- ISO-8601 datetime
    sent        INTEGER NOT NULL DEFAULT 0           -- 0=pending 1=sent
);

CREATE TABLE IF NOT EXISTS calendar_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    title       TEXT    NOT NULL,
    description TEXT,
    start_time  TEXT    NOT NULL,
    end_time    TEXT,
    location    TEXT,
    created_at  TEXT    NOT NULL
);
"""


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Return a thread-safe SQLite connection with row-factory."""
    path = db_path or config.DATABASE_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Initialise the database (create tables if absent) and return the connection."""
    conn = _connect(db_path)
    conn.executescript(_SCHEMA)
    conn.commit()
    logger.info("Database initialised at %s", db_path or config.DATABASE_PATH)
    return conn


# ── Messages ──────────────────────────────────────────────────────────────────

def save_message(conn: sqlite3.Connection, user_id: int, text: str, role: str = "user") -> int:
    """Persist a conversation message and return its row id."""
    ts = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO messages (user_id, role, text, timestamp) VALUES (?, ?, ?, ?)",
        (user_id, role, text, ts),
    )
    conn.commit()
    return cur.lastrowid


def get_recent_messages(
    conn: sqlite3.Connection, user_id: int, limit: int = 20
) -> List[Dict[str, Any]]:
    """Return the most recent *limit* messages for *user_id* (oldest first)."""
    rows = conn.execute(
        "SELECT * FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


# ── Tasks ─────────────────────────────────────────────────────────────────────

def create_task(
    conn: sqlite3.Connection,
    user_id: int,
    title: str,
    description: str = "",
    category: str = "general",
    priority: int = 2,
    deadline: Optional[str] = None,
) -> int:
    """Insert a new task and return its id."""
    ts = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO tasks
               (user_id, title, description, category, priority, deadline, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
        (user_id, title, description, category, priority, deadline, ts),
    )
    conn.commit()
    return cur.lastrowid


def get_tasks(
    conn: sqlite3.Connection,
    user_id: int,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return tasks for *user_id*, optionally filtered by *status*."""
    if status:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE user_id=? AND status=? ORDER BY priority, created_at",
            (user_id, status),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE user_id=? ORDER BY priority, created_at",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def update_task_status(conn: sqlite3.Connection, task_id: int, status: str) -> bool:
    """Update task status.  Returns True if a row was changed."""
    cur = conn.execute(
        "UPDATE tasks SET status=? WHERE id=?", (status, task_id)
    )
    conn.commit()
    return cur.rowcount > 0


def delete_task(conn: sqlite3.Connection, task_id: int) -> bool:
    """Delete a task by id.  Returns True if a row was deleted."""
    cur = conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    conn.commit()
    return cur.rowcount > 0


# ── Reminders ─────────────────────────────────────────────────────────────────

def create_reminder(
    conn: sqlite3.Connection,
    user_id: int,
    message: str,
    remind_at: datetime,
    task_id: Optional[int] = None,
) -> int:
    """Schedule a reminder and return its id."""
    cur = conn.execute(
        "INSERT INTO reminders (user_id, task_id, message, remind_at) VALUES (?, ?, ?, ?)",
        (user_id, task_id, message, remind_at.isoformat()),
    )
    conn.commit()
    return cur.lastrowid


def get_pending_reminders(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Return all unsent reminders whose time has passed."""
    now = datetime.now(timezone.utc).isoformat()
    rows = conn.execute(
        "SELECT * FROM reminders WHERE sent=0 AND remind_at <= ?", (now,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_user_reminders(
    conn: sqlite3.Connection, user_id: int, include_sent: bool = False
) -> List[Dict[str, Any]]:
    """Return reminders for a specific user."""
    if include_sent:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE user_id=? ORDER BY remind_at",
            (user_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE user_id=? AND sent=0 ORDER BY remind_at",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_reminder_sent(conn: sqlite3.Connection, reminder_id: int) -> None:
    """Mark a reminder as delivered."""
    conn.execute("UPDATE reminders SET sent=1 WHERE id=?", (reminder_id,))
    conn.commit()


# ── Calendar events ───────────────────────────────────────────────────────────

def create_event(
    conn: sqlite3.Connection,
    user_id: int,
    title: str,
    start_time: datetime,
    end_time: Optional[datetime] = None,
    description: str = "",
    location: str = "",
) -> int:
    """Insert a calendar event and return its id."""
    ts = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO calendar_events
               (user_id, title, description, start_time, end_time, location, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id,
            title,
            description,
            start_time.isoformat(),
            end_time.isoformat() if end_time else None,
            location,
            ts,
        ),
    )
    conn.commit()
    return cur.lastrowid


def get_events(
    conn: sqlite3.Connection,
    user_id: int,
    from_dt: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Return calendar events for *user_id*, optionally from *from_dt* onwards."""
    if from_dt:
        rows = conn.execute(
            "SELECT * FROM calendar_events WHERE user_id=? AND start_time>=? ORDER BY start_time",
            (user_id, from_dt.isoformat()),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM calendar_events WHERE user_id=? ORDER BY start_time",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]
