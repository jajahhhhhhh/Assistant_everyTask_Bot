"""
Task management — high-level helpers that combine the storage layer
with NLP-driven task extraction.
"""

import logging
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from assistant import processor, storage

logger = logging.getLogger(__name__)

PRIORITY_LABELS = {1: "🔴 High", 2: "🟡 Medium", 3: "🟢 Low"}
STATUS_EMOJI = {
    "pending": "⏳",
    "in_progress": "🔄",
    "done": "✅",
    "cancelled": "❌",
}
CATEGORY_EMOJI = {
    "business": "💼",
    "personal": "🏠",
    "urgent": "🚨",
    "general": "📋",
}


# ── Core helpers ──────────────────────────────────────────────────────────────

def add_task(
    conn: sqlite3.Connection,
    user_id: int,
    title: str,
    description: str = "",
    category: str = "general",
    priority: int = 2,
    deadline: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a task and return it as a dict (including its new id)."""
    task_id = storage.create_task(
        conn, user_id, title, description, category, priority, deadline
    )
    return {
        "id": task_id,
        "title": title,
        "description": description,
        "category": category,
        "priority": priority,
        "deadline": deadline,
        "status": "pending",
        "created_at": datetime.utcnow().isoformat(),
    }


def extract_and_save_tasks(
    conn: sqlite3.Connection, user_id: int, text: str
) -> List[Dict[str, Any]]:
    """
    Run NLP task extraction on *text*, persist the results, and return
    the list of created task dicts (each with a real DB id).
    """
    extracted = processor.extract_tasks(text)
    saved: List[Dict[str, Any]] = []
    for task in extracted:
        task_id = storage.create_task(
            conn,
            user_id,
            title=task.get("title", "Untitled task"),
            description=task.get("description", ""),
            category=task.get("category", "general"),
            priority=int(task.get("priority", 2)),
            deadline=task.get("deadline"),
        )
        task["id"] = task_id
        saved.append(task)
    return saved


def format_task(task: Dict[str, Any]) -> str:
    """Format a single task dict as a human-readable string."""
    priority_label = PRIORITY_LABELS.get(task.get("priority", 2), "Medium")
    status_em = STATUS_EMOJI.get(task.get("status", "pending"), "⏳")
    cat_em = CATEGORY_EMOJI.get(task.get("category", "general"), "📋")

    lines = [
        f"{status_em} *{task['title']}* (#{task['id']})",
        f"  Category: {cat_em} {task.get('category', 'general').capitalize()}",
        f"  Priority: {priority_label}",
    ]
    if task.get("description"):
        lines.append(f"  Notes: {task['description']}")
    if task.get("deadline"):
        lines.append(f"  ⏰ Deadline: {task['deadline']}")
    return "\n".join(lines)


def format_task_list(tasks: List[Dict[str, Any]]) -> str:
    """Format a list of tasks as a Markdown-friendly string."""
    if not tasks:
        return "No tasks found."
    return "\n\n".join(format_task(t) for t in tasks)


def task_statistics(tasks: List[Dict[str, Any]]) -> Dict[str, int]:
    """Return a dict of status → count for the given task list."""
    stats: Dict[str, int] = {}
    for task in tasks:
        status = task.get("status", "pending")
        stats[status] = stats.get(status, 0) + 1
    return stats
