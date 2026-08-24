"""Tests for the command handlers, driven through fake Update/Context objects."""

import pytest

USER = 42


class TestTaskCommand:
    async def test_adds_a_task_and_confirms(self, bot, make):
        update, context = make(args=["Buy", "groceries"])
        await bot.task_command(update, context)

        assert "Task Added" in update.message.last_reply
        assert [t["title"] for t in await bot.Storage.get_tasks(USER)] == ["Buy groceries"]

    async def test_without_args_shows_usage_and_saves_nothing(self, bot, make):
        update, context = make(args=[])
        await bot.task_command(update, context)

        assert "/task" in update.message.last_reply
        assert await bot.Storage.get_tasks(USER) == []

    async def test_underscored_title_is_escaped_in_the_reply(self, bot, make):
        """Regression: an unescaped _ made Telegram reject the confirmation."""
        update, context = make(args=["fix", "user_id", "handling"])
        await bot.task_command(update, context)

        reply = update.message.last_reply
        assert "user\\_id" in reply
        # the title itself is stored verbatim
        assert (await bot.Storage.get_tasks(USER))[0]["title"] == "fix user_id handling"

    async def test_priority_is_inferred(self, bot, make):
        update, context = make(args=["urgent", "call", "the", "bank"])
        await bot.task_command(update, context)
        assert (await bot.Storage.get_tasks(USER))[0]["priority"] == "urgent"

    async def test_innocent_title_stays_medium(self, bot, make):
        update, context = make(args=["Buy", "flowers"])
        await bot.task_command(update, context)
        assert (await bot.Storage.get_tasks(USER))[0]["priority"] == "medium"


class TestTasksCommand:
    async def test_empty_state(self, bot, make):
        update, context = make()
        await bot.tasks_command(update, context)
        assert "No tasks yet" in update.message.last_reply

    async def test_lists_pending_tasks(self, bot, make):
        await bot.Storage.add_task(USER, "first")
        await bot.Storage.add_task(USER, "second")

        update, context = make()
        await bot.tasks_command(update, context)

        reply = update.message.last_reply
        assert "first" in reply and "second" in reply

    async def test_titles_are_escaped(self, bot, make):
        await bot.Storage.add_task(USER, "fix user_id")
        update, context = make()
        await bot.tasks_command(update, context)
        assert "user\\_id" in update.message.last_reply

    async def test_completed_tasks_are_summarised(self, bot, make):
        task_id = await bot.Storage.add_task(USER, "done one")
        await bot.Storage.complete_task(USER, task_id)

        update, context = make()
        await bot.tasks_command(update, context)
        assert "Done" in update.message.last_reply


class TestDoneCommand:
    async def test_completes_a_task(self, bot, make):
        task_id = await bot.Storage.add_task(USER, "finish me")
        update, context = make(args=[str(task_id)])
        await bot.done_command(update, context)

        assert "completed" in update.message.last_reply
        assert (await bot.Storage.get_tasks(USER))[0]["status"] == "done"

    async def test_unknown_id_reports_not_found(self, bot, make):
        update, context = make(args=["999"])
        await bot.done_command(update, context)
        assert "not found" in update.message.last_reply

    async def test_non_numeric_id_is_rejected(self, bot, make):
        update, context = make(args=["abc"])
        await bot.done_command(update, context)
        assert "Invalid" in update.message.last_reply

    async def test_without_args_shows_usage(self, bot, make):
        update, context = make(args=[])
        await bot.done_command(update, context)
        assert "Usage" in update.message.last_reply


class TestRemindCommand:
    @pytest.mark.parametrize("spec", ["30m", "2h", "1d"])
    async def test_accepts_valid_durations(self, bot, make, spec):
        update, context = make(args=[spec, "call", "mom"])
        await bot.remind_command(update, context)

        assert "Reminder Set" in update.message.last_reply
        assert [r["text"] for r in await bot.Storage.get_reminders(USER)] == ["call mom"]

    @pytest.mark.parametrize("spec", ["soon", "30x", "m", "", "-5m", "0m", "1.5h"])
    async def test_rejects_bad_durations_without_storing(self, bot, make, spec):
        update, context = make(args=[spec, "call", "mom"])
        await bot.remind_command(update, context)

        assert "30m" in update.message.last_reply
        assert await bot.Storage.get_reminders(USER) == []

    async def test_reminder_text_is_escaped(self, bot, make):
        update, context = make(args=["1h", "ship", "user_id", "fix"])
        await bot.remind_command(update, context)
        assert "user\\_id" in update.message.last_reply

    async def test_missing_text_shows_usage(self, bot, make):
        update, context = make(args=["30m"])
        await bot.remind_command(update, context)

        assert "Set a reminder" in update.message.last_reply
        assert await bot.Storage.get_reminders(USER) == []


class TestNoteCommands:
    async def test_saves_a_note(self, bot, make):
        update, context = make(args=["remember", "the", "milk"])
        await bot.note_command(update, context)

        assert "Note Saved" in update.message.last_reply
        assert (await bot.Storage.get_notes(USER))[0]["content"] == "remember the milk"

    async def test_without_args_shows_usage(self, bot, make):
        update, context = make(args=[])
        await bot.note_command(update, context)
        assert "Usage" in update.message.last_reply

    async def test_notes_listing_escapes_content(self, bot, make):
        await bot.Storage.add_note(USER, "check user_id")
        update, context = make()
        await bot.notes_command(update, context)
        assert "user\\_id" in update.message.last_reply

    async def test_empty_notes_listing(self, bot, make):
        update, context = make()
        await bot.notes_command(update, context)
        assert "No notes yet" in update.message.last_reply


class TestTranslateCommand:
    async def test_unknown_language_is_rejected(self, bot, make):
        update, context = make(args=["xx", "hello"])
        await bot.translate_command(update, context)
        assert "Unknown language" in update.message.last_reply

    async def test_missing_args_shows_usage(self, bot, make):
        update, context = make(args=["th"])
        await bot.translate_command(update, context)
        assert "Usage" in update.message.last_reply


class TestCancelCommand:
    async def test_clears_an_in_progress_setup(self, bot, make):
        """Regression: /cancel lived in handle_message, which never sees
        commands, so a user in Airtable setup could not get out."""
        bot.user_setup_state[USER] = {"type": "airtable", "step": 1}

        update, context = make()
        await bot.cancel_command(update, context)

        assert "cancelled" in update.message.last_reply
        assert USER not in bot.user_setup_state

    async def test_with_nothing_in_progress(self, bot, make):
        update, context = make()
        await bot.cancel_command(update, context)

        assert "Nothing to cancel" in update.message.last_reply

    async def test_only_clears_the_calling_user(self, bot, make):
        bot.user_setup_state[USER] = {"type": "airtable", "step": 1}
        bot.user_setup_state[99] = {"type": "sheets", "step": 1}

        update, context = make()
        await bot.cancel_command(update, context)

        assert 99 in bot.user_setup_state


class TestMyStorageCommand:
    async def test_defaults_to_local(self, bot, make):
        update, context = make()
        await bot.mystorage_command(update, context)
        assert "Local" in update.message.last_reply

    async def test_airtable_shows_base_id(self, bot, make):
        bot.StorageSettings.set_airtable(USER, "patXXX", "appYYY")
        update, context = make()
        await bot.mystorage_command(update, context)

        reply = update.message.last_reply
        assert "appYYY" in reply
        # the API key must never be echoed back into the chat
        assert "patXXX" not in reply

    async def test_sheets_with_a_missing_id_does_not_crash(self, bot, make):
        """Regression: settings.get(key, 'N/A') returns None when the column
        is NULL, so slicing it raised TypeError."""
        import sqlite3

        conn = sqlite3.connect(bot.DB_PATH)
        conn.execute(
            "INSERT INTO user_storage_settings (user_id, storage_type, google_sheet_id)"
            " VALUES (?, 'sheets', NULL)", (USER,))
        conn.commit()
        conn.close()

        update, context = make()
        await bot.mystorage_command(update, context)
        assert "N/A" in update.message.last_reply


class TestHandleMessage:
    async def test_default_reply_outside_setup(self, bot, make):
        update, context = make(text="hello there")
        await bot.handle_message(update, context)
        assert "/task" in update.message.last_reply

    async def test_airtable_setup_advances_through_steps(self, bot, make):
        bot.user_setup_state[USER] = {"type": "airtable", "step": 1}

        update, context = make(text="patXXX")
        await bot.handle_message(update, context)
        assert bot.user_setup_state[USER]["step"] == 2
        assert bot.user_setup_state[USER]["api_key"] == "patXXX"

        update, context = make(text="appYYY")
        await bot.handle_message(update, context)
        assert bot.user_setup_state[USER]["step"] == 3
        assert bot.user_setup_state[USER]["base_id"] == "appYYY"
