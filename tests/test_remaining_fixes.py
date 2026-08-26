"""
เทสต์ของที่เหลือจาก PR #5 และบั๊กที่เจอระหว่างตรวจ

  1. detect_priority  — เดิมจับแบบ substring "highlight" จึงกลายเป็น high
  2. handle_voice     — ไฟล์ .ogg ค้างทุกครั้งที่ถอดเสียงล้มเหลว
  3. /cancel          — ไม่มีทางออกจากโหมดตั้งค่า

(/mystorage ที่โยน TypeError เมื่อคอลัมน์เป็น NULL ย้ายไปแก้ใน #17 เพราะ PR นั้น
แตะบรรทัดเดียวกันและรีวิวชี้ที่นั่น)
"""

import os
import unittest
from types import SimpleNamespace

from tests._bot_case import BotDbCase, bot

USER_A = 111


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, user_id=USER_A, voice=None):
        self.effective_user = SimpleNamespace(id=user_id, first_name="J")
        self.message = FakeMessage()
        self.message.voice = voice


class TestDetectPriority(unittest.TestCase):
    def test_the_words_themselves_still_work(self):
        self.assertEqual(bot.detect_priority("urgent meeting"), "urgent")
        self.assertEqual(bot.detect_priority("important call"), "high")
        self.assertEqual(bot.detect_priority("low effort"), "low")
        self.assertEqual(bot.detect_priority("do it later"), "low")

    def test_thai_keywords_still_match_inside_words(self):
        """ภาษาไทยเขียนติดกัน ขอบคำใช้ไม่ได้"""
        self.assertEqual(bot.detect_priority("ประชุมด่วน"), "urgent")
        self.assertEqual(bot.detect_priority("เรื่องสำคัญมาก"), "high")
        self.assertEqual(bot.detect_priority("งานต่ำกว่าที่คิด"), "low")

    def test_the_first_matching_rule_wins(self):
        """ข้อจำกัดที่ยังอยู่ ไม่ได้แก้ใน PR นี้

        กฎถูกไล่ตามลำดับ urgent → high → low ประโยคที่มีทั้งสองคำจึงได้อันแรก
        "ความสำคัญต่ำ" เลยกลายเป็น high ทั้งที่ความหมายคือสำคัญน้อย เขียนไว้ให้
        เห็นชัด ๆ ว่ารู้อยู่ ไม่ใช่เผลอ — การอ่านความหมายจริงต้องใช้วิธีอื่น
        """
        self.assertEqual(bot.detect_priority("ความสำคัญต่ำ"), "high")

    def test_words_that_merely_contain_a_keyword_are_not_matched(self):
        """อาการเดิม — จัดลำดับผิดโดยไม่มีใครรู้"""
        for title in ("ทำ highlight ให้ลูกค้า", "วาด flowchart", "ซื้อ pillow",
                      "highlights ของเดือนนี้", "lower the price"):
            with self.subTest(title=title):
                self.assertEqual(bot.detect_priority(title), "medium")

    def test_an_exclamation_mark_means_urgent(self):
        self.assertEqual(bot.detect_priority("ส่งของ!"), "urgent")

    def test_plain_titles_are_medium(self):
        self.assertEqual(bot.detect_priority("ซื้อของที่ตลาด"), "medium")

    def test_empty_and_none_do_not_crash(self):
        self.assertEqual(bot.detect_priority(""), "medium")
        self.assertEqual(bot.detect_priority(None), "medium")

    def test_urgent_wins_over_the_others(self):
        self.assertEqual(bot.detect_priority("urgent but low effort"), "urgent")


class TestPriorityReachesStorage(BotDbCase):
    async def test_a_task_named_highlight_is_not_high_priority(self):
        update = FakeUpdate()
        await bot.task_command(update, SimpleNamespace(args=["ทำ", "highlight"]))
        self.assertEqual((await bot.Storage.get_tasks(USER_A))[0]["priority"], "medium")

    async def test_a_genuinely_urgent_task_still_is(self):
        update = FakeUpdate()
        await bot.task_command(update, SimpleNamespace(args=["urgent", "fix"]))
        self.assertEqual((await bot.Storage.get_tasks(USER_A))[0]["priority"], "urgent")


class TestVoiceTempFile(BotDbCase):
    """ไฟล์ .ogg ต้องถูกลบแม้ถอดเสียงล้มเหลว"""

    async def run_voice(self, transcribe):
        created = []

        async def fake_get_file(file_id):
            async def download_to_drive(path):
                created.append(path)
                with open(path, "wb") as handle:
                    handle.write(b"ogg")
            return SimpleNamespace(download_to_drive=download_to_drive)

        original = bot.transcribe_voice
        bot.transcribe_voice = transcribe
        try:
            update = FakeUpdate(voice=SimpleNamespace(file_id="f1", duration=3))
            context = SimpleNamespace(bot=SimpleNamespace(get_file=fake_get_file))
            await bot.handle_voice(update, context)
        finally:
            bot.transcribe_voice = original
        return created, update.message.replies

    async def test_the_file_is_removed_after_a_successful_run(self):
        async def ok(path):
            return "ถอดเสียงได้"

        created, replies = await self.run_voice(ok)
        self.assertEqual(len(created), 1)
        self.assertFalse(os.path.exists(created[0]), "ไฟล์ชั่วคราวต้องถูกลบ")
        self.assertIn("ถอดเสียงได้", replies[-1])

    async def test_the_file_is_removed_when_transcription_fails(self):
        """อาการเดิม — os.unlink อยู่หลังบรรทัดที่พัง ไฟล์จึงค้างทุกครั้ง"""
        async def boom(path):
            raise RuntimeError("โควตาหมด")

        created, replies = await self.run_voice(boom)
        self.assertEqual(len(created), 1)
        self.assertFalse(os.path.exists(created[0]), "ล้มเหลวแล้วไฟล์ยังต้องถูกลบ")
        self.assertIn("Could not transcribe", replies[-1])


class TestCancel(BotDbCase):
    async def cancel(self):
        update = FakeUpdate()
        await bot.cancel_command(update, SimpleNamespace(args=[]))
        return update.message.replies[-1]

    async def test_leaving_setup_mode(self):
        bot.user_setup_state[USER_A] = {"type": "airtable", "step": 1}
        self.assertIn("ยกเลิก", await self.cancel())
        self.assertNotIn(USER_A, bot.user_setup_state)

    async def test_cancelling_when_nothing_is_in_progress(self):
        bot.user_setup_state.pop(USER_A, None)
        self.assertIn("ไม่มีอะไรให้ยกเลิก", await self.cancel())

    def test_the_command_is_registered(self):
        """เดิมไม่มี handler เลย พิมพ์ /cancel แล้วเงียบ"""
        import inspect

        source = inspect.getsource(bot.build_application)
        self.assertIn('CommandHandler("cancel", cancel_command)', source)


if __name__ == "__main__":
    unittest.main()
