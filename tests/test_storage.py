"""
เทสต์ชั้นเก็บข้อมูลของ bot.py — init_db, โน้ต และค่าตั้งของผู้ใช้

(ไฟล์เดิมเทสต์ assistant/storage.py ซึ่งถูกลบไปแล้ว เนื้อหาจึงเขียนใหม่ให้ตรง
กับโค้ดที่ยังอยู่จริงใน bot.py)
"""

import unittest

from tests._bot_case import BotDbCase, bot

USER_A = 111
USER_B = 222


class TestInitDb(BotDbCase):
    async def test_creates_every_table(self):
        names = {row["name"] for row in self.rows("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(
            {"tasks", "reminders", "notes", "user_storage_settings", "transcriptions"} - names,
            set(),
        )

    async def test_is_idempotent(self):
        await bot.Storage.add_task(USER_A, "งานที่ต้องอยู่รอด")
        bot.init_db()  # เรียกซ้ำตอนบูตใหม่ ต้องไม่ล้างของเดิม
        self.assertEqual(len(await bot.Storage.get_tasks(USER_A)), 1)


class TestNotes(BotDbCase):
    async def test_add_and_read_back(self):
        note_id = await bot.Storage.add_note(USER_A, "ไอเดียร้าน", tags="idea")
        notes = await bot.Storage.get_notes(USER_A)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["id"], note_id)
        self.assertEqual(notes[0]["content"], "ไอเดียร้าน")
        self.assertEqual(notes[0]["tags"], "idea")

    async def test_notes_are_per_user(self):
        await bot.Storage.add_note(USER_A, "ของ A")
        await bot.Storage.add_note(USER_B, "ของ B")
        self.assertEqual([n["content"] for n in await bot.Storage.get_notes(USER_A)], ["ของ A"])

    async def test_tags_are_optional(self):
        await bot.Storage.add_note(USER_A, "ไม่มีแท็ก")
        self.assertIsNone((await bot.Storage.get_notes(USER_A))[0]["tags"])


class TestStorageSettings(BotDbCase):
    def test_defaults_for_an_unknown_user(self):
        settings = bot.StorageSettings.get_settings(USER_A)
        self.assertEqual(settings["storage_type"], "local")
        self.assertEqual(settings["preferred_language"], "en")

    def test_set_storage_type(self):
        bot.StorageSettings.set_storage_type(USER_A, "airtable")
        self.assertEqual(bot.StorageSettings.get_settings(USER_A)["storage_type"], "airtable")

    def test_airtable_credentials_round_trip(self):
        bot.StorageSettings.set_airtable(USER_A, "key123", "appABC", "Jobs")
        settings = bot.StorageSettings.get_settings(USER_A)
        self.assertEqual(settings["storage_type"], "airtable")
        self.assertEqual(settings["airtable_api_key"], "key123")
        self.assertEqual(settings["airtable_base_id"], "appABC")
        self.assertEqual(settings["airtable_table_name"], "Jobs")

    def test_table_name_falls_back_to_tasks(self):
        bot.StorageSettings.set_airtable(USER_A, "key", "app")
        self.assertEqual(bot.StorageSettings.get_settings(USER_A)["airtable_table_name"], "Tasks")

    def test_switching_backend_keeps_one_row_per_user(self):
        bot.StorageSettings.set_airtable(USER_A, "key", "app")
        bot.StorageSettings.set_google_sheets(USER_A, "sheet-1")
        settings = bot.StorageSettings.get_settings(USER_A)
        self.assertEqual(settings["storage_type"], "sheets")
        self.assertEqual(settings["google_sheet_id"], "sheet-1")
        self.assertEqual(len(self.rows("SELECT user_id FROM user_storage_settings")), 1)

    def test_settings_are_per_user(self):
        bot.StorageSettings.set_storage_type(USER_A, "airtable")
        self.assertEqual(bot.StorageSettings.get_settings(USER_B)["storage_type"], "local")


if __name__ == "__main__":
    unittest.main()
