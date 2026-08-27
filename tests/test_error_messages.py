"""
เทสต์ว่าข้อความ error จากบริการภายนอกไม่หลุดไปถึงผู้ใช้

อาการจริงที่ทำให้ต้องมีไฟล์นี้: ใส่ค่าผิดช่องใน Railway ทำให้ OPENAI_API_KEY
ไม่ใช่คีย์ของ OpenAI พอมีคนส่งข้อความเสียง OpenAI ตอบกลับมาว่า

    401 - Incorrect API key provided: 86662002****...****3RAI

แล้วบอทเอาข้อความนั้นไปโพสต์ลงแชต Telegram ทั้งดุ้น ค้างอยู่ในประวัติแชตถาวร
ข้อความจากผู้ให้บริการภายนอกไม่ควรถูกส่งต่อดิบ ๆ ไม่ว่ากรณีใด
"""

import logging
import unittest
from types import SimpleNamespace

from tests._bot_case import bot

# รูปแบบเดียวกับที่ OpenAI ตอบกลับมาจริง ย่อให้สั้นลง
OPENAI_401 = (
    "Error code: 401 - {'error': {'message': 'Incorrect API key provided: "
    "86662002****3RAI. You can find your API key at ...', "
    "'code': 'invalid_api_key'}}"
)


class _StatusError(Exception):
    """เลียนแบบ exception ของไลบรารีที่แนบ status_code มาให้"""

    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


class TestAuthErrorDetection(unittest.TestCase):
    def test_status_code_401_counts(self):
        self.assertTrue(bot._looks_like_auth_error(_StatusError("nope", 401)))

    def test_message_markers_count_when_no_status_is_attached(self):
        for text in (
            OPENAI_401,
            "invalid_api_key",
            "Invalid Authentication",
            "401 Unauthorized",
        ):
            with self.subTest(text=text[:30]):
                self.assertTrue(bot._looks_like_auth_error(RuntimeError(text)))

    def test_ordinary_failures_do_not_count(self):
        for text in ("rate limited", "connection reset by peer", "timeout"):
            with self.subTest(text=text):
                self.assertFalse(bot._looks_like_auth_error(RuntimeError(text)))

    def test_a_status_of_500_is_not_an_auth_error(self):
        self.assertFalse(bot._looks_like_auth_error(_StatusError("boom", 500)))


class TestApiFailureMessage(unittest.TestCase):
    def setUp(self):
        self._records = []

    def _capture(self):
        return self.assertLogs("bot", level="ERROR")

    def test_the_masked_key_never_reaches_the_user(self):
        """หัวใจของไฟล์นี้ — เศษของคีย์ต้องไม่โผล่ในข้อความที่ผู้ใช้เห็น"""
        with self._capture():
            message = bot.api_failure_message(
                RuntimeError(OPENAI_401), "ถอดเสียง", "OPENAI_API_KEY"
            )
        for fragment in ("86662002", "3RAI", "Incorrect API key"):
            self.assertNotIn(fragment, message)

    def test_an_auth_failure_names_the_variable_to_check(self):
        with self._capture():
            message = bot.api_failure_message(
                RuntimeError(OPENAI_401), "ถอดเสียง", "OPENAI_API_KEY"
            )
        self.assertIn("OPENAI_API_KEY", message)
        self.assertIn("ถอดเสียง", message)

    def test_an_auth_failure_without_a_named_variable_stays_generic(self):
        with self._capture():
            message = bot.api_failure_message(RuntimeError(OPENAI_401), "เชื่อมต่อ Drive")
        self.assertIn("credential", message)
        self.assertNotIn("86662002", message)

    def test_an_ordinary_failure_says_to_look_in_the_log(self):
        with self._capture():
            message = bot.api_failure_message(RuntimeError("connection reset"), "แปลภาษา")
        self.assertNotIn("connection reset", message)
        self.assertIn("log", message)

    def test_the_detail_is_written_to_the_log(self):
        """ตัดออกจากผู้ใช้แล้วต้องไปโผล่ที่ log ไม่ใช่หายไปเฉย ๆ"""
        with self.assertLogs("bot", level="ERROR") as captured:
            bot.api_failure_message(RuntimeError("rate limited"), "แปลภาษา")
        blob = "\n".join(captured.output)
        self.assertIn("rate limited", blob)
        self.assertIn("แปลภาษา", blob)


class TestNoRawExceptionsRemain(unittest.TestCase):
    def test_no_reply_path_interpolates_an_exception_variable(self):
        """กันไม่ให้ str(e) กลับเข้ามาใหม่ในอนาคต

        มองหารูปแบบที่เคยมีจริงทั้งหกจุด — str(e) และ {e} ในสตริงที่ส่งออก
        """
        import ast
        import pathlib

        source = pathlib.Path(bot.__file__).read_text()
        tree = ast.parse(source)

        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            rendered = ast.unparse(node)
            if "str(e)" in rendered or "{e}" in rendered:
                offenders.append(f"บรรทัด {node.lineno}: {rendered[:60]}")

        self.assertEqual(
            offenders, [],
            "มี exception ถูกยัดลงสตริง — ต้องผ่าน api_failure_message แทน",
        )


if __name__ == "__main__":
    unittest.main()
