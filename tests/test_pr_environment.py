"""
บอทตัวจริงต้องไม่ถูกแย่งข้อความโดยสำเนาของตัวเองที่ Railway สร้างให้ pull request

Telegram ให้มีผู้เรียก getUpdates ได้ทีละรายต่อโทเคน ใครขอก่อนได้ไป ข้อความนั้น
หายจากคิวทันที environment ของ PR ถูกคัดลอกตัวแปรมาจาก project ทั้งชุด รวมถึง
TELEGRAM_BOT_TOKEN มันจึงกลายเป็นผู้เรียกรายที่สอง

ของจริงที่เสียไปแล้ว: ประวัติแชท Renovate 325 ข้อความที่นำเข้าไประหว่างที่ PR #37
เปิดอยู่ ถูก environment pr-37 รับไปทั้งชุด แล้วหายพร้อมกับมันตอน merge
"""

import unittest
from unittest import mock

import app


class TestPrEnvironmentIsRecognised(unittest.TestCase):
    def test_railway_pr_environment_names_are_caught(self):
        for name in (
            "Assistant_everyTask_Bot-pr-39",
            "Assistant_everyTask_Bot-pr-37",
            "staging-pr-1",
            "  Assistant_everyTask_Bot-pr-412  ",     # ช่องว่างหัวท้าย
        ):
            with self.subTest(name=name):
                self.assertTrue(app.is_pr_environment(name))

    def test_everything_else_keeps_polling(self):
        """ผิดพลาดทางนี้แค่ข้อความซ้ำ ผิดอีกทางคือบอทตัวจริงเงียบไปทั้งตัว"""
        for name in (
            "production",
            "staging",
            "",
            None,
            "pr",                       # ไม่มีเลข
            "my-pr-branch",             # ไม่มีเลข
            "pr-39-production",         # เลขไม่ได้อยู่ท้าย
            "Assistant_everyTask_Bot",
        ):
            with self.subTest(name=name):
                with mock.patch.dict(app.os.environ, {}, clear=True):
                    self.assertFalse(app.is_pr_environment(name))

    def test_it_reads_the_environment_when_no_name_is_given(self):
        with mock.patch.dict(
            app.os.environ, {"RAILWAY_ENVIRONMENT_NAME": "repo-pr-7"}
        ):
            self.assertTrue(app.is_pr_environment())

    def test_an_unset_variable_still_polls(self):
        with mock.patch.dict(app.os.environ, {}, clear=True):
            self.assertFalse(app.is_pr_environment())


class TestStartTelegramRefusesInAPrEnvironment(unittest.IsolatedAsyncioTestCase):
    async def test_it_returns_without_touching_telegram(self):
        with mock.patch.dict(
            app.os.environ, {"RAILWAY_ENVIRONMENT_NAME": "repo-pr-39"}
        ):
            with mock.patch("bot.build_application") as build:
                with self.assertLogs("app", level="WARNING") as captured:
                    self.assertIsNone(await app.start_telegram())
        build.assert_not_called()
        self.assertIn("pull request", "\n".join(captured.output))

    async def test_a_normal_environment_still_starts_polling(self):
        with mock.patch.dict(
            app.os.environ, {"RAILWAY_ENVIRONMENT_NAME": "production"}
        ):
            with mock.patch("bot.BOT_TOKEN", ""):
                # ไม่มีโทเคน → คืน None ด้วยเหตุผลเดิม ไม่ใช่เพราะกันไว้
                with self.assertLogs("app", level="WARNING") as captured:
                    self.assertIsNone(await app.start_telegram())
        self.assertIn("TELEGRAM_BOT_TOKEN", "\n".join(captured.output))
        self.assertNotIn("pull request", "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
