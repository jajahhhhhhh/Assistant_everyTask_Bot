"""
Tests for assistant/analytics.py — progress reports and recommendations.
"""

import pytest
from datetime import datetime, timedelta

from assistant.storage import init_db, create_task, update_task_status, create_event
from assistant.analytics import task_progress_report, weekly_summary, recommendations

USER = 55


@pytest.fixture
def conn():
    return init_db(":memory:")


class TestTaskProgressReport:
    def test_no_tasks(self, conn):
        report = task_progress_report(conn, USER)
        assert "No tasks" in report

    def test_counts_statuses(self, conn):
        t1 = create_task(conn, USER, "Task 1")
        t2 = create_task(conn, USER, "Task 2")
        t3 = create_task(conn, USER, "Task 3")
        update_task_status(conn, t1, "done")
        update_task_status(conn, t2, "in_progress")
        report = task_progress_report(conn, USER)
        assert "Done" in report or "done" in report.lower()
        assert "1" in report  # at least one done

    def test_completion_rate(self, conn):
        tid = create_task(conn, USER, "Only task")
        update_task_status(conn, tid, "done")
        report = task_progress_report(conn, USER)
        assert "100%" in report

    def test_overdue_highlighted(self, conn):
        past_deadline = (datetime.utcnow() - timedelta(days=1)).isoformat()
        create_task(conn, USER, "Overdue task", deadline=past_deadline)
        report = task_progress_report(conn, USER)
        assert "Overdue" in report or "overdue" in report.lower()


class TestWeeklySummary:
    def test_returns_string(self, conn):
        result = weekly_summary(conn, USER)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_task_counts(self, conn):
        create_task(conn, USER, "New task")
        result = weekly_summary(conn, USER)
        assert "1" in result

    def test_includes_upcoming_events(self, conn):
        future = datetime.utcnow() + timedelta(days=3)
        create_event(conn, USER, "Big conference", future)
        result = weekly_summary(conn, USER)
        assert "Big conference" in result


class TestRecommendations:
    def test_no_tasks_returns_clear(self, conn):
        result = recommendations(conn, USER)
        assert "clear" in result.lower() or "great" in result.lower()

    def test_urgent_tasks_highlighted(self, conn):
        create_task(conn, USER, "Critical bug fix", category="urgent", priority=1)
        result = recommendations(conn, USER)
        assert "urgent" in result.lower() or "🚨" in result

    def test_many_high_priority_warning(self, conn):
        for i in range(6):
            create_task(conn, USER, f"High prio task {i}", priority=1)
        result = recommendations(conn, USER)
        assert "high" in result.lower() or "delegat" in result.lower()
