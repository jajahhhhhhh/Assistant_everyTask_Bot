"""Shared fixtures.

Every bot.py storage helper opens ``sqlite3.connect(DB_PATH)`` by reading the
module-level global at call time, so pointing ``bot.DB_PATH`` at a temporary
file is enough to isolate a test from the real database.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot as bot_module  # noqa: E402


@pytest.fixture
def bot(tmp_path, monkeypatch):
    """bot module wired to a throwaway SQLite database."""
    monkeypatch.setattr(bot_module, "DB_PATH", str(tmp_path / "test.db"))
    bot_module.init_db()
    bot_module.user_setup_state.clear()
    yield bot_module
    bot_module.user_setup_state.clear()


class FakeMessage:
    """Stands in for telegram.Message, recording what the handler sent."""

    def __init__(self, text="", voice=None):
        self.text = text
        self.voice = voice
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append({"text": text, **kwargs})
        return self.replies[-1]

    @property
    def last_reply(self):
        return self.replies[-1]["text"] if self.replies else None


class FakeBot:
    """Stands in for telegram.Bot, recording outbound messages."""

    def __init__(self, fail_for=()):
        self.sent = []
        self.fail_for = set(fail_for)

    async def send_message(self, chat_id, text, **kwargs):
        if chat_id in self.fail_for:
            raise RuntimeError(f"blocked by user {chat_id}")
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})
        return self.sent[-1]


class FakeUpdate:
    def __init__(self, user_id=42, text="", voice=None):
        self.effective_user = type("User", (), {"id": user_id, "first_name": "Tester"})()
        self.message = FakeMessage(text=text, voice=voice)


class FakeContext:
    def __init__(self, args=None, bot=None):
        self.args = args or []
        self.bot = bot or FakeBot()
        self.bot_data = {}


@pytest.fixture(name="FakeBot")
def fake_bot_class():
    """The FakeBot class itself; call with fail_for=[chat_id] to simulate a block."""
    return FakeBot


@pytest.fixture
def make():
    """Build (update, context) pairs for driving a handler."""

    def _make(args=None, text="", user_id=42, bot=None):
        return FakeUpdate(user_id=user_id, text=text), FakeContext(args=args, bot=bot)

    return _make


@pytest.fixture
def update():
    return FakeUpdate()


@pytest.fixture
def context():
    return FakeContext()
