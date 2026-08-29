"""
เทสต์ handler ที่รับไฟล์ export — ผ่าน handle_chat_export ตัวจริง

ทำไมต้องมีไฟล์นี้: หลัง deploy จริง ผู้ใช้ส่งไฟล์เข้าไปแล้วถามว่า "เข้าไหม"
แล้วตอบไม่ได้ เพราะ handler เขียน log เฉพาะตอนพัง การนำเข้าเจ็ดร้อยข้อความที่
สำเร็จ กับไฟล์ที่ไม่เคยเดินทางมาถึงบอทเลย หน้าตาใน log เหมือนกันเป๊ะ — ว่างทั้งคู่

log ต้องบอกจำนวน ไม่ใช่เนื้อหา ประวัติแชตเป็นข้อมูลส่วนตัว
"""

import unittest
from types import SimpleNamespace

from tests._bot_case import BotDbCase, bot

EXPORT = (
    "[LINE] Chat with Farid\n2026/08/24\n"
    "09:15\tFarid\tส่งไฟล์สัญญาให้หน่อย\n"
    "09:20\tJ\tเดี๋ยวส่งให้ตอนบ่าย\n"
)


class FakeMessage:
    def __init__(self, document, caption=None):
        self.document = document
        self.caption = caption
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, document, caption=None, user_id=7):
        self.effective_user = SimpleNamespace(id=user_id, first_name="J")
        self.message = FakeMessage(document, caption)


class FakeFile:
    def __init__(self, payload):
        self._payload = payload

    async def download_to_drive(self, path):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(self._payload)


class FakeContext:
    def __init__(self, payload):
        self.bot = SimpleNamespace(get_file=self._get_file)
        self._payload = payload

    async def _get_file(self, file_id):
        return FakeFile(self._payload)


def document(name="[LINE] Chat with Farid.txt", size=100):
    return SimpleNamespace(file_name=name, file_size=size, file_id="f1")


class TestTheImportLeavesATrace(BotDbCase):
    async def _run(self, payload=EXPORT, doc=None, caption=None):
        update = FakeUpdate(doc or document(), caption)
        with self.assertLogs("bot", level="INFO") as captured:
            await bot.handle_chat_export(update, FakeContext(payload))
        return update, "\n".join(captured.output)

    async def test_the_file_arriving_is_logged_before_anything_can_go_wrong(self):
        """ต้องรู้ว่าไฟล์มาถึง แยกจากคำถามว่านำเข้าสำเร็จไหม"""
        _, log = await self._run()
        self.assertIn("รับไฟล์แชท", log)
        self.assertIn("[LINE] Chat with Farid.txt", log)

    async def test_a_successful_import_reports_its_numbers(self):
        _, log = await self._run()
        self.assertIn("นำเข้าไฟล์แชทสำเร็จ", log)
        self.assertIn("อ่านได้ 2 ข้อความ", log)
        self.assertIn("เก็บใหม่ 2", log)

    async def test_the_message_bodies_never_reach_the_log(self):
        """หัวใจของไฟล์นี้ — นับได้ แต่ห้ามเอาเนื้อความหรือชื่อคนลง log

        ชื่อไฟล์อยู่ใน log ได้ ผู้ใช้เป็นคนตั้งเองและจำเป็นตอนไล่ปัญหา เทสต์นี้จึง
        ใช้ชื่อไฟล์ที่ไม่มีชื่อคนอยู่ เพื่อให้จับได้จริงว่าชื่อผู้ส่งรั่วจากที่อื่น
        """
        _, log = await self._run(doc=document(name="chat.txt"))
        for secret in ("ส่งไฟล์สัญญาให้หน่อย", "เดี๋ยวส่งให้ตอนบ่าย", "Farid"):
            self.assertNotIn(secret, log)

    async def test_a_file_that_parses_to_nothing_is_logged_as_a_failure(self):
        _, log = await self._run(payload="อ่านไม่ออกสักบรรทัด\nบรรทัดที่สอง\n")
        self.assertIn("นำเข้าไฟล์แชทไม่สำเร็จ", log)
        self.assertIn("2 บรรทัด", log)

    async def test_a_rejected_extension_says_why_in_the_log(self):
        _, log = await self._run(doc=document(name="chat.zip"))
        self.assertIn("ปฏิเสธไฟล์แชท", log)
        self.assertNotIn("นำเข้าไฟล์แชทสำเร็จ", log)

    async def test_an_oversized_file_says_why_in_the_log(self):
        big = document(size=bot.MAX_EXPORT_BYTES + 1)
        _, log = await self._run(doc=big)
        self.assertIn("ปฏิเสธไฟล์แชท", log)

    async def test_the_owner_situation_is_recorded(self):
        """เดาเจ้าของไม่ได้ = ทุกข้อความเป็นขาเข้า ต้องเห็นใน log ว่าเกิดขึ้น"""
        _, log = await self._run(caption="ฉันคือ J")
        self.assertIn("เจ้าของ: ผู้ใช้ระบุมา", log)

    async def test_the_rows_actually_land_in_the_database(self):
        await self._run()
        self.assertEqual(len(self.rows("SELECT id FROM chat_messages")), 2)

    async def test_a_command_typed_in_the_caption_is_called_out(self):
        """เงียบไม่ได้ — ผู้ใช้พิมพ์ /rooms ติดมากับไฟล์แล้วรอผลอยู่"""
        update, _ = await self._run(caption="ฉันคือ J /rooms 1 ลิปะน้อย")
        reply = update.message.replies[-1]
        self.assertIn("/rooms", reply)
        self.assertIn("ไม่ได้ทำงาน", reply)

    async def test_the_name_before_that_command_still_counts(self):
        """เตือนเรื่องคำสั่งแล้ว แต่ต้องไม่ทิ้งชื่อที่เขาพิมพ์มาถูก"""
        update, _ = await self._run(caption="ฉันคือ J /rooms 1 ลิปะน้อย")
        reply = update.message.replies[-1]
        self.assertNotIn("ไม่พบชื่อ", reply)
        self.assertNotIn("ไม่รู้ว่าชื่อไหนคือคุณ", reply)
        self.assertEqual(
            [r["direction"] for r in
             self.rows("SELECT direction FROM chat_messages ORDER BY id")],
            ["in", "out"],
        )

    async def test_a_clean_caption_gets_no_command_warning(self):
        update, _ = await self._run(caption="ฉันคือ J")
        self.assertNotIn("ไม่ได้ทำงาน", update.message.replies[-1])


if __name__ == "__main__":
    unittest.main()
