"""
Tests for assistant/storage.py — database CRUD operations.
Uses a temporary in-memory SQLite database so no files are written.
"""

import sqlite3
from datetime import datetime, timedelta

import pytest

from assistant.storage import (
    init_db,
    save_message,
    get_recent_messages,
    create_task,
    get_tasks,
    update_task_status,
    delete_task,
    create_reminder,
    get_pending_reminders,
    get_user_reminders,
    mark_reminder_sent,
    create_event,
    get_events,
)

USER_A = 111
USER_B = 222


@pytest.fixture
def conn():
    """Return a fresh in-memory DB connection for each test."""
    return init_db(":memory:")


# ── Messages ──────────────────────────────────────────────────────────────────

class TestMessages:
    def test_save_and_retrieve(self, conn):
        save_message(conn, USER_A, "Hello")
        save_message(conn, USER_A, "World", role="assistant")
        msgs = get_recent_messages(conn, USER_A)
        assert len(msgs) == 2
        assert msgs[0]["text"] == "Hello"
        assert msgs[1]["role"] == "assistant"

    def test_limit(self, conn):
        for i in range(10):
            save_message(conn, USER_A, f"msg {i}")
        msgs = get_recent_messages(conn, USER_A, limit=5)
        assert len(msgs) == 5

    def test_user_isolation(self, conn):
        save_message(conn, USER_A, "A's message")
        save_message(conn, USER_B, "B's message")
        a_msgs = get_recent_messages(conn, USER_A)
        b_msgs = get_recent_messages(conn, USER_B)
        assert len(a_msgs) == 1
        assert len(b_msgs) == 1
        assert a_msgs[0]["text"] == "A's message"


# ── Tasks ─────────────────────────────────────────────────────────────────────

class TestTasks:
    def test_create_and_retrieve(self, conn):
        task_id = create_task(conn, USER_A, "Write report", priority=1, category="business")
        tasks = get_tasks(conn, USER_A)
        assert len(tasks) == 1
        assert tasks[0]["id"] == task_id
        assert tasks[0]["title"] == "Write report"
        assert tasks[0]["priority"] == 1
        assert tasks[0]["category"] == "business"
        assert tasks[0]["status"] == "pending"

    def test_status_filter(self, conn):
        create_task(conn, USER_A, "Task A")
        tid2 = create_task(conn, USER_A, "Task B")
        update_task_status(conn, tid2, "done")

        pending = get_tasks(conn, USER_A, status="pending")
        done = get_tasks(conn, USER_A, status="done")
        assert len(pending) == 1
        assert len(done) == 1

    def test_update_status(self, conn):
        tid = create_task(conn, USER_A, "My task")
        assert update_task_status(conn, tid, "in_progress") is True
        tasks = get_tasks(conn, USER_A)
        assert tasks[0]["status"] == "in_progress"

    def test_update_nonexistent_returns_false(self, conn):
        assert update_task_status(conn, 9999, "done") is False

    def test_delete_task(self, conn):
        tid = create_task(conn, USER_A, "To delete")
        assert delete_task(conn, tid) is True
        assert get_tasks(conn, USER_A) == []

    def test_delete_nonexistent_returns_false(self, conn):
        assert delete_task(conn, 9999) is False

    def test_user_isolation(self, conn):
        create_task(conn, USER_A, "A's task")
        create_task(conn, USER_B, "B's task")
        assert len(get_tasks(conn, USER_A)) == 1
        assert len(get_tasks(conn, USER_B)) == 1

    def test_deadline_stored(self, conn):
        deadline = "2030-12-31T23:59:59"
        tid = create_task(conn, USER_A, "Future task", deadline=deadline)
        tasks = get_tasks(conn, USER_A)
        assert tasks[0]["deadline"] == deadline


# ── Reminders ─────────────────────────────────────────────────────────────────

class TestReminders:
    def test_create_and_retrieve(self, conn):
        future = datetime.utcnow() + timedelta(hours=1)
        rid = create_reminder(conn, USER_A, "Call Bob", future)
        reminders = get_user_reminders(conn, USER_A)
        assert len(reminders) == 1
        assert reminders[0]["id"] == rid
        assert reminders[0]["message"] == "Call Bob"
        assert reminders[0]["sent"] == 0

    def test_pending_reminders_only_past_due(self, conn):
        past = datetime.utcnow() - timedelta(minutes=1)
        future = datetime.utcnow() + timedelta(hours=2)
        create_reminder(conn, USER_A, "Past reminder", past)
        create_reminder(conn, USER_A, "Future reminder", future)
        pending = get_pending_reminders(conn)
        assert len(pending) == 1
        assert pending[0]["message"] == "Past reminder"

    def test_mark_sent(self, conn):
        past = datetime.utcnow() - timedelta(minutes=5)
        rid = create_reminder(conn, USER_A, "Hello", past)
        mark_reminder_sent(conn, rid)
        pending = get_pending_reminders(conn)
        assert pending == []

    def test_include_sent(self, conn):
        past = datetime.utcnow() - timedelta(minutes=5)
        rid = create_reminder(conn, USER_A, "Hello", past)
        mark_reminder_sent(conn, rid)
        all_reminders = get_user_reminders(conn, USER_A, include_sent=True)
        assert len(all_reminders) == 1

    def test_task_link(self, conn):
        tid = create_task(conn, USER_A, "Linked task")
        future = datetime.utcnow() + timedelta(hours=1)
        rid = create_reminder(conn, USER_A, "Don't forget", future, task_id=tid)
        reminders = get_user_reminders(conn, USER_A)
        assert reminders[0]["task_id"] == tid


# ── Calendar events ───────────────────────────────────────────────────────────

class TestCalendarEvents:
    def test_create_and_retrieve(self, conn):
        start = datetime(2030, 6, 15, 10, 0)
        end = datetime(2030, 6, 15, 11, 0)
        eid = create_event(conn, USER_A, "Team meeting", start, end, "Weekly sync")
        events = get_events(conn, USER_A)
        assert len(events) == 1
        assert events[0]["id"] == eid
        assert events[0]["title"] == "Team meeting"

    def test_from_dt_filter(self, conn):
        past = datetime(2020, 1, 1, 9, 0)
        future = datetime(2030, 1, 1, 9, 0)
        create_event(conn, USER_A, "Old event", past)
        create_event(conn, USER_A, "New event", future)
        events = get_events(conn, USER_A, from_dt=datetime(2025, 1, 1))
        assert len(events) == 1
        assert events[0]["title"] == "New event"

    def test_user_isolation(self, conn):
        start = datetime(2030, 1, 1, 9, 0)
        create_event(conn, USER_A, "A's event", start)
        create_event(conn, USER_B, "B's event", start)
        assert len(get_events(conn, USER_A)) == 1
        assert len(get_events(conn, USER_B)) == 1
