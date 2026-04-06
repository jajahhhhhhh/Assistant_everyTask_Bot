"""
Reminder and notification system.

Uses APScheduler to poll the database every minute for due reminders
and dispatches them via a configurable callback (Telegram send_message).
"""

import logging
from datetime import datetime, timedelta
from typing import Callable, Optional

import sqlite3

import dateparser
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

import config
from assistant import storage

logger = logging.getLogger(__name__)


class ReminderService:
    """
    Polls the reminders table and fires a callback for each due reminder.

    Usage::

        async def send(user_id, message):
            await bot.send_message(chat_id=user_id, text=message)

        svc = ReminderService(conn, send)
        svc.start()
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        notify_callback: Callable,
        poll_interval_seconds: int = 60,
    ):
        self._conn = conn
        self._notify = notify_callback
        self._scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)
        self._poll_interval = poll_interval_seconds

    def start(self) -> None:
        """Start the background scheduler."""
        self._scheduler.add_job(
            self._check_reminders,
            trigger=IntervalTrigger(seconds=self._poll_interval),
            id="reminder_poll",
            replace_existing=True,
        )
        self._scheduler.start()
        logger.info("ReminderService started (poll every %ds)", self._poll_interval)

    def stop(self) -> None:
        """Stop the background scheduler."""
        self._scheduler.shutdown(wait=False)
        logger.info("ReminderService stopped")

    async def _check_reminders(self) -> None:
        """Check for due reminders and invoke the notify callback."""
        due = storage.get_pending_reminders(self._conn)
        for reminder in due:
            try:
                await self._notify(reminder["user_id"], f"⏰ Reminder: {reminder['message']}")
                storage.mark_reminder_sent(self._conn, reminder["id"])
                logger.info("Sent reminder #%d to user %d", reminder["id"], reminder["user_id"])
            except Exception as exc:  # pragma: no cover
                logger.error("Failed to send reminder #%d: %s", reminder["id"], exc)


# ── Parsing helpers ───────────────────────────────────────────────────────────

def parse_reminder_time(text: str) -> Optional[datetime]:
    """
    Parse a natural-language time expression and return a UTC datetime.

    Examples: "in 30 minutes", "tomorrow at 9am", "next Monday".
    Returns None if parsing fails.
    """
    tz = pytz.timezone(config.TIMEZONE)
    settings = {
        "PREFER_DATES_FROM": "future",
        "RETURN_AS_TIMEZONE_AWARE": True,
        "TIMEZONE": config.TIMEZONE,
    }
    result = dateparser.parse(text, settings=settings)
    if result:
        return result.astimezone(pytz.utc).replace(tzinfo=None)
    return None


def schedule_reminder(
    conn: sqlite3.Connection,
    user_id: int,
    message: str,
    time_expression: str,
    task_id: Optional[int] = None,
) -> Optional[int]:
    """
    Parse *time_expression* and create a reminder in the database.

    Returns the reminder id on success, or None if the time cannot be parsed.
    """
    remind_at = parse_reminder_time(time_expression)
    if remind_at is None:
        logger.warning("Could not parse reminder time: %r", time_expression)
        return None
    return storage.create_reminder(conn, user_id, message, remind_at, task_id)


def format_reminder_list(reminders: list) -> str:
    """Format a list of reminder dicts as human-readable text."""
    if not reminders:
        return "No upcoming reminders."
    lines = []
    for r in reminders:
        lines.append(f"⏰ #{r['id']} — {r['message']} at {r['remind_at']}")
    return "\n".join(lines)
