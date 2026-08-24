"""Tests for reminder storage and delivery.

Before the scheduler was wired up, reminders were written to the database and
listed by /reminders but never actually sent, so these cover both halves.
"""

from datetime import datetime, timedelta

import pytest

USER = 42
OTHER = 99


@pytest.fixture
def now():
    return datetime(2026, 8, 24, 12, 0, 0)


class TestReminderStorage:
    async def test_add_and_list(self, bot, now):
        reminder_id = await bot.Storage.add_reminder(USER, "call mom", now + timedelta(hours=1))
        assert isinstance(reminder_id, int)

        reminders = await bot.Storage.get_reminders(USER)
        assert [r["text"] for r in reminders] == ["call mom"]
        assert reminders[0]["status"] == "pending"

    async def test_listed_in_chronological_order(self, bot, now):
        await bot.Storage.add_reminder(USER, "later", now + timedelta(hours=5))
        await bot.Storage.add_reminder(USER, "sooner", now + timedelta(hours=1))
        assert [r["text"] for r in await bot.Storage.get_reminders(USER)] == ["sooner", "later"]

    async def test_scoped_per_user(self, bot, now):
        await bot.Storage.add_reminder(OTHER, "theirs", now + timedelta(hours=1))
        assert await bot.Storage.get_reminders(USER) == []

    async def test_sent_reminders_drop_off_the_list(self, bot, now):
        reminder_id = await bot.Storage.add_reminder(USER, "done", now - timedelta(minutes=1))
        await bot.Storage.mark_reminder_sent(reminder_id)
        assert await bot.Storage.get_reminders(USER) == []


class TestDueReminders:
    async def test_only_returns_reminders_in_the_past(self, bot, now):
        await bot.Storage.add_reminder(USER, "due", now - timedelta(minutes=1))
        await bot.Storage.add_reminder(USER, "not yet", now + timedelta(minutes=1))

        due = await bot.Storage.get_due_reminders(now)
        assert [r["text"] for r in due] == ["due"]

    async def test_includes_every_user(self, bot, now):
        await bot.Storage.add_reminder(USER, "mine", now - timedelta(minutes=1))
        await bot.Storage.add_reminder(OTHER, "theirs", now - timedelta(minutes=2))

        due = await bot.Storage.get_due_reminders(now)
        assert {r["user_id"] for r in due} == {USER, OTHER}

    async def test_carries_the_user_id_for_delivery(self, bot, now):
        await bot.Storage.add_reminder(USER, "ping", now - timedelta(minutes=1))
        assert (await bot.Storage.get_due_reminders(now))[0]["user_id"] == USER

    async def test_already_sent_are_excluded(self, bot, now):
        reminder_id = await bot.Storage.add_reminder(USER, "ping", now - timedelta(minutes=1))
        await bot.Storage.mark_reminder_sent(reminder_id)
        assert await bot.Storage.get_due_reminders(now) == []

    async def test_mark_sent_is_not_repeatable(self, bot, now):
        reminder_id = await bot.Storage.add_reminder(USER, "ping", now - timedelta(minutes=1))
        assert await bot.Storage.mark_reminder_sent(reminder_id) is True
        assert await bot.Storage.mark_reminder_sent(reminder_id) is False


class TestDeliverDueReminders:
    async def test_sends_due_reminders_to_the_right_chat(self, bot, now, FakeBot):
        await bot.Storage.add_reminder(USER, "call mom", now - timedelta(minutes=1))
        fake_bot = FakeBot()

        assert await bot.deliver_due_reminders(fake_bot, now) == 1
        assert len(fake_bot.sent) == 1
        assert fake_bot.sent[0]["chat_id"] == USER
        assert "call mom" in fake_bot.sent[0]["text"]

    async def test_does_not_send_reminders_that_are_not_due(self, bot, now, FakeBot):
        await bot.Storage.add_reminder(USER, "future", now + timedelta(hours=1))
        fake_bot = FakeBot()

        assert await bot.deliver_due_reminders(fake_bot, now) == 0
        assert fake_bot.sent == []

    async def test_a_reminder_is_delivered_exactly_once(self, bot, now, FakeBot):
        await bot.Storage.add_reminder(USER, "once", now - timedelta(minutes=1))
        fake_bot = FakeBot()

        assert await bot.deliver_due_reminders(fake_bot, now) == 1
        assert await bot.deliver_due_reminders(fake_bot, now) == 0
        assert len(fake_bot.sent) == 1

    async def test_user_text_is_escaped(self, bot, now, FakeBot):
        await bot.Storage.add_reminder(USER, "ship user_id fix", now - timedelta(minutes=1))
        fake_bot = FakeBot()
        await bot.deliver_due_reminders(fake_bot, now)

        assert "user\\_id" in fake_bot.sent[0]["text"]

    async def test_failed_send_stays_pending_for_retry(self, bot, now, FakeBot):
        """A user who blocked the bot must not silently lose the reminder."""
        await bot.Storage.add_reminder(USER, "retry me", now - timedelta(minutes=1))
        blocked = FakeBot(fail_for=[USER])

        assert await bot.deliver_due_reminders(blocked, now) == 0
        assert [r["text"] for r in await bot.Storage.get_due_reminders(now)] == ["retry me"]

    async def test_one_failure_does_not_block_other_users(self, bot, now, FakeBot):
        await bot.Storage.add_reminder(USER, "blocked", now - timedelta(minutes=2))
        await bot.Storage.add_reminder(OTHER, "fine", now - timedelta(minutes=1))
        partial = FakeBot(fail_for=[USER])

        assert await bot.deliver_due_reminders(partial, now) == 1
        assert partial.sent[0]["chat_id"] == OTHER

    async def test_no_due_reminders_is_a_noop(self, bot, now, FakeBot):
        fake_bot = FakeBot()
        assert await bot.deliver_due_reminders(fake_bot, now) == 0


class TestSchedulerWiring:
    """The scheduler was imported but never started, so nothing ever swept
    the reminders table. Guard the wiring itself."""

    async def test_start_registers_the_sweep_job(self, bot):
        app = type("App", (), {"bot": None, "bot_data": {}})()
        await bot._start_scheduler(app)
        try:
            scheduler = app.bot_data["scheduler"]
            job = scheduler.get_job("deliver_due_reminders")
            assert job is not None
            assert job.func is bot.deliver_due_reminders
        finally:
            await bot._stop_scheduler(app)

    async def test_stop_releases_the_scheduler(self, bot):
        app = type("App", (), {"bot": None, "bot_data": {}})()
        await bot._start_scheduler(app)

        await bot._stop_scheduler(app)
        assert "scheduler" not in app.bot_data
        # calling it again must not raise
        await bot._stop_scheduler(app)

    async def test_stop_without_start_is_safe(self, bot):
        app = type("App", (), {"bot": None, "bot_data": {}})()
        await bot._stop_scheduler(app)


class TestPollSecondsParsing:
    """A bad REMINDER_POLL_SECONDS used to raise at import, killing the bot
    before the log could explain why."""

    def test_default_when_unset(self, bot, monkeypatch):
        monkeypatch.delenv("REMINDER_POLL_SECONDS", raising=False)
        assert bot._poll_seconds() == 30

    def test_reads_a_valid_value(self, bot, monkeypatch):
        monkeypatch.setenv("REMINDER_POLL_SECONDS", "5")
        assert bot._poll_seconds() == 5

    @pytest.mark.parametrize("raw", ["abc", "", "30s", "1.5"])
    def test_non_integer_falls_back_instead_of_raising(self, bot, monkeypatch, raw):
        monkeypatch.setenv("REMINDER_POLL_SECONDS", raw)
        assert bot._poll_seconds() == 30

    @pytest.mark.parametrize("raw", ["0", "-5"])
    def test_non_positive_falls_back(self, bot, monkeypatch, raw):
        monkeypatch.setenv("REMINDER_POLL_SECONDS", raw)
        assert bot._poll_seconds() == 30
