"""
เทสต์การเตือนใน bot.py — การอ่านช่วงเวลา และการเก็บลงฐานข้อมูล

(ไฟล์เดิมเทสต์ assistant/reminders.py ซึ่งถูกลบไปแล้ว ตัวอ่านช่วงเวลาเคยฝังอยู่
ใน remind_command จึงถูกแยกออกมาเป็น bot.parse_duration ให้ทดสอบได้)
"""

import unittest
from datetime import datetime, timedelta

from tests._bot_case import BotDbCase, bot

USER_A = 111
USER_B = 222


class TestParseDuration(unittest.TestCase):
    def test_minutes_hours_days(self):
        self.assertEqual(bot.parse_duration("30m"), timedelta(minutes=30))
        self.assertEqual(bot.parse_duration("2h"), timedelta(hours=2))
        self.assertEqual(bot.parse_duration("1d"), timedelta(days=1))

    def test_case_and_padding(self):
        self.assertEqual(bot.parse_duration(" 45M "), timedelta(minutes=45))

    def test_rejects_zero_and_negatives(self):
        self.assertIsNone(bot.parse_duration("0m"))
        self.assertIsNone(bot.parse_duration("-5m"))

    def test_rejects_garbage(self):
        for value in ("", "abc", "30", "m30", "30x", "1.5h", "30 minutes", None):
            self.assertIsNone(bot.parse_duration(value), value)

    def test_large_values_still_parse(self):
        self.assertEqual(bot.parse_duration("999d"), timedelta(days=999))


class TestReminderStorage(BotDbCase):
    async def test_add_and_list(self):
        when = datetime(2026, 9, 1, 8, 30)
        reminder_id = await bot.Storage.add_reminder(USER_A, "โทรหาแม่", when)

        reminders = await bot.Storage.get_reminders(USER_A)
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0]["id"], reminder_id)
        self.assertEqual(reminders[0]["text"], "โทรหาแม่")
        self.assertEqual(reminders[0]["remind_at"], when.isoformat())
        self.assertEqual(reminders[0]["status"], "pending")

    async def test_sorted_by_time(self):
        base = datetime(2026, 9, 1, 8, 0)
        await bot.Storage.add_reminder(USER_A, "ทีหลัง", base + timedelta(hours=3))
        await bot.Storage.add_reminder(USER_A, "ก่อน", base)
        self.assertEqual([r["text"] for r in await bot.Storage.get_reminders(USER_A)], ["ก่อน", "ทีหลัง"])

    async def test_only_pending_are_listed(self):
        reminder_id = await bot.Storage.add_reminder(USER_A, "ส่งไปแล้ว", datetime(2026, 9, 1))
        conn_rows = self.rows("SELECT id FROM reminders")
        self.assertEqual(len(conn_rows), 1)

        import sqlite3

        conn = sqlite3.connect(bot.DB_PATH)
        conn.execute("UPDATE reminders SET status = 'sent' WHERE id = ?", (reminder_id,))
        conn.commit()
        conn.close()

        self.assertEqual(await bot.Storage.get_reminders(USER_A), [])

    async def test_reminders_are_per_user(self):
        await bot.Storage.add_reminder(USER_A, "ของ A", datetime(2026, 9, 1))
        self.assertEqual(await bot.Storage.get_reminders(USER_B), [])


if __name__ == "__main__":
    unittest.main()
