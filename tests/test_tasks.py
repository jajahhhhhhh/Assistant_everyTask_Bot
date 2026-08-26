"""
เทสต์งานใน bot.py — Storage.add_task / get_tasks / complete_task

(ไฟล์เดิมเทสต์ assistant/tasks.py ซึ่งถูกลบไปแล้ว)
"""

import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests._bot_case import BotDbCase, bot

import line_webhook

USER_A = 111
USER_B = 222


class TestAddTask(BotDbCase):
    async def test_defaults(self):
        task_id = await bot.Storage.add_task(USER_A, "เขียนเทสต์")
        task = (await bot.Storage.get_tasks(USER_A))[0]
        self.assertEqual(task["id"], task_id)
        self.assertEqual(task["title"], "เขียนเทสต์")
        self.assertEqual(task["priority"], "medium")
        self.assertEqual(task["status"], "inbox")

    async def test_optional_fields_are_stored(self):
        self.add_project("เฉวง")
        await bot.Storage.add_task(
            USER_A, "ยื่นเอกสาร", priority="high", due_date="2026-09-01", project="เฉวง"
        )
        task = (await bot.Storage.get_tasks(USER_A))[0]
        self.assertEqual(task["priority"], "high")
        self.assertEqual(task["due_date"], "2026-09-01")
        self.assertEqual(task["project"], "เฉวง")

    async def test_unknown_project_name_is_left_unlinked(self):
        """projects.type มี CHECK ห้าค่า เดาไม่ได้ จึงปล่อยว่างแทนการสร้างมั่ว"""
        await bot.Storage.add_task(USER_A, "งานของโปรเจกต์ที่ยังไม่มี", project="ยังไม่มีชื่อนี้")
        self.assertIsNone((await bot.Storage.get_tasks(USER_A))[0]["project"])
        self.assertEqual(self.rows("SELECT id FROM projects"), [])

    async def test_creating_a_task_records_a_status_event(self):
        """รายงานทุกตัวอ่าน task_events ฝั่ง LINE เขียนตอนสร้างงาน ฝั่งนี้ก็ต้องเขียน"""
        task_id = await bot.Storage.add_task(USER_A, "งานที่ต้องมีประวัติ")
        events = self.rows("SELECT * FROM task_events WHERE task_id = ?", (task_id,))
        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0]["from_status"])
        self.assertEqual(events[0]["to_status"], "inbox")

    async def test_ids_are_unique(self):
        first = await bot.Storage.add_task(USER_A, "งานหนึ่ง")
        second = await bot.Storage.add_task(USER_A, "งานสอง")
        self.assertNotEqual(first, second)


class TestListTasks(BotDbCase):
    async def test_only_your_own_tasks(self):
        await bot.Storage.add_task(USER_A, "ของ A")
        await bot.Storage.add_task(USER_B, "ของ B")
        self.assertEqual([t["title"] for t in await bot.Storage.get_tasks(USER_A)], ["ของ A"])

    async def test_filter_by_status(self):
        keep = await bot.Storage.add_task(USER_A, "ยังไม่เสร็จ")
        done = await bot.Storage.add_task(USER_A, "เสร็จแล้ว")
        await bot.Storage.complete_task(USER_A, done)

        todo = await bot.Storage.get_tasks(USER_A, status="inbox")
        self.assertEqual([t["id"] for t in todo], [keep])
        self.assertEqual([t["id"] for t in await bot.Storage.get_tasks(USER_A, status="done")], [done])

    async def test_empty_list_for_a_new_user(self):
        self.assertEqual(await bot.Storage.get_tasks(USER_A), [])


class TestCompleteTask(BotDbCase):
    async def test_marks_done_and_stamps_the_time(self):
        task_id = await bot.Storage.add_task(USER_A, "งานที่จะปิด")
        self.assertTrue(await bot.Storage.complete_task(USER_A, task_id))

        row = self.rows("SELECT status, completed_at FROM tasks WHERE id = ?", (task_id,))[0]
        self.assertEqual(row["status"], "done")
        self.assertIsNotNone(row["completed_at"])

    async def test_cannot_close_someone_elses_task(self):
        task_id = await bot.Storage.add_task(USER_A, "ของ A")
        self.assertFalse(await bot.Storage.complete_task(USER_B, task_id))
        self.assertEqual((await bot.Storage.get_tasks(USER_A))[0]["status"], "inbox")

    async def test_unknown_task_id(self):
        self.assertFalse(await bot.Storage.complete_task(USER_A, 9999))

    async def test_closing_records_the_transition(self):
        task_id = await bot.Storage.add_task(USER_A, "งานที่จะปิด")
        await bot.Storage.complete_task(USER_A, task_id)
        events = self.rows(
            "SELECT from_status, to_status FROM task_events WHERE task_id = ? ORDER BY id",
            (task_id,),
        )
        self.assertEqual(
            [(e["from_status"], e["to_status"]) for e in events],
            [(None, "inbox"), ("inbox", "done")],
        )

    async def test_closing_twice_is_harmless(self):
        task_id = await bot.Storage.add_task(USER_A, "กดซ้ำ")
        self.assertTrue(await bot.Storage.complete_task(USER_A, task_id))
        self.assertTrue(await bot.Storage.complete_task(USER_A, task_id))
        # ปิดซ้ำต้องไม่เพิ่มแถวประวัติที่ไม่มีอยู่จริง
        self.assertEqual(
            len(self.rows("SELECT id FROM task_events WHERE task_id = ?", (task_id,))), 2
        )


class TestSharedTaskTable(unittest.IsolatedAsyncioTestCase):
    """ตาราง tasks มีเจ้าของเดียวคือ sql/01_schema.sql

    เดิม bot.py ประกาศ tasks ของตัวเองที่มี user_id/priority/due_date/project
    ทั้งสองฝั่งใช้ CREATE TABLE IF NOT EXISTS ใครสร้างก่อนได้ตารางนั้นไป
    app.py เรียก start_web() ก่อน start_telegram() เสมอ Life OS จึงชนะทุกครั้ง
    และ /task พังด้วย OperationalError โดยไม่มีใครเห็น เพราะฐานข้อมูลหายทุก
    deploy อยู่แล้ว พอมี volume ถาวรโครงที่ผิดจะถูกเก็บไว้ถาวรด้วย
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._original = bot.DB_PATH
        bot.DB_PATH = str(Path(self._tmp.name) / "shared.db")

    def tearDown(self):
        bot.DB_PATH = self._original
        self._tmp.cleanup()

    def columns(self):
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            return [row[1] for row in conn.execute("PRAGMA table_info(tasks)")]
        finally:
            conn.close()

    async def test_task_works_in_the_boot_order_app_py_actually_uses(self):
        """start_web() ก่อน แล้วค่อย start_telegram() — ลำดับจริงของ app.py"""
        line_webhook.init_webhook_tables(bot.DB_PATH)   # start_web()
        bot.init_db()                                   # start_telegram()

        task_id = await bot.Storage.add_task(111, "ซื้อของ")
        self.assertEqual((await bot.Storage.get_tasks(111))[0]["id"], task_id)

    async def test_task_works_when_telegram_boots_first(self):
        """สลับลำดับก็ต้องได้ตารางเดียวกัน ไม่ใช่คนละโครงตามใครมาก่อน"""
        bot.init_db()
        line_webhook.init_webhook_tables(bot.DB_PATH)

        self.assertIn("source_ref", self.columns())
        self.assertNotIn("user_id", self.columns())
        await bot.Storage.add_task(111, "ซื้อของ")

    async def test_telegram_and_line_tasks_share_one_table(self):
        """งานจากสองช่องทางอยู่ตารางเดียว แยกกันด้วย source — เห็นรวมกันในรายงาน"""
        bot.init_db()
        await bot.Storage.add_task(111, "งานจาก Telegram")

        conn = line_webhook.connect(bot.DB_PATH)
        try:
            line_webhook.create_task(
                conn,
                title="งานจาก LINE",
                project_id=None,
                source_message_id=None,
                at=line_webhook.utc_now(),
            )
            rows = conn.execute(
                "SELECT source, title FROM tasks ORDER BY id"
            ).fetchall()
        finally:
            conn.close()

        self.assertEqual(
            [(r["source"], r["title"]) for r in rows],
            [("telegram", "งานจาก Telegram"), ("line", "งานจาก LINE")],
        )
        # แต่ /tasks ของผู้ใช้ Telegram ต้องไม่เห็นงานของ LINE
        self.assertEqual(
            [t["title"] for t in await bot.Storage.get_tasks(111)], ["งานจาก Telegram"]
        )

    async def test_the_bot_writes_with_foreign_keys_enforced(self):
        """ตารางกลางพึ่ง FK และ ON DELETE CASCADE — สองเส้นทางต้องตั้งค่าเหมือนกัน

        sqlite3.connect() เปล่า ๆ ปิด foreign_keys ไว้เป็นค่าเริ่มต้น และ PRAGMA
        ที่อยู่หัว 01_schema.sql มีผลแค่กับ connection ที่รันสคริปต์นั้น ไม่ติดไป
        กับไฟล์ ถ้าฝั่งบอทเขียนโดยไม่เปิด FK แถวกำพร้าใน task_events จะเข้าได้
        เงียบ ๆ ทั้งที่ฝั่งเว็บเขียนแบบเดียวกันไม่ได้
        """
        bot.init_db()

        conn = bot.connect(bot.DB_PATH)
        try:
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            with self.assertRaises(sqlite3.IntegrityError):
                with conn:
                    conn.execute(
                        "INSERT INTO task_events (task_id, to_status, at) VALUES (?, 'inbox', ?)",
                        (99999, line_webhook.utc_now()),
                    )
        finally:
            conn.close()

    async def test_priority_is_added_to_a_database_that_predates_it(self):
        """ฐานข้อมูลบน volume ที่สร้างไว้ก่อนมีคอลัมน์นี้ ต้องถูกเติมให้ ไม่ใช่พัง

        CREATE TABLE IF NOT EXISTS ไม่แตะตารางที่มีอยู่ ถ้าไม่ ALTER ให้
        ฐานข้อมูลเดิมจะไม่มี priority ตลอดไปเมื่อมี volume ถาวรแล้ว
        """
        schema = (Path(__file__).resolve().parent.parent / "sql" / "01_schema.sql").read_text(
            encoding="utf-8"
        )
        conn = sqlite3.connect(bot.DB_PATH)
        try:
            older = re.sub(r"^\s*priority\s+TEXT,.*\n", "", schema, count=1, flags=re.M)
            conn.executescript(older)
            conn.commit()
        finally:
            conn.close()
        self.assertNotIn("priority", self.columns())   # ฐานข้อมูล "เก่า" จริง ๆ

        bot.init_db()

        self.assertIn("priority", self.columns())
        await bot.Storage.add_task(111, "งานหลังอัปเกรด", priority="high")
        self.assertEqual((await bot.Storage.get_tasks(111))[0]["priority"], "high")


if __name__ == "__main__":
    unittest.main()
