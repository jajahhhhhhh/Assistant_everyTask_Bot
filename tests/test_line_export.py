"""
เทสต์ตัวอ่านไฟล์ประวัติแชทที่ export จาก LINE

webhook รับได้เฉพาะข้อความที่เข้ามาหลังจากต่อ webhook แล้ว บทสนทนาก่อนหน้านั้น
ทั้งหมดต้องเข้าทางไฟล์ export ทางเดียว ไฟล์นี้จึงคุมสองเรื่อง: อ่านรูปแบบที่แอป
LINE พ่นออกมาจริงได้ และ **ไม่ทิ้งบรรทัดที่อ่านไม่ออกอย่างเงียบ ๆ**

รูปแบบไฟล์ต่างกันตามภาษาและเวอร์ชันของแอป ตัวอย่างในไฟล์นี้จึงครอบหลายแบบเท่าที่
รู้ ไม่ได้ยืนยันว่าครบทุกแบบ — ต้องเอาไฟล์จริงมาทดสอบอีกที
"""

import sqlite3
import unittest

import line_export
from tests._bot_case import BotDbCase, bot

# รูปแบบภาษาอังกฤษ คั่นด้วย tab ซึ่งเป็นแบบที่พบบ่อยที่สุด
EXPORT_EN = """[LINE] Chat with Farid
Saved on: 2026/08/25 18:04

2026/08/24
09:15\tFarid\tส่งไฟล์สัญญาให้หน่อยได้ไหม
09:20\tJ\tเดี๋ยวส่งให้ตอนบ่าย
09:21\tFarid\tขอบคุณครับ

2026/08/25
10:02\tFarid\tสัญญายังไม่ได้เลย
10:30\tJ\tขอโทษ ลืมสนิท ส่งแล้วนะ
"""


class TestParseBasics(unittest.TestCase):
    def setUp(self):
        self.export = line_export.parse_export(EXPORT_EN)

    def test_title_comes_from_the_header(self):
        self.assertEqual(self.export.title, "Farid")

    def test_every_message_is_read(self):
        self.assertEqual(len(self.export.messages), 5)

    def test_nothing_is_skipped_in_a_clean_file(self):
        self.assertEqual(self.export.skipped, [])

    def test_the_date_line_sets_the_day_for_the_lines_under_it(self):
        first, last = self.export.messages[0], self.export.messages[-1]
        self.assertEqual(first.sent_at, "2026-08-24T09:15:00")
        self.assertEqual(last.sent_at, "2026-08-25T10:30:00")

    def test_sender_and_body_are_separated(self):
        self.assertEqual(self.export.messages[0].sender, "Farid")
        self.assertEqual(self.export.messages[0].body, "ส่งไฟล์สัญญาให้หน่อยได้ไหม")

    def test_senders_are_ranked_by_volume(self):
        self.assertEqual(set(self.export.senders), {"Farid", "J"})


class TestFormatVariants(unittest.TestCase):
    def test_buddhist_years_are_converted(self):
        """แอป LINE ภาษาไทย export ปี พ.ศ. — 2569 คือ 2026"""
        export = line_export.parse_export(
            "[LINE] แชทกับ สมชาย\n2569/08/24\n09:15\tสมชาย\tสวัสดี\n"
        )
        self.assertEqual(export.messages[0].sent_at, "2026-08-24T09:15:00")

    def test_day_first_dates_are_read_as_day_first(self):
        export = line_export.parse_export(
            "[LINE] Chat with A\n24/08/2026\n09:15\tA\thi\n"
        )
        self.assertEqual(export.messages[0].sent_at[:10], "2026-08-24")

    def test_a_weekday_in_brackets_does_not_break_the_date(self):
        export = line_export.parse_export(
            "[LINE] Chat with A\n2026/08/24 (จ)\n09:15\tA\thi\n"
        )
        self.assertEqual(export.messages[0].sent_at[:10], "2026-08-24")

    def test_twelve_hour_times_convert(self):
        export = line_export.parse_export(
            "[LINE] Chat with A\n2026/08/24\n01:05 PM\tA\thi\n12:30 AM\tA\tlate\n"
        )
        self.assertEqual(export.messages[0].sent_at[11:], "13:05:00")
        self.assertEqual(export.messages[1].sent_at[11:], "00:30:00")

    def test_spaces_work_when_the_tabs_are_gone(self):
        """ก๊อปวางผ่านบางแอปทำให้ tab กลายเป็นช่องว่าง"""
        export = line_export.parse_export(
            "[LINE] Chat with A\n2026/08/24\n09:15   A   ข้อความ\n"
        )
        self.assertEqual(len(export.messages), 1)
        self.assertEqual(export.messages[0].body, "ข้อความ")

    def test_carriage_returns_do_not_end_up_in_the_body(self):
        export = line_export.parse_export(
            "[LINE] Chat with A\r\n2026/08/24\r\n09:15\tA\thi\r\n"
        )
        self.assertEqual(export.messages[0].body, "hi")

    def test_a_multi_line_message_stays_one_message(self):
        """ข้อความที่ผู้ใช้กด Enter ในตัว ถูก export เป็นหลายบรรทัดติดกัน"""
        export = line_export.parse_export(
            "[LINE] Chat with A\n2026/08/24\n09:15\tA\tบรรทัดแรก\nบรรทัดสอง\n09:16\tA\tถัดไป\n"
        )
        self.assertEqual(len(export.messages), 2)
        self.assertEqual(export.messages[0].body, "บรรทัดแรก\nบรรทัดสอง")


class TestSkippedLinesAreReported(unittest.TestCase):
    """เรื่องสำคัญที่สุดของไฟล์นี้

    ไฟล์ที่ parse ได้ครึ่งเดียวหน้าตาเหมือนไฟล์ที่สำเร็จทุกประการ ถ้าไม่นับบรรทัด
    ที่อ่านไม่ออกออกมาให้เห็น ผู้ใช้จะเชื่อว่าประวัติเข้าครบแล้วทั้งที่หายไปครึ่ง
    """

    def test_a_message_before_any_date_line_is_reported_not_dropped(self):
        export = line_export.parse_export(
            "[LINE] Chat with A\n09:15\tA\tไม่รู้ว่าวันไหน\n"
        )
        self.assertEqual(export.messages, [])
        self.assertEqual(len(export.skipped), 1)
        self.assertEqual(export.skipped[0][0], 2, "ต้องบอกเลขบรรทัดด้วย")

    def test_a_time_without_a_sender_is_reported(self):
        export = line_export.parse_export(
            "[LINE] Chat with A\n2026/08/24\n09:15\n"
        )
        self.assertEqual(export.messages, [])
        self.assertEqual(len(export.skipped), 1)

    def test_an_impossible_date_is_not_treated_as_a_date(self):
        export = line_export.parse_export(
            "[LINE] Chat with A\n2026/13/45\n2026/08/24\n09:15\tA\thi\n"
        )
        self.assertEqual(len(export.messages), 1)
        self.assertEqual(export.messages[0].sent_at[:10], "2026-08-24")


class TestGuessOwner(unittest.TestCase):
    def test_the_name_that_is_not_in_the_title_is_you(self):
        export = line_export.parse_export(EXPORT_EN)
        self.assertEqual(line_export.guess_owner(export), "J")

    def test_a_group_chat_cannot_be_guessed(self):
        export = line_export.parse_export(
            "[LINE] Chat in ทีมงาน\n2026/08/24\n"
            "09:15\tA\thi\n09:16\tB\thi\n09:17\tC\thi\n"
        )
        self.assertIsNone(line_export.guess_owner(export))

    def test_no_title_means_no_guess(self):
        export = line_export.parse_export("2026/08/24\n09:15\tA\thi\n09:16\tB\thi\n")
        self.assertIsNone(line_export.guess_owner(export))


class TestThreadKey(unittest.TestCase):
    def test_the_same_file_gives_the_same_key(self):
        a = line_export.parse_export(EXPORT_EN)
        b = line_export.parse_export(EXPORT_EN)
        self.assertEqual(line_export.thread_key(a), line_export.thread_key(b))

    def test_a_different_chat_gives_a_different_key(self):
        other = line_export.parse_export(
            "[LINE] Chat with Somchai\n2026/08/24\n09:15\tSomchai\thi\n"
        )
        self.assertNotEqual(
            line_export.thread_key(line_export.parse_export(EXPORT_EN)),
            line_export.thread_key(other),
        )

    def test_imported_threads_cannot_collide_with_live_ones(self):
        """ห้องจาก webhook ใช้ chat id ของ LINE ตรง ๆ คำนำหน้าจึงต้องแยกกัน"""
        key = line_export.thread_key(line_export.parse_export(EXPORT_EN))
        self.assertTrue(key.startswith("import:"))


class TestImport(BotDbCase):
    def parse(self, text=EXPORT_EN):
        return line_export.parse_export(text)

    def run_import(self, export=None, **kwargs):
        conn = bot.connect(bot.DB_PATH)
        try:
            return line_export.import_export(conn, export or self.parse(), **kwargs)
        finally:
            conn.close()

    def test_messages_land_in_the_shared_table(self):
        result = self.run_import(owner_name="J")
        self.assertEqual(result.imported, 5)
        rows = self.rows("SELECT direction, body FROM chat_messages ORDER BY sent_at")
        self.assertEqual(len(rows), 5)

    def test_the_owner_messages_are_outbound(self):
        self.run_import(owner_name="J")
        directions = [
            row["direction"]
            for row in self.rows("SELECT direction FROM chat_messages ORDER BY sent_at")
        ]
        self.assertEqual(directions, ["in", "out", "in", "in", "out"])

    def test_outbound_messages_have_no_contact(self):
        """contact_id เป็น NULL แปลว่าเป็นเราเอง — view ที่นับเวลาตอบพึ่งข้อนี้"""
        self.run_import(owner_name="J")
        rows = self.rows(
            "SELECT contact_id FROM chat_messages WHERE direction = 'out'"
        )
        self.assertTrue(all(row["contact_id"] is None for row in rows))

    def test_inbound_messages_are_classified(self):
        result = self.run_import(owner_name="J")
        self.assertTrue(result.intents, "ต้องคัดแยกข้อความขาเข้าให้ด้วย")
        rows = self.rows(
            "SELECT intent FROM chat_messages WHERE direction = 'in' AND intent IS NOT NULL"
        )
        self.assertTrue(rows)

    def test_our_own_messages_are_not_classified(self):
        """ข้อความของเราเองไม่ใช่สิ่งที่รอเราตอบ ติด intent ไปจะทำให้รายงานเพี้ยน"""
        self.run_import(owner_name="J")
        rows = self.rows(
            "SELECT intent FROM chat_messages WHERE direction = 'out'"
        )
        self.assertTrue(all(row["intent"] is None for row in rows))

    def test_importing_the_same_file_twice_adds_nothing(self):
        first = self.run_import(owner_name="J")
        second = self.run_import(owner_name="J")
        self.assertEqual(first.imported, 5)
        self.assertEqual(second.imported, 0)
        self.assertEqual(second.duplicates, 5)
        self.assertEqual(len(self.rows("SELECT id FROM chat_messages")), 5)

    def test_without_an_owner_everything_is_inbound(self):
        result = self.run_import(owner_name=None)
        self.assertEqual(result.imported, 5)
        directions = {
            row["direction"]
            for row in self.rows("SELECT direction FROM chat_messages")
        }
        self.assertEqual(directions, {"in"})

    def test_the_thread_carries_the_title_and_the_latest_timestamp(self):
        self.run_import(owner_name="J")
        thread = self.rows("SELECT title, last_msg_at, platform FROM chat_threads")[0]
        self.assertEqual(thread["title"], "Farid")
        self.assertEqual(thread["platform"], "line")
        self.assertEqual(thread["last_msg_at"], "2026-08-25T10:30:00")

    def test_skipped_lines_are_carried_into_the_result(self):
        export = self.parse(EXPORT_EN + "09:15\tจะไม่มีวันที่รองรับ\n")
        result = self.run_import(export, owner_name="J")
        self.assertEqual(result.skipped_lines, len(export.skipped))

    def test_an_empty_export_is_refused_rather_than_silently_doing_nothing(self):
        with self.assertRaises(ValueError):
            self.run_import(line_export.parse_export("[LINE] Chat with A\n"))

    def test_imported_history_does_not_touch_a_live_thread(self):
        """ห้องที่ webhook สร้างไว้ต้องไม่ถูกประวัติที่นำเข้ามาปน"""
        conn = bot.connect(bot.DB_PATH)
        try:
            with conn:
                line_webhook_thread = line_export.line_webhook.upsert_thread(
                    conn, "Cabcdef1234", is_group=False, sent_at="2026-08-01T00:00:00"
                )
                live_id = int(line_webhook_thread["id"])
        finally:
            conn.close()

        self.run_import(owner_name="J")
        rows = self.rows(
            "SELECT COUNT(*) AS n FROM chat_messages WHERE thread_id = ?", (live_id,)
        )
        self.assertEqual(rows[0]["n"], 0)


if __name__ == "__main__":
    unittest.main()


class TestImportFromText(BotDbCase):
    """นำเข้าผ่านเส้นทางที่ handler ใช้จริง — ข้ามเธรด

    เทสต์ที่เรียก import_export() ตรง ๆ รันบนเธรดเดียวจึงไม่มีทางเจอบั๊กนี้
    ตัวเชื่อมของ sqlite3 ผูกกับเธรดที่สร้าง การเปิด connection บนเธรดของ event
    loop แล้วส่งเข้า asyncio.to_thread ทำให้พังทุกครั้ง — เป็นบั๊กชนิดเดียวกับที่
    ทำให้ข้อความ LINE หายไปเงียบ ๆ ใน #11
    """

    async def test_it_works_when_run_off_the_event_loop_thread(self):
        import asyncio

        export, result = await asyncio.to_thread(
            line_export.import_from_text,
            bot.DB_PATH,
            EXPORT_EN,
            owner_name="J",
        )
        self.assertEqual(result.imported, 5)
        self.assertEqual(len(self.rows("SELECT id FROM chat_messages")), 5)

    async def test_the_owner_is_guessed_when_not_given(self):
        import asyncio

        export, result = await asyncio.to_thread(
            line_export.import_from_text, bot.DB_PATH, EXPORT_EN
        )
        directions = [
            row["direction"]
            for row in self.rows("SELECT direction FROM chat_messages ORDER BY sent_at")
        ]
        self.assertIn("out", directions, "ต้องเดาได้ว่า J คือเจ้าของ")

    async def test_an_empty_file_returns_instead_of_raising(self):
        """handler ต้องบอกผู้ใช้ว่าอ่านไม่ได้ ไม่ใช่โยน exception ออกไป"""
        import asyncio

        export, result = await asyncio.to_thread(
            line_export.import_from_text, bot.DB_PATH, "[LINE] Chat with A\n"
        )
        self.assertEqual(export.messages, [])
        self.assertEqual(result.imported, 0)
