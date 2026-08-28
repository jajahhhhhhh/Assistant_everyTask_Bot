"""
เทสต์เส้นทาง "บอกชื่อเจ้าของทีหลัง" — ตั้งแต่แคปชันจนถึงแถวในฐานข้อมูล

อาการจริงที่ทำให้ต้องมีไฟล์นี้ เจ้าของส่งไฟล์ export เข้ามาพร้อมแคปชัน

    /task - Renovate "ฉันคือ W.ch♾️💵💰 "

บอทอ่านชื่อไม่เจอ เพราะตัวจับยึดหัวข้อความไว้ ทั้ง 722 ข้อความจึงถูกบันทึกเป็น
ขาเข้า รวมถึง 252 ข้อความที่เขาพิมพ์เอง แล้วบอทตอบกลับว่า "ส่งไฟล์ใหม่พร้อม
caption ว่า ฉันคือ ..." ซึ่งเป็นคำแนะนำที่ทำข้อมูลพัง: กันซ้ำเอาทิศทางไปใส่ในคีย์
ด้วย ข้อความของเจ้าของจึงกลายเป็นของใหม่ทั้ง 252 ใบ ได้ 974 แถวจาก 722 ข้อความ
"""

import unittest

import line_export
from tests._bot_case import BotDbCase, bot

# แชทกลุ่มสามคน เจ้าของคือ Ann Lee — เหมือนโครงของไฟล์จริง
# ทุกคนต้องพูดอย่างน้อยสองครั้ง ตัวเดาชื่อผู้ส่งไม่รับชื่อที่โผล่ครั้งเดียว
EXPORT = (
    "2026.07.09 วันพฤหัสบดี\n"
    "09:15 Ann Lee ส่งของพรุ่งนี้นะครับ\n"
    "09:16 Bob รับทราบครับ\n"
    "09:17 Cat ขอบคุณค่ะ\n"
    "09:18 Ann Lee ครับผม\n"
    "09:19 Bob แล้วเจอกัน\n"
    "09:20 Cat โอเคค่ะ\n"
)


class TestCaptionParsing(unittest.TestCase):
    def test_the_caption_the_owner_actually_sent(self):
        """แคปชันจริง คำใบ้ไม่ได้อยู่ต้นข้อความและมีอัญประกาศล้อม"""
        self.assertEqual(
            bot._owner_from_caption('/task - Renovate "ฉันคือ W.ch♾️💵💰 "'),
            "W.ch♾️💵💰",
        )

    def test_a_plain_caption_still_works(self):
        self.assertEqual(bot._owner_from_caption("ฉันคือ Ann Lee"), "Ann Lee")

    def test_quotes_around_the_name_are_peeled_off(self):
        for caption in ('ฉันคือ "Ann Lee"', "ฉันคือ 'Ann Lee'", "ฉันคือ “Ann Lee”"):
            with self.subTest(caption=caption):
                self.assertEqual(bot._owner_from_caption(caption), "Ann Lee")

    def test_the_other_thai_wordings_work_anywhere_too(self):
        for caption in ("ไฟล์กลุ่มนะ ผมคือ Ann Lee", "แนบมาแล้ว เราคือ Ann Lee"):
            with self.subTest(caption=caption):
                self.assertEqual(bot._owner_from_caption(caption), "Ann Lee")

    def test_me_inside_an_ordinary_sentence_is_not_a_name(self):
        """"me" ยังต้องยึดหัวบรรทัด ไม่งั้นประโยคธรรมดากลายเป็นชื่อคน"""
        self.assertIsNone(bot._owner_from_caption("please send me the file"))

    def test_me_at_the_start_of_a_line_still_works(self):
        self.assertEqual(bot._owner_from_caption("me: Ann Lee"), "Ann Lee")

    def test_no_caption_means_no_name(self):
        self.assertIsNone(bot._owner_from_caption(None))
        self.assertIsNone(bot._owner_from_caption("ส่งไฟล์มาให้"))


class TestResolveOwner(unittest.TestCase):
    SENDERS = ["Ann Lee", "Bob", "Cat"]

    def test_an_exact_name_passes_through(self):
        self.assertEqual(line_export.resolve_owner("Ann Lee", self.SENDERS), "Ann Lee")

    def test_extra_spaces_and_case_still_match(self):
        for typed in ("  Ann   Lee ", "ann lee", "ANN LEE"):
            with self.subTest(typed=typed):
                self.assertEqual(
                    line_export.resolve_owner(typed, self.SENDERS), "Ann Lee"
                )

    def test_a_name_that_is_not_in_the_file_resolves_to_nothing(self):
        """คืน None เพื่อให้ผู้เรียกบอกผู้ใช้ ไม่ใช่นำเข้าผิดเงียบ ๆ"""
        self.assertIsNone(line_export.resolve_owner("Danny", self.SENDERS))


class TestTellingTheBotWhoYouAreAfterwards(BotDbCase):
    """ส่งไฟล์เดิมซ้ำพร้อมชื่อที่ถูก ต้อง "แก้" ของเดิม ไม่ใช่ "เพิ่ม" ชุดใหม่"""

    def _import(self, owner=None):
        return line_export.import_from_text(
            bot.DB_PATH, EXPORT, owner_name=owner, file_name="กลุ่มงาน.txt"
        )[1]

    def test_the_first_import_without_a_name_files_everything_inbound(self):
        result = self._import()
        self.assertEqual(result.imported, 6)
        self.assertIsNone(result.owner)
        self.assertEqual(
            [r["direction"] for r in self.rows("SELECT direction FROM chat_messages")],
            ["in"] * 6,
        )

    def test_resending_with_the_name_adds_no_rows(self):
        """หัวใจของไฟล์นี้ — คำแนะนำที่บอทให้เองต้องไม่ทำข้อมูลบวม"""
        self._import()
        again = self._import(owner="Ann Lee")
        self.assertEqual(again.imported, 0)
        self.assertEqual(len(self.rows("SELECT id FROM chat_messages")), 6)

    def test_resending_with_the_name_flips_the_owner_messages(self):
        self._import()
        again = self._import(owner="Ann Lee")
        self.assertEqual(again.corrected, 2)
        self.assertEqual(again.duplicates, 4)
        directions = [
            r["direction"]
            for r in self.rows("SELECT direction FROM chat_messages ORDER BY sent_at")
        ]
        self.assertEqual(directions, ["out", "in", "in", "out", "in", "in"])

    def test_a_corrected_message_stops_being_something_we_owe_a_reply_to(self):
        """ข้อความของเราเองไม่ใช่สิ่งที่รอเราตอบ intent กับ contact ต้องถูกล้าง"""
        self._import()
        self._import(owner="Ann Lee")
        rows = self.rows(
            "SELECT intent, urgency, confidence, contact_id FROM chat_messages"
            " WHERE direction = 'out'"
        )
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertIsNone(row["intent"])
            self.assertIsNone(row["urgency"])
            self.assertIsNone(row["confidence"])
            self.assertIsNone(row["contact_id"])

    def test_a_third_send_changes_nothing_at_all(self):
        self._import()
        self._import(owner="Ann Lee")
        third = self._import(owner="Ann Lee")
        self.assertEqual((third.imported, third.corrected), (0, 0))
        self.assertEqual(third.duplicates, 6)
        self.assertEqual(len(self.rows("SELECT id FROM chat_messages")), 6)

    def test_getting_the_name_right_the_first_time_needs_no_correction(self):
        first = self._import(owner="Ann Lee")
        self.assertEqual((first.imported, first.corrected), (6, 0))

    def test_naming_someone_who_is_not_in_the_file_is_reported(self):
        """ต่างจาก "ไม่ได้บอกชื่อมา" — ต้องบอกผู้ใช้ว่าพิมพ์ชื่อผิด"""
        result = self._import(owner="Danny")
        self.assertEqual(result.owner_unmatched, "Danny")
        self.assertIsNone(result.owner)
        self.assertEqual(
            [r["direction"] for r in self.rows("SELECT direction FROM chat_messages")],
            ["in"] * 6,
        )

    def test_a_name_typed_with_sloppy_spacing_still_lands(self):
        self._import()
        again = self._import(owner="  ann   lee  ")
        self.assertEqual(again.corrected, 2)
        self.assertEqual(again.owner, "Ann Lee")
        self.assertIsNone(again.owner_unmatched)


if __name__ == "__main__":
    unittest.main()
