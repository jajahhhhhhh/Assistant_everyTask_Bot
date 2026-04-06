"""
Tests for assistant/tasks.py — task management helpers.
"""

import pytest
import sqlite3

from assistant.storage import init_db
from assistant.tasks import (
    add_task,
    format_task,
    format_task_list,
    task_statistics,
)

USER = 42


@pytest.fixture
def conn():
    return init_db(":memory:")


class TestAddTask:
    def test_basic(self, conn):
        task = add_task(conn, USER, "Write tests")
        assert task["id"] is not None
        assert task["title"] == "Write tests"
        assert task["status"] == "pending"
        assert task["priority"] == 2
        assert task["category"] == "general"

    def test_custom_category_priority(self, conn):
        task = add_task(conn, USER, "Urgent report", category="business", priority=1)
        assert task["category"] == "business"
        assert task["priority"] == 1

    def test_deadline_stored(self, conn):
        task = add_task(conn, USER, "Deadline task", deadline="2030-01-01")
        assert task["deadline"] == "2030-01-01"


class TestFormatTask:
    def test_format_includes_title(self, conn):
        task = add_task(conn, USER, "Review PR")
        text = format_task(task)
        assert "Review PR" in text

    def test_format_includes_category(self, conn):
        task = add_task(conn, USER, "Invoice", category="business")
        text = format_task(task)
        assert "Business" in text or "business" in text

    def test_format_includes_priority(self, conn):
        task = add_task(conn, USER, "Rush job", priority=1)
        text = format_task(task)
        assert "High" in text

    def test_format_with_description(self, conn):
        task = add_task(conn, USER, "Call", description="Call Alice about the project")
        text = format_task(task)
        assert "Call Alice" in text

    def test_format_with_deadline(self, conn):
        task = add_task(conn, USER, "Submit", deadline="2030-12-31")
        text = format_task(task)
        assert "2030-12-31" in text


class TestFormatTaskList:
    def test_empty_list(self):
        text = format_task_list([])
        assert "No tasks" in text

    def test_non_empty_list(self, conn):
        t1 = add_task(conn, USER, "Task 1")
        t2 = add_task(conn, USER, "Task 2")
        text = format_task_list([t1, t2])
        assert "Task 1" in text
        assert "Task 2" in text


class TestTaskStatistics:
    def test_counts_by_status(self):
        tasks = [
            {"status": "pending"},
            {"status": "pending"},
            {"status": "done"},
            {"status": "cancelled"},
        ]
        stats = task_statistics(tasks)
        assert stats["pending"] == 2
        assert stats["done"] == 1
        assert stats["cancelled"] == 1

    def test_empty_returns_empty_dict(self):
        assert task_statistics([]) == {}
