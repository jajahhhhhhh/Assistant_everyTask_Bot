"""
เทสต์การ "ส่ง" การเตือน — ส่วนที่ขาดหายไปตลอด

/remind เก็บลง reminders มาตั้งแต่ต้น และ AsyncIOScheduler ก็ถูก import ไว้ที่หัว
bot.py แต่ไม่เคยมีใครเรียกใช้ ผลคือการเตือนถูกบันทึกไว้แล้วไม่มีอะไรมารับ ไม่เคย
ยิงออกสักครั้ง และไม่มี error ให้เห็นด้วย
"""

import asyncio
import os
import unittest
from datetime import datetime, timedelta

from tests._bot_case import BotDbCase, bot

USER_A = 111
USER_B = 222


class FakeBot:
    """ตัวแทน telegram.Bot — จดว่าถูกสั่งส่งอะไรบ้าง"""

    def __init__(self, fail_with=None):
        self.sent = []
        self._fail_with = fail_with

    async def send_message(self, chat_id, text, **kwargs):
        if self._fail_with is not None:
            raise self._fail_with
        self.sent.append({"chat_id": chat_id, "text": text, "kwargs": kwargs})


class TestDueReminders(BotDbCase):
    async def test_only_reminders_that_have_come_due(self):
        past = await bot.Storage.add_reminder(USER_A, "ถึงแล้ว", datetime.now() - timedelta(minutes=1))
        await bot.Storage.add_reminder(USER_A, "ยังไม่ถึง", datetime.now() + timedelta(hours=1))

        due = await bot.Storage.get_due_reminders()
        self.assertEqual([r["id"] for r in due], [past])

    async def test_overdue_ones_are_included(self):
        """บอทดับไปสองชั่วโมง ของที่ครบกำหนดระหว่างนั้นต้องได้ออกตอนกลับมา"""
        await bot.Storage.add_reminder(USER_A, "ค้างมานาน", datetime.now() - timedelta(hours=2))
        self.assertEqual(len(await bot.Storage.get_due_reminders()), 1)

    async def test_already_sent_ones_are_not_picked_up_again(self):
        reminder_id = await bot.Storage.add_reminder(USER_A, "ส่งไปแล้ว", datetime.now() - timedelta(minutes=1))
        await bot.Storage.mark_reminder(reminder_id, "sent")
        self.assertEqual(await bot.Storage.get_due_reminders(), [])

    async def test_sorted_oldest_first(self):
        later = await bot.Storage.add_reminder(USER_A, "ทีหลัง", datetime.now() - timedelta(minutes=1))
        earlier = await bot.Storage.add_reminder(USER_A, "ก่อน", datetime.now() - timedelta(minutes=10))
        self.assertEqual([r["id"] for r in await bot.Storage.get_due_reminders()], [earlier, later])


class TestDelivery(BotDbCase):
    async def test_sends_to_the_user_who_asked(self):
        await bot.Storage.add_reminder(USER_A, "โทรหาแม่", datetime.now() - timedelta(minutes=1))
        fake = FakeBot()

        self.assertEqual(await bot.deliver_due_reminders(fake), 1)
        self.assertEqual(len(fake.sent), 1)
        self.assertEqual(fake.sent[0]["chat_id"], USER_A)
        self.assertIn("โทรหาแม่", fake.sent[0]["text"])

    async def test_never_uses_markdown(self):
        """เนื้อความมาจากผู้ใช้ — Markdown ที่ไม่จับคู่ทำให้ Telegram ตอบ 400 แล้วหาย"""
        await bot.Storage.add_reminder(USER_A, "ราคา *พิเศษ [ดูที่นี่", datetime.now() - timedelta(minutes=1))
        fake = FakeBot()
        await bot.deliver_due_reminders(fake)

        self.assertNotIn("parse_mode", fake.sent[0]["kwargs"])
        self.assertIn("ราคา *พิเศษ [ดูที่นี่", fake.sent[0]["text"])

    async def test_delivered_ones_are_marked_and_not_repeated(self):
        await bot.Storage.add_reminder(USER_A, "ครั้งเดียวพอ", datetime.now() - timedelta(minutes=1))
        fake = FakeBot()

        await bot.deliver_due_reminders(fake)
        self.assertEqual(await bot.deliver_due_reminders(fake), 0)
        self.assertEqual(len(fake.sent), 1)

    async def test_each_user_gets_only_their_own(self):
        await bot.Storage.add_reminder(USER_A, "ของ A", datetime.now() - timedelta(minutes=1))
        await bot.Storage.add_reminder(USER_B, "ของ B", datetime.now() - timedelta(minutes=1))
        fake = FakeBot()
        await bot.deliver_due_reminders(fake)

        self.assertEqual(
            {(s["chat_id"], s["text"].split("\n")[-1]) for s in fake.sent},
            {(USER_A, "ของ A"), (USER_B, "ของ B")},
        )

    async def test_a_temporary_failure_leaves_it_pending(self):
        """ขัดข้องชั่วคราวต้องได้ลองใหม่ ไม่ใช่ทิ้ง"""
        from telegram.error import TimedOut

        await bot.Storage.add_reminder(USER_A, "ลองใหม่ได้", datetime.now() - timedelta(minutes=1))
        self.assertEqual(await bot.deliver_due_reminders(FakeBot(fail_with=TimedOut())), 0)

        rows = self.rows("SELECT status FROM reminders")
        self.assertEqual(rows[0]["status"], "pending")
        # รอบถัดไปที่ส่งได้ ต้องออกจริง
        self.assertEqual(await bot.deliver_due_reminders(FakeBot()), 1)

    async def test_a_blocked_user_is_given_up_on(self):
        """ผู้ใช้บล็อกบอทแล้ว ลองกี่รอบก็ไม่ผ่าน วนไปเรื่อย ๆ ไม่มีประโยชน์"""
        from telegram.error import Forbidden

        await bot.Storage.add_reminder(USER_A, "บล็อกแล้ว", datetime.now() - timedelta(minutes=1))
        await bot.deliver_due_reminders(FakeBot(fail_with=Forbidden("blocked")))

        self.assertEqual(self.rows("SELECT status FROM reminders")[0]["status"], "failed")
        self.assertEqual(await bot.Storage.get_due_reminders(), [])

    async def test_one_bad_reminder_does_not_stop_the_rest(self):
        from telegram.error import Forbidden

        class OnlyFirstFails(FakeBot):
            async def send_message(self, chat_id, text, **kwargs):
                if chat_id == USER_A:
                    raise Forbidden("blocked")
                await FakeBot.send_message(self, chat_id, text, **kwargs)

        await bot.Storage.add_reminder(USER_A, "คนที่บล็อก", datetime.now() - timedelta(minutes=2))
        await bot.Storage.add_reminder(USER_B, "คนที่ยังรับได้", datetime.now() - timedelta(minutes=1))

        self.assertEqual(await bot.deliver_due_reminders(OnlyFirstFails()), 1)


class TestPollInterval(unittest.TestCase):
    """ค่าที่พิมพ์ผิดในหน้า Variables ต้องไม่ล้มทั้งบอท"""

    def setUp(self):
        self._saved = os.environ.get("REMINDER_POLL_SECONDS")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("REMINDER_POLL_SECONDS", None)
        else:
            os.environ["REMINDER_POLL_SECONDS"] = self._saved

    def read(self, value):
        if value is None:
            os.environ.pop("REMINDER_POLL_SECONDS", None)
        else:
            os.environ["REMINDER_POLL_SECONDS"] = value
        return bot._reminder_poll_seconds()

    def test_unset_uses_the_default(self):
        self.assertEqual(self.read(None), bot.DEFAULT_REMINDER_POLL_SECONDS)

    def test_a_normal_value_is_honoured(self):
        self.assertEqual(self.read("120"), 120)

    def test_garbage_falls_back_instead_of_crashing(self):
        """'30s' ในหน้า Variables ต้องไม่ทำให้บอทบูตไม่ขึ้น"""
        for value in ("30s", "", "ครึ่งนาที", "1.5"):
            with self.subTest(value=value):
                self.assertEqual(self.read(value), bot.DEFAULT_REMINDER_POLL_SECONDS)

    def test_zero_and_negative_are_clamped(self):
        """0 ทำให้ APScheduler ยิงรัวไม่หยุด"""
        self.assertEqual(self.read("0"), 1)
        self.assertEqual(self.read("-5"), 1)


class TestSchedulerIsActuallyWired(unittest.IsolatedAsyncioTestCase):
    """เส้นทางบูตจริงต้องเริ่มตัวส่งการเตือน

    นี่คือเทสต์ที่จะจับบั๊กเดิมได้ ตัวส่งเคย "มีอยู่" ในความตั้งใจ แต่ไม่มีใคร
    เรียก จึงไม่มีอะไรพัง ไม่มี error และไม่มีการเตือนออกสักครั้ง
    """

    def tearDown(self):
        bot.stop_reminder_scheduler()

    async def test_start_schedules_the_delivery_job(self):
        class FakeApplication:
            bot = FakeBot()

        scheduler = bot.start_reminder_scheduler(FakeApplication())
        try:
            job = scheduler.get_job("deliver_due_reminders")
            self.assertIsNotNone(job, "ต้องมีงานส่งการเตือนอยู่ในตารางเวลา")
            self.assertIs(job.func, bot.deliver_due_reminders)
            self.assertEqual(job.trigger.interval.total_seconds(), bot.REMINDER_POLL_SECONDS)
            # รอบที่ค้างต้องยุบรวม ไม่ใช่ยิงรัวตามหลัง
            self.assertTrue(job.coalesce)
            self.assertEqual(job.max_instances, 1)
        finally:
            bot.stop_reminder_scheduler()

    async def test_stop_removes_the_job(self):
        class FakeApplication:
            bot = FakeBot()

        scheduler = bot.start_reminder_scheduler(FakeApplication())
        bot.stop_reminder_scheduler()
        # APScheduler 3.11 ยังรายงาน running เป็น True หลัง shutdown จึงตรวจสิ่งที่
        # สังเกตได้จริงแทน คือไม่เหลืองานให้ยิงอีก
        self.assertEqual(scheduler.get_jobs(), [])

    async def test_starting_twice_does_not_leave_two_running(self):
        """สองตัววิ่งพร้อมกัน = ผู้ใช้ได้การเตือนซ้ำ และตัวเก่าปิดไม่ได้อีกเลย"""
        class FakeApplication:
            bot = FakeBot()

        first = bot.start_reminder_scheduler(FakeApplication())
        second = bot.start_reminder_scheduler(FakeApplication())
        try:
            self.assertIsNot(first, second)
            self.assertEqual(first.get_jobs(), [], "ตัวเดิมต้องถูกปิดไปแล้ว")
            self.assertIsNotNone(second.get_job("deliver_due_reminders"))
        finally:
            bot.stop_reminder_scheduler()

    async def test_stopping_twice_is_harmless(self):
        bot.stop_reminder_scheduler()
        bot.stop_reminder_scheduler()

    async def test_the_production_boot_path_starts_it(self):
        """app.py ใช้ initialize() + start_polling() ซึ่งไม่เรียก post_init

        ถ้าไปพึ่ง post_init ตัวส่งจะไม่เริ่มเลยตอน deploy จริง โดยไม่มีอะไรฟ้อง
        """
        import inspect
        import app

        source = inspect.getsource(app.start_telegram)
        self.assertIn("start_reminder_scheduler", source)
        self.assertIn("stop_reminder_scheduler", inspect.getsource(app.run))


if __name__ == "__main__":
    unittest.main()
