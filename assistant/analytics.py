"""
Analytics engine — insights, progress reports, and recommendations.
"""

import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List

from assistant import storage

logger = logging.getLogger(__name__)


# ── Progress / statistics ─────────────────────────────────────────────────────

def task_progress_report(conn: sqlite3.Connection, user_id: int) -> str:
    """
    Return a formatted progress report for all tasks belonging to *user_id*.
    Includes counts per status, overdue items, and a completion rate.
    """
    all_tasks = storage.get_tasks(conn, user_id)
    if not all_tasks:
        return "📊 No tasks recorded yet."

    counts: Dict[str, int] = {}
    overdue: List[Dict[str, Any]] = []
    now_iso = datetime.utcnow().isoformat()

    for task in all_tasks:
        status = task.get("status", "pending")
        counts[status] = counts.get(status, 0) + 1
        if (
            task.get("deadline")
            and task["deadline"] < now_iso
            and status not in ("done", "cancelled")
        ):
            overdue.append(task)

    total = len(all_tasks)
    done = counts.get("done", 0)
    completion_rate = round(done / total * 100) if total else 0

    lines = [
        "📊 *Progress Report*",
        f"Total tasks : {total}",
        f"✅ Done      : {done}",
        f"⏳ Pending   : {counts.get('pending', 0)}",
        f"🔄 In progress: {counts.get('in_progress', 0)}",
        f"❌ Cancelled  : {counts.get('cancelled', 0)}",
        f"📈 Completion : {completion_rate}%",
    ]
    if overdue:
        lines.append(f"\n⚠️ Overdue tasks ({len(overdue)}):")
        for t in overdue[:5]:  # cap at 5 for readability
            lines.append(f"  • {t['title']} (due {t['deadline']})")
    return "\n".join(lines)


def weekly_summary(conn: sqlite3.Connection, user_id: int) -> str:
    """
    Return a concise weekly activity summary (tasks created / completed,
    reminders fired, upcoming events).
    """
    one_week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()

    all_tasks = storage.get_tasks(conn, user_id)
    recent_tasks = [t for t in all_tasks if t.get("created_at", "") >= one_week_ago]
    completed = [t for t in recent_tasks if t.get("status") == "done"]

    events = storage.get_events(conn, user_id, from_dt=datetime.utcnow())
    upcoming = events[:5]

    lines = [
        "📅 *Weekly Summary*",
        f"Tasks created this week : {len(recent_tasks)}",
        f"Tasks completed         : {len(completed)}",
    ]
    if upcoming:
        lines.append("\n📆 Upcoming events:")
        for ev in upcoming:
            lines.append(f"  • {ev['title']} — {ev['start_time']}")
    return "\n".join(lines)


def recommendations(conn: sqlite3.Connection, user_id: int) -> str:
    """
    Provide simple rule-based recommendations based on the user's task list.
    """
    all_tasks = storage.get_tasks(conn, user_id, status="pending")
    urgent = [t for t in all_tasks if t.get("category") == "urgent" or t.get("priority") == 1]
    high_count = len([t for t in all_tasks if t.get("priority") == 1])
    low_count = len([t for t in all_tasks if t.get("priority") == 3])

    tips: List[str] = []
    if urgent:
        tips.append(f"🚨 You have {len(urgent)} urgent task(s) — address them first.")
    if high_count > 5:
        tips.append("⚠️ Many high-priority tasks detected — consider delegating some.")
    if low_count > 10:
        tips.append("🗑️ You have many low-priority tasks — consider archiving old ones.")
    if not all_tasks:
        tips.append("✨ Task list is clear — great job!")
    elif len(all_tasks) > 20:
        tips.append("📋 Your task list is long — try batching similar tasks.")

    if not tips:
        tips.append("👍 Everything looks balanced. Keep it up!")
    return "💡 *Recommendations*\n" + "\n".join(tips)
