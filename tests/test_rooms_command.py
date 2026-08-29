"""
เทสต์ /rooms — ผูกห้องแชตเข้ากับไซต์งาน

ทำไมต้องมี: ผู้รับเหมาเจ้าเดียวทำงานให้สองไซต์ (ลิปะน้อย กับ เฉวง) แยกงานด้วยคำ
ในข้อความไม่แม่น เพราะเขาพูดถึงทั้งสองที่ปนกันในประโยคเดียว แต่แยกด้วย "ห้อง"
แม่นเสมอ — LINE ให้ groupId ต่างกันมาอยู่แล้ว และ chat_threads.project_id
มีคอลัมน์รออยู่ตั้งแต่แรก แค่ยังไม่มีทางให้ผู้ใช้ผูกเอง

จุดที่พลาดง่ายที่สุดคือข้อความเก่า: chat_messages ถือ project_id ของตัวเอง
(คัดลอกจากห้องตอนบันทึก) ถ้าอัปเดตแค่ห้อง ประวัติที่นำเข้าไปแล้วจะไม่มีไซต์
"""

import unittest
from types import SimpleNamespace

import line_export
from tests._bot_case import BotDbCase, bot

EXPORT = (
    "2026.07.09 วันพฤหัสบดี\n"
    "09:15 Ann Lee ส่งของพรุ่งนี้นะครับ\n"
    "09:16 Bob รับทราบครับ\n"
    "09:17 Ann Lee ครับผม\n"
    "09:18 Bob แล้วเจอกัน\n"
)


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class FakeUpdate:
    def __init__(self):
        self.effective_user = SimpleNamespace(id=7, first_name="J")
        self.message = FakeMessage()


class FakeContext:
    def __init__(self, *args):
        self.args = list(args)


class TestRooms(BotDbCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        line_export.import_from_text(
            bot.DB_PATH, EXPORT, owner_name="Ann Lee", file_name="งานลิปะน้อย.txt"
        )

    async def run_command(self, *args):
        update = FakeUpdate()
        await bot.rooms_command(update, FakeContext(*args))
        return update.message.replies

    def room_id(self):
        return self.rows("SELECT id FROM chat_threads")[0]["id"]

    # ── ดูรายการ ──────────────────────────────────────────────────────────────
    async def test_the_listing_shows_the_room_with_its_message_count(self):
        replies = await self.run_command()
        self.assertIn("งานลิปะน้อย", replies[0])
        self.assertIn("4 ข้อความ", replies[0])

    async def test_a_room_with_no_site_says_so(self):
        replies = await self.run_command()
        self.assertIn("ยังไม่ผูกไซต์", replies[0])

    async def test_an_empty_database_says_what_to_do_next(self):
        conn = bot.connect(bot.DB_PATH)
        try:
            with conn:
                conn.execute("DELETE FROM chat_messages")
                conn.execute("DELETE FROM chat_threads")
        finally:
            conn.close()
        replies = await self.run_command()
        self.assertIn("ยังไม่มีห้องแชต", replies[0])

    # ── ผูกไซต์ ───────────────────────────────────────────────────────────────
    async def test_binding_creates_the_site_when_it_is_new(self):
        await self.run_command(str(self.room_id()), "ลิปะน้อย")
        names = [r["name"] for r in self.rows("SELECT name FROM projects")]
        self.assertIn("ลิปะน้อย", names)

    async def test_binding_points_the_thread_at_the_site(self):
        await self.run_command(str(self.room_id()), "ลิปะน้อย")
        row = self.rows(
            "SELECT p.name FROM chat_threads t JOIN projects p ON p.id = t.project_id"
        )
        self.assertEqual(row[0]["name"], "ลิปะน้อย")

    async def test_messages_already_imported_get_the_site_too(self):
        """หัวใจของไฟล์นี้ — ผูกแล้วประวัติเก่าต้องติดไซต์ไปด้วย ไม่ใช่แค่ข้อความใหม่"""
        await self.run_command(str(self.room_id()), "ลิปะน้อย")
        rows = self.rows("SELECT project_id FROM chat_messages")
        self.assertEqual(len(rows), 4)
        for row in rows:
            self.assertIsNotNone(row["project_id"], "ข้อความเก่ายังไม่ติดไซต์")

    async def test_the_reply_reports_how_many_messages_moved(self):
        replies = await self.run_command(str(self.room_id()), "ลิปะน้อย")
        self.assertIn("4", replies[0])

    async def test_a_second_room_can_share_an_existing_site(self):
        """ผูกไซต์เดิมซ้ำต้องไม่สร้างไซต์ชื่อซ้ำ — projects.name เป็น UNIQUE"""
        await self.run_command(str(self.room_id()), "ลิปะน้อย")
        replies = await self.run_command(str(self.room_id()), "ลิปะน้อย")
        self.assertIn("ผูกกับไซต์เดิม", replies[0])
        self.assertEqual(len(self.rows("SELECT id FROM projects WHERE name = 'ลิปะน้อย'")), 1)

    async def test_a_site_name_with_spaces_is_kept_whole(self):
        await self.run_command(str(self.room_id()), "ตึก", "2", "ชั้น", "ลิปะน้อย")
        names = [r["name"] for r in self.rows("SELECT name FROM projects")]
        self.assertIn("ตึก 2 ชั้น ลิปะน้อย", names)

    async def test_rebinding_moves_the_room_to_the_new_site(self):
        await self.run_command(str(self.room_id()), "ลิปะน้อย")
        await self.run_command(str(self.room_id()), "เฉวง")
        row = self.rows(
            "SELECT p.name FROM chat_threads t JOIN projects p ON p.id = t.project_id"
        )
        self.assertEqual(row[0]["name"], "เฉวง")

    # ── ใช้ผิด ────────────────────────────────────────────────────────────────
    async def test_an_unknown_room_number_is_refused_without_touching_anything(self):
        replies = await self.run_command("999", "ลิปะน้อย")
        self.assertIn("ไม่มีห้องเลข 999", replies[0])
        self.assertEqual(self.rows("SELECT id FROM projects"), [])

    async def test_a_missing_site_name_shows_the_usage(self):
        replies = await self.run_command(str(self.room_id()))
        self.assertIn("/rooms", replies[0])
        self.assertEqual(self.rows("SELECT id FROM projects"), [])

    async def test_a_non_numeric_room_shows_the_usage(self):
        replies = await self.run_command("ลิปะน้อย", "1")
        self.assertIn("/rooms", replies[0])

class TestRoomsTrailingCommand(BotDbCase):
    """คำสั่งที่พิมพ์ต่อท้ายชื่อไซต์ในบรรทัดเดียวกัน

    ผู้ใช้พิมพ์ "/rooms 2 Renovate เฉวง /reclassify" มาจริง Telegram ส่งมาเป็น
    ข้อความเดียว จึงทำงานได้แค่คำสั่งแรก ถ้าเอาทุกคำมาต่อเป็นชื่อ ไซต์จะชื่อ
    "Renovate เฉวง /reclassify" และ /reclassify ก็ไม่ได้รันด้วย เสียทั้งสองทาง
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        line_export.import_from_text(
            bot.DB_PATH, EXPORT, owner_name="Ann Lee", file_name="งานลิปะน้อย.txt"
        )

    async def run_command(self, *args):
        update = FakeUpdate()
        await bot.rooms_command(update, FakeContext(*args))
        return update.message.replies

    def room_id(self):
        return self.rows("SELECT id FROM chat_threads")[0]["id"]

    async def test_the_command_does_not_become_part_of_the_site_name(self):
        await self.run_command(str(self.room_id()), "เฉวง", "/reclassify")
        names = [r["name"] for r in self.rows("SELECT name FROM projects")]
        self.assertIn("เฉวง", names)
        self.assertNotIn("เฉวง /reclassify", names)

    async def test_the_dropped_command_is_reported(self):
        replies = await self.run_command(str(self.room_id()), "เฉวง", "/reclassify")
        self.assertIn("/reclassify", replies[-1])
        self.assertIn("ไม่ได้ทำงาน", replies[-1])

    async def test_a_plain_site_name_gets_no_warning(self):
        replies = await self.run_command(str(self.room_id()), "เฉวง")
        self.assertNotIn("ไม่ได้ทำงาน", replies[-1])

    async def test_a_site_name_of_several_words_still_joins(self):
        await self.run_command(str(self.room_id()), "ลิปะ", "น้อย", "/reclassify")
        names = [r["name"] for r in self.rows("SELECT name FROM projects")]
        self.assertIn("ลิปะ น้อย", names)

    async def test_a_command_where_the_name_should_be_is_a_usage_error(self):
        """เหลือแต่คำสั่ง = ไม่ได้บอกชื่อไซต์มา ต้องไม่ผูกกับชื่อว่าง"""
        replies = await self.run_command(str(self.room_id()), "/reclassify")
        self.assertIn("ใช้แบบนี้", replies[-1])
        self.assertEqual(self.rows("SELECT name FROM projects"), [])


class TestRoomsHint(BotDbCase):
    """คำใบ้ท้ายรายการต้องก๊อปไปใช้ได้เลย

    เดิมเขียนว่า "/rooms <เลขห้อง> <ชื่อไซต์>" ผู้ใช้พิมพ์วงเล็บตามมาจริงสามรอบ
    ติดกัน แล้วบอทก็เงียบทุกรอบ เพราะ args[0] ไม่ใช่ตัวเลข
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        line_export.import_from_text(
            bot.DB_PATH, EXPORT, owner_name="Ann Lee", file_name="งานลิปะน้อย.txt"
        )

    async def test_the_hint_is_a_command_that_actually_runs(self):
        update = FakeUpdate()
        await bot.rooms_command(update, FakeContext())
        reply = update.message.replies[0]
        thread_id = self.rows("SELECT id FROM chat_threads")[0]["id"]
        self.assertIn("/rooms " + str(thread_id) + " ", reply)
        self.assertNotIn("<", reply)
        self.assertNotIn(">", reply)


if __name__ == "__main__":
    unittest.main()
