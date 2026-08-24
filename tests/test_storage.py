"""Tests for the SQLite storage layer in bot.py."""

from datetime import datetime, timedelta

import pytest

USER = 42
OTHER = 99


class TestTasks:
    async def test_add_returns_id_and_persists(self, bot):
        task_id = await bot.Storage.add_task(USER, "Write tests")
        assert isinstance(task_id, int)

        tasks = await bot.Storage.get_tasks(USER)
        assert [t["title"] for t in tasks] == ["Write tests"]
        assert tasks[0]["status"] == "todo"
        assert tasks[0]["priority"] == "medium"

    async def test_priority_and_metadata_round_trip(self, bot):
        await bot.Storage.add_task(USER, "Ship it", priority="urgent",
                                   due_date="2026-01-01", project="apollo")
        task = (await bot.Storage.get_tasks(USER))[0]
        assert task["priority"] == "urgent"
        assert task["due_date"] == "2026-01-01"
        assert task["project"] == "apollo"

    async def test_tasks_are_scoped_per_user(self, bot):
        await bot.Storage.add_task(USER, "mine")
        await bot.Storage.add_task(OTHER, "theirs")
        assert [t["title"] for t in await bot.Storage.get_tasks(USER)] == ["mine"]
        assert [t["title"] for t in await bot.Storage.get_tasks(OTHER)] == ["theirs"]

    async def test_status_filter(self, bot):
        first = await bot.Storage.add_task(USER, "one")
        await bot.Storage.add_task(USER, "two")
        await bot.Storage.complete_task(USER, first)

        assert len(await bot.Storage.get_tasks(USER, status="todo")) == 1
        assert len(await bot.Storage.get_tasks(USER, status="done")) == 1

    async def test_complete_marks_done(self, bot):
        task_id = await bot.Storage.add_task(USER, "finish me")
        assert await bot.Storage.complete_task(USER, task_id) is True
        assert (await bot.Storage.get_tasks(USER))[0]["status"] == "done"

    async def test_complete_unknown_task_returns_false(self, bot):
        assert await bot.Storage.complete_task(USER, 12345) is False

    async def test_cannot_complete_another_users_task(self, bot):
        task_id = await bot.Storage.add_task(OTHER, "not yours")
        assert await bot.Storage.complete_task(USER, task_id) is False
        assert (await bot.Storage.get_tasks(OTHER))[0]["status"] == "todo"


class TestNotes:
    async def test_add_and_list(self, bot):
        note_id = await bot.Storage.add_note(USER, "remember the milk")
        assert isinstance(note_id, int)

        notes = await bot.Storage.get_notes(USER)
        assert notes[0]["content"] == "remember the milk"

    async def test_notes_are_scoped_per_user(self, bot):
        await bot.Storage.add_note(USER, "mine")
        assert await bot.Storage.get_notes(OTHER) == []

    async def test_empty_by_default(self, bot):
        assert await bot.Storage.get_notes(USER) == []


class TestStorageSettings:
    def test_defaults_for_unknown_user(self, bot):
        settings = bot.StorageSettings.get_settings(USER)
        assert settings["storage_type"] == "local"
        assert settings["preferred_language"] == "en"

    def test_set_storage_type_upserts(self, bot):
        bot.StorageSettings.set_storage_type(USER, "airtable")
        assert bot.StorageSettings.get_settings(USER)["storage_type"] == "airtable"

        bot.StorageSettings.set_storage_type(USER, "local")
        assert bot.StorageSettings.get_settings(USER)["storage_type"] == "local"

    def test_set_airtable(self, bot):
        bot.StorageSettings.set_airtable(USER, "patXXX", "appYYY", "Todos")
        settings = bot.StorageSettings.get_settings(USER)
        assert settings["storage_type"] == "airtable"
        assert settings["airtable_api_key"] == "patXXX"
        assert settings["airtable_base_id"] == "appYYY"
        assert settings["airtable_table_name"] == "Todos"

    def test_set_google_sheets(self, bot):
        bot.StorageSettings.set_google_sheets(USER, "sheet-123")
        settings = bot.StorageSettings.get_settings(USER)
        assert settings["storage_type"] == "sheets"
        assert settings["google_sheet_id"] == "sheet-123"

    def test_reset_to_local_clears_credentials(self, bot):
        bot.StorageSettings.set_airtable(USER, "patXXX", "appYYY")
        bot.StorageSettings.reset_to_local(USER)

        settings = bot.StorageSettings.get_settings(USER)
        assert settings["storage_type"] == "local"
        assert settings["airtable_api_key"] is None
        assert settings["airtable_base_id"] is None

    def test_set_language_preserves_storage_type(self, bot):
        bot.StorageSettings.set_airtable(USER, "patXXX", "appYYY")
        bot.StorageSettings.set_language(USER, "th")

        settings = bot.StorageSettings.get_settings(USER)
        assert settings["preferred_language"] == "th"
        assert settings["storage_type"] == "airtable"

    def test_settings_are_scoped_per_user(self, bot):
        bot.StorageSettings.set_language(USER, "ja")
        assert bot.StorageSettings.get_settings(OTHER)["preferred_language"] == "en"


class TestInitDb:
    async def test_is_idempotent_and_preserves_data(self, bot):
        await bot.Storage.add_task(USER, "survivor")
        bot.init_db()
        bot.init_db()
        assert [t["title"] for t in await bot.Storage.get_tasks(USER)] == ["survivor"]

    def test_creates_every_table(self, bot):
        import sqlite3

        conn = sqlite3.connect(bot.DB_PATH)
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        conn.close()
        assert {"tasks", "reminders", "notes",
                "user_storage_settings", "transcriptions"} <= names
