"""
เทสต์บริการ AI ของ bot.py — แปลภาษาและถอดเสียง

(ไฟล์เดิมเทสต์ assistant/translator.py ซึ่งถูกลบไปแล้ว ตอนนี้ทั้งสองอย่างอยู่ใน
bot.py และเรียก OpenAI ผ่านตัวแปรโมดูล `client` จึงสลับเป็นตัวปลอมได้ในเทสต์
ไม่มีเทสต์ไหนต่อเน็ตจริง)
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot


class FakeCompletions:
    def __init__(self, reply="สวัสดีโลก", error=None):
        self.reply = reply
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        message = type("Message", (), {"content": self.reply})
        choice = type("Choice", (), {"message": message})
        return type("Response", (), {"choices": [choice]})


class FakeTranscriptions:
    def __init__(self, text="ทดสอบเสียง", error=None):
        self.text = text
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.text


class FakeClient:
    def __init__(self, completions=None, transcriptions=None):
        self.chat = type("Chat", (), {"completions": completions or FakeCompletions()})
        self.audio = type("Audio", (), {"transcriptions": transcriptions or FakeTranscriptions()})


class ClientCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._original_client = bot.client

    async def asyncTearDown(self):
        bot.client = self._original_client


class TestLanguages(unittest.TestCase):
    def test_common_codes_are_present(self):
        for code in ("en", "th", "ja", "zh"):
            self.assertIn(code, bot.LANGUAGES)

    def test_every_entry_has_a_display_name(self):
        for code, name in bot.LANGUAGES.items():
            self.assertTrue(name.strip(), code)
            self.assertRegex(code, r"^[a-z]{2}$")


class TestTranslate(ClientCase):
    async def test_returns_the_model_output_trimmed(self):
        completions = FakeCompletions(reply="  สวัสดีโลก \n")
        bot.client = FakeClient(completions=completions)

        self.assertEqual(await bot.translate_text("Hello world", "th"), "สวัสดีโลก")

    async def test_target_language_name_reaches_the_prompt(self):
        completions = FakeCompletions()
        bot.client = FakeClient(completions=completions)

        await bot.translate_text("Hello", "ja")
        system_prompt = completions.calls[0]["messages"][0]["content"]
        self.assertIn(bot.LANGUAGES["ja"], system_prompt)
        self.assertEqual(completions.calls[0]["messages"][1]["content"], "Hello")

    async def test_unknown_language_code_is_passed_through(self):
        completions = FakeCompletions()
        bot.client = FakeClient(completions=completions)

        await bot.translate_text("Hello", "xx")
        self.assertIn("xx", completions.calls[0]["messages"][0]["content"])

    async def test_without_a_configured_client(self):
        bot.client = None
        self.assertIn("not configured", await bot.translate_text("Hello", "th"))

    async def test_api_errors_are_reported_without_leaking_the_detail(self):
        """เดิมส่ง str(exc) กลับให้ผู้ใช้ ซึ่งพาของที่ไม่ควรออกไปด้วย

        ตอน OPENAI_API_KEY ผิด OpenAI ตอบกลับพร้อมคีย์ที่มาสก์ไว้บางส่วน แล้วบอท
        ก็โพสต์ลงแชต Telegram ให้ค้างอยู่ในประวัติถาวร
        """
        bot.client = FakeClient(completions=FakeCompletions(error=RuntimeError("rate limited")))
        with self.assertLogs("bot", level="ERROR"):
            result = await bot.translate_text("Hello", "th")
        self.assertNotIn("rate limited", result)
        self.assertIn("แปลภาษาไม่สำเร็จ", result)


class TestTranscribe(ClientCase):
    async def test_returns_the_transcript(self):
        bot.client = FakeClient(transcriptions=FakeTranscriptions(text=" ทดสอบเสียง "))
        with tempfile.NamedTemporaryFile(suffix=".ogg") as audio:
            audio.write(b"not really audio")
            audio.flush()
            self.assertEqual(await bot.transcribe_voice(audio.name), "ทดสอบเสียง")

    async def test_without_a_configured_client(self):
        bot.client = None
        self.assertIn("not configured", await bot.transcribe_voice("/tmp/whatever.ogg"))

    async def test_failures_propagate_so_the_caller_can_tell_them_apart(self):
        """เดิมกลืน exception แล้วคืนข้อความ error เป็น "ผลการถอดเสียง"

        ผลคือความล้มเหลวถูกบันทึกลงตาราง transcriptions เหมือนเป็นข้อความจริง
        แล้วแสดงใต้หัวข้อ Voice Transcription ราวกับสำเร็จ
        """
        bot.client = FakeClient()
        with self.assertRaises(OSError):
            await bot.transcribe_voice("/tmp/does-not-exist.ogg")


if __name__ == "__main__":
    unittest.main()
