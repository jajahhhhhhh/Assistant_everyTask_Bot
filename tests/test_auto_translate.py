"""
เทสต์โหมดแปลอัตโนมัติ (/autotr) — สวิตช์ในฐานข้อมูล, บริการแปลที่รู้ว่า
"เป็นภาษานี้อยู่แล้ว", และ handle_message ที่ต้องแปลเฉพาะตอนเปิดสวิตช์
และต้องไม่แปลข้อความระหว่างตั้งค่า storage

ไม่มีเทสต์ไหนต่อ OpenAI จริง — สลับ bot.client เป็นตัวปลอมเหมือน test_translator
"""

import sqlite3
import unittest
from types import SimpleNamespace

from tests._bot_case import BotDbCase, bot
from tests.test_translator import FakeClient, FakeCompletions

USER = 7


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, text=""):
        self.effective_user = SimpleNamespace(id=USER, first_name="J")
        self.message = FakeMessage(text)


class FakeContext:
    def __init__(self, *args):
        self.args = list(args)


class AutoTranslateCase(BotDbCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self._original_client = bot.client
        self.completions = FakeCompletions(reply="สวัสดีโลก")
        bot.client = FakeClient(completions=self.completions)

    async def asyncTearDown(self):
        bot.client = self._original_client
        bot.user_setup_state.pop(USER, None)
        await super().asyncTearDown()


class TestSetting(AutoTranslateCase):
    async def test_defaults_to_off(self):
        self.assertFalse(bot.StorageSettings.get_settings(USER)["auto_translate"])
        bot.StorageSettings.set_language(USER, "th")
        self.assertFalse(bot.StorageSettings.get_settings(USER)["auto_translate"])

    async def test_set_and_clear(self):
        bot.StorageSettings.set_auto_translate(USER, True)
        self.assertTrue(bot.StorageSettings.get_settings(USER)["auto_translate"])
        bot.StorageSettings.set_auto_translate(USER, False)
        self.assertFalse(bot.StorageSettings.get_settings(USER)["auto_translate"])

    async def test_keeps_the_other_settings(self):
        bot.StorageSettings.set_language(USER, "ja")
        bot.StorageSettings.set_auto_translate(USER, True)
        settings = bot.StorageSettings.get_settings(USER)
        self.assertEqual(settings["preferred_language"], "ja")
        self.assertTrue(settings["auto_translate"])

    async def test_old_database_gains_the_column(self):
        """ฐานข้อมูลบน volume ที่สร้างก่อนฟีเจอร์นี้ไม่มีคอลัมน์ init_db ต้องเติมให้"""
        conn = sqlite3.connect(bot.DB_PATH)
        with conn:
            conn.execute("DROP TABLE user_storage_settings")
            conn.execute(
                "CREATE TABLE user_storage_settings ("
                " user_id INTEGER PRIMARY KEY, storage_type TEXT DEFAULT 'local',"
                " airtable_api_key TEXT, airtable_base_id TEXT, airtable_table_name TEXT,"
                " google_sheet_id TEXT, preferred_language TEXT DEFAULT 'en')"
            )
            conn.execute("INSERT INTO user_storage_settings (user_id, preferred_language) VALUES (?, 'th')", (USER,))
        conn.close()

        bot.init_db()
        settings = bot.StorageSettings.get_settings(USER)
        self.assertEqual(settings["preferred_language"], "th")
        self.assertFalse(settings["auto_translate"])


class TestCommand(AutoTranslateCase):
    async def run_command(self, *args):
        update = FakeUpdate()
        await bot.autotr_command(update, FakeContext(*args))
        return update.message.replies

    async def test_on_and_off(self):
        replies = await self.run_command("on")
        self.assertTrue(bot.StorageSettings.get_settings(USER)["auto_translate"])
        self.assertIn("ON", replies[-1])

        replies = await self.run_command("off")
        self.assertFalse(bot.StorageSettings.get_settings(USER)["auto_translate"])
        self.assertIn("OFF", replies[-1])

    async def test_no_argument_toggles(self):
        await self.run_command()
        self.assertTrue(bot.StorageSettings.get_settings(USER)["auto_translate"])
        await self.run_command()
        self.assertFalse(bot.StorageSettings.get_settings(USER)["auto_translate"])

    async def test_status_names_the_target_language(self):
        bot.StorageSettings.set_language(USER, "th")
        replies = await self.run_command("on")
        self.assertIn(bot.LANGUAGES["th"], replies[-1])

    async def test_unknown_argument_changes_nothing(self):
        replies = await self.run_command("maybe")
        self.assertFalse(bot.StorageSettings.get_settings(USER)["auto_translate"])
        self.assertIn("Usage", replies[-1])


class TestService(AutoTranslateCase):
    async def test_translates(self):
        self.assertEqual(await bot.auto_translate_text("Hello world", "th"), "สวัสดีโลก")
        system_prompt = self.completions.calls[0]["messages"][0]["content"]
        self.assertIn(bot.LANGUAGES["th"], system_prompt)
        self.assertIn(bot._ALREADY_TARGET, system_prompt)

    async def test_already_in_target_language_returns_none(self):
        for reply in ("SAME", " same\n", "`SAME`", "SAME."):
            self.completions.reply = reply
            self.assertIsNone(await bot.auto_translate_text("สวัสดี", "th"), reply)

    async def test_translation_containing_the_sentinel_word_is_kept(self):
        self.completions.reply = "It is the SAME house"
        self.assertEqual(await bot.auto_translate_text("มันคือบ้านหลังเดิม", "en"), "It is the SAME house")

    async def test_api_errors_are_reported_without_leaking_the_detail(self):
        bot.client = FakeClient(completions=FakeCompletions(error=RuntimeError("rate limited")))
        with self.assertLogs("bot", level="ERROR"):
            result = await bot.auto_translate_text("Hello", "th")
        self.assertIsNotNone(result)
        self.assertNotIn("rate limited", result)

    async def test_without_a_configured_client(self):
        bot.client = None
        self.assertIn("not configured", await bot.auto_translate_text("Hello", "th"))


class TestHandleMessage(AutoTranslateCase):
    async def send(self, text):
        update = FakeUpdate(text)
        await bot.handle_message(update, FakeContext())
        return update.message.replies

    async def test_off_by_default_shows_the_hint_and_never_calls_the_model(self):
        replies = await self.send("Hello world")
        self.assertEqual(self.completions.calls, [])
        self.assertIn("/autotr", replies[-1])

    async def test_on_translates_to_the_preferred_language(self):
        bot.StorageSettings.set_language(USER, "th")
        bot.StorageSettings.set_auto_translate(USER, True)

        replies = await self.send("Hello world")
        self.assertEqual(self.completions.calls[0]["messages"][1]["content"], "Hello world")
        self.assertIn(bot.LANGUAGES["th"], self.completions.calls[0]["messages"][0]["content"])
        self.assertEqual(len(replies), 1)
        self.assertIn("สวัสดีโลก", replies[0])

    async def test_on_with_text_already_in_the_language(self):
        bot.StorageSettings.set_language(USER, "th")
        bot.StorageSettings.set_auto_translate(USER, True)
        self.completions.reply = "SAME"

        replies = await self.send("สวัสดีครับ")
        self.assertEqual(len(replies), 1)
        self.assertIn("อยู่แล้ว", replies[0])
        self.assertNotIn("SAME", replies[0])

    async def test_setup_mode_wins_over_auto_translate(self):
        """คีย์ Airtable ที่พิมพ์ระหว่าง /settings ต้องไปเข้าขั้นตอนตั้งค่า ไม่ใช่ถูกแปล"""
        bot.StorageSettings.set_auto_translate(USER, True)
        bot.user_setup_state[USER] = {"type": "airtable", "step": 1}

        replies = await self.send("patXXXXXXXX")
        self.assertEqual(self.completions.calls, [])
        self.assertEqual(bot.user_setup_state[USER]["api_key"], "patXXXXXXXX")
        self.assertIn("Step 2/3", replies[-1])


if __name__ == "__main__":
    unittest.main()


class TestEditedMessagesAreIgnored(unittest.TestCase):
    """การแก้ไขข้อความที่ส่งไปแล้วต้องไม่เข้า handle_message

    MessageHandler ค่าเริ่มต้นจับ edited_message ด้วย ซึ่ง update.message เป็น
    None แล้ว handle_message ล้มด้วย AttributeError — เห็นใน log บน Railway
    ทันทีที่ผู้ใช้แก้ข้อความหลังเปิด /autotr
    """

    def build(self):
        original = bot.BOT_TOKEN
        bot.BOT_TOKEN = "123456:AAFakeTokenForTestingOnly"
        try:
            return bot.build_application()
        finally:
            bot.BOT_TOKEN = original

    def message_handlers(self):
        from telegram.ext import MessageHandler

        return [
            handler
            for group in self.build().handlers.values()
            for handler in group
            if isinstance(handler, MessageHandler)
        ]

    def test_every_message_handler_skips_edits(self):
        import datetime

        from telegram import Chat, Message, Update, User

        handlers = self.message_handlers()
        self.assertGreaterEqual(len(handlers), 3)

        new = Message(
            message_id=1,
            date=datetime.datetime.now(datetime.timezone.utc),
            chat=Chat(id=USER, type="private"),
            from_user=User(id=USER, first_name="J", is_bot=False),
            text="Hello",
        )
        for handler in handlers:
            fresh = Update(update_id=1, message=new)
            edited = Update(update_id=2, edited_message=new)
            self.assertFalse(
                handler.check_update(edited), f"{handler.callback.__name__} รับ edited_message"
            )
            if handler.callback is bot.handle_message:
                self.assertTrue(handler.check_update(fresh))
