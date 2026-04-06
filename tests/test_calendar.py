"""
Tests for assistant/calendar_integration.py — event management and iCal export.
"""

import pytest
from datetime import datetime, timedelta
from pathlib import Path

from assistant.storage import init_db
from assistant.calendar_integration import (
    add_event,
    get_upcoming_events,
    format_event,
    format_event_list,
    export_ical,
)

USER = 33


@pytest.fixture
def conn():
    return init_db(":memory:")


@pytest.fixture(autouse=True)
def tmp_exports(tmp_path, monkeypatch):
    """Redirect EXPORTS_DIR to a temp path so tests don't write to the project."""
    import config
    monkeypatch.setattr(config, "EXPORTS_DIR", str(tmp_path))


class TestAddEvent:
    def test_basic(self, conn):
        start = datetime(2030, 6, 15, 10, 0)
        event = add_event(conn, USER, "Sprint review", start)
        assert event["id"] is not None
        assert event["title"] == "Sprint review"
        assert "start_time" in event
        assert "end_time" in event  # defaults to start + 1 hour

    def test_default_end_time_is_one_hour_later(self, conn):
        start = datetime(2030, 3, 10, 14, 0)
        event = add_event(conn, USER, "Standup", start)
        end = datetime.fromisoformat(event["end_time"])
        expected_end = start + timedelta(hours=1)
        assert end == expected_end

    def test_custom_end_time(self, conn):
        start = datetime(2030, 3, 10, 14, 0)
        end = datetime(2030, 3, 10, 16, 0)
        event = add_event(conn, USER, "Workshop", start, end)
        assert datetime.fromisoformat(event["end_time"]) == end


class TestGetUpcomingEvents:
    def test_returns_only_future_events(self, conn):
        past = datetime.utcnow() - timedelta(days=2)
        future = datetime.utcnow() + timedelta(days=3)
        add_event(conn, USER, "Past event", past)
        add_event(conn, USER, "Future event", future)
        upcoming = get_upcoming_events(conn, USER, days=7)
        titles = [e["title"] for e in upcoming]
        assert "Future event" in titles
        assert "Past event" not in titles

    def test_cutoff_respects_days_param(self, conn):
        in_3_days = datetime.utcnow() + timedelta(days=3)
        in_14_days = datetime.utcnow() + timedelta(days=14)
        add_event(conn, USER, "Soon event", in_3_days)
        add_event(conn, USER, "Later event", in_14_days)

        within_7 = get_upcoming_events(conn, USER, days=7)
        within_30 = get_upcoming_events(conn, USER, days=30)
        assert len(within_7) == 1
        assert len(within_30) == 2


class TestFormatEvent:
    def test_includes_title_and_times(self, conn):
        start = datetime(2030, 6, 15, 10, 0)
        event = add_event(conn, USER, "Team lunch", start)
        text = format_event(event)
        assert "Team lunch" in text
        assert "2030" in text

    def test_includes_location(self, conn):
        start = datetime(2030, 6, 15, 10, 0)
        event = add_event(conn, USER, "Offsite", start, location="Conference Room A")
        text = format_event(event)
        assert "Conference Room A" in text


class TestFormatEventList:
    def test_empty(self):
        text = format_event_list([])
        assert "No" in text

    def test_non_empty(self, conn):
        start = datetime(2030, 6, 15, 10, 0)
        e1 = add_event(conn, USER, "Meeting A", start)
        e2 = add_event(conn, USER, "Meeting B", start + timedelta(hours=2))
        text = format_event_list([e1, e2])
        assert "Meeting A" in text
        assert "Meeting B" in text


class TestExportIcal:
    def test_creates_file(self, tmp_path):
        events = [
            {
                "title": "Test event",
                "start_time": "2030-06-15T10:00:00",
                "end_time": "2030-06-15T11:00:00",
                "description": "A test",
                "location": "",
            }
        ]
        filepath = export_ical(events, filename="test_export.ics")
        assert filepath.exists()
        assert filepath.suffix == ".ics"

    def test_file_contains_event_title(self, tmp_path):
        events = [
            {
                "title": "My Special Meeting",
                "start_time": "2030-07-01T09:00:00",
                "end_time": "2030-07-01T10:00:00",
                "description": "",
                "location": "",
            }
        ]
        filepath = export_ical(events, filename="check.ics")
        content = filepath.read_bytes().decode("utf-8")
        assert "My Special Meeting" in content

    def test_empty_events_creates_empty_calendar(self, tmp_path):
        filepath = export_ical([], filename="empty.ics")
        assert filepath.exists()
