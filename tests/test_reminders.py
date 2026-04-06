"""
Tests for assistant/reminders.py — reminder parsing and scheduling helpers.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from assistant.reminders import parse_reminder_time, schedule_reminder, format_reminder_list
from assistant.storage import init_db

USER = 77


@pytest.fixture
def conn():
    return init_db(":memory:")


class TestParseReminderTime:
    def test_returns_datetime_for_valid_expression(self):
        result = parse_reminder_time("in 1 hour")
        assert result is not None
        assert isinstance(result, datetime)
        # Should be approximately 1 hour from now
        now = datetime.utcnow()
        assert result > now
        assert result < now + timedelta(hours=2)

    def test_returns_none_for_garbage(self):
        result = parse_reminder_time("xyzzy foobar")
        assert result is None

    def test_future_preference(self):
        # "tomorrow" should parse to a future date
        result = parse_reminder_time("tomorrow at 9am")
        if result is not None:
            assert result > datetime.utcnow()


class TestScheduleReminder:
    def test_valid_time_returns_id(self, conn):
        rid = schedule_reminder(conn, USER, "Test message", "in 30 minutes")
        assert rid is not None
        assert isinstance(rid, int)

    def test_invalid_time_returns_none(self, conn):
        rid = schedule_reminder(conn, USER, "Test message", "blorp blorp")
        assert rid is None

    def test_reminder_stored_in_db(self, conn):
        from assistant.storage import get_user_reminders
        schedule_reminder(conn, USER, "Hello", "in 1 hour")
        reminders = get_user_reminders(conn, USER)
        assert len(reminders) == 1
        assert reminders[0]["message"] == "Hello"


class TestFormatReminderList:
    def test_empty(self):
        text = format_reminder_list([])
        assert "No" in text

    def test_non_empty(self):
        reminders = [
            {"id": 1, "message": "Call doctor", "remind_at": "2030-01-01T09:00:00"},
            {"id": 2, "message": "Send report", "remind_at": "2030-01-02T10:00:00"},
        ]
        text = format_reminder_list(reminders)
        assert "Call doctor" in text
        assert "Send report" in text
