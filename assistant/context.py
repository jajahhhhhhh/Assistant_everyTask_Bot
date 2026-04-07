"""
Context / memory management — persistent conversational context per user.

Provides a thin wrapper that:
  - Saves every inbound and outbound message to the database
  - Supplies recent conversation history to the NLP processor
  - Tracks active context (recent intent, last extracted tasks, etc.)
"""

import logging
import sqlite3
import threading
from typing import Any, Dict, List, Optional

from assistant import storage

logger = logging.getLogger(__name__)

# Maximum messages kept in the in-process cache (not the DB)
_CACHE_LIMIT = 50

# Per-user in-memory context cache  {user_id: {...}}
_context_cache: Dict[int, Dict[str, Any]] = {}
_cache_lock = threading.Lock()


def _get_cache(user_id: int) -> Dict[str, Any]:
    with _cache_lock:
        if user_id not in _context_cache:
            _context_cache[user_id] = {
                "last_intent": None,
                "last_tasks": [],
                "message_count": 0,
            }
        return _context_cache[user_id]


# ── Public API ────────────────────────────────────────────────────────────────

def record_user_message(
    conn: sqlite3.Connection, user_id: int, text: str
) -> None:
    """Persist the user's message and update in-memory context."""
    storage.save_message(conn, user_id, text, role="user")
    ctx = _get_cache(user_id)
    ctx["message_count"] = ctx.get("message_count", 0) + 1


def record_assistant_reply(
    conn: sqlite3.Connection, user_id: int, text: str
) -> None:
    """Persist the assistant's reply."""
    storage.save_message(conn, user_id, text, role="assistant")


def get_history(
    conn: sqlite3.Connection, user_id: int, limit: int = 20
) -> List[Dict[str, Any]]:
    """Return recent conversation history for *user_id* (oldest first)."""
    return storage.get_recent_messages(conn, user_id, limit=limit)


def set_last_intent(user_id: int, intent: str) -> None:
    _get_cache(user_id)["last_intent"] = intent


def get_last_intent(user_id: int) -> Optional[str]:
    return _get_cache(user_id).get("last_intent")


def set_last_tasks(user_id: int, tasks: List[Dict[str, Any]]) -> None:
    _get_cache(user_id)["last_tasks"] = tasks


def get_last_tasks(user_id: int) -> List[Dict[str, Any]]:
    return _get_cache(user_id).get("last_tasks", [])


def clear_context(user_id: int) -> None:
    """Reset the in-memory context for *user_id* (does not delete DB records)."""
    with _cache_lock:
        _context_cache.pop(user_id, None)
