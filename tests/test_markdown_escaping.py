"""
เทสต์ว่าข้อความของผู้ใช้ถูก escape ก่อนส่งด้วย parse_mode="Markdown"

Telegram ปฏิเสธ "ทั้งข้อความ" ด้วย 400 Can't parse entities ถ้ามี _ * ` [ ที่ไม่
จับคู่ ผลคืองานชื่อ "fix user_id" ไม่มีคำยืนยันกลับมา และที่แย่กว่านั้นคือ /tasks
ที่มีงานแบบนั้นอยู่แถวเดียว จะพังทั้งรายการ ไม่ใช่แค่แถวนั้น
"""

import unittest
from types import SimpleNamespace

from tests._bot_case import BotDbCase, bot

USER_A = 111

# ข้อความจริงที่คนพิมพ์กันแล้วทำให้ Telegram ปฏิเสธ
BREAKING = "fix user_id"
BREAKING_ESCAPED = "fix user\\_id"


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append({"text": text, **kwargs})


class FakeUpdate:
    def __init__(self, user_id=USER_A, first_name="J"):
        self.effective_user = SimpleNamespace(id=user_id, first_name=first_name)
        self.message = FakeMessage()


class FakeContext:
    def __init__(self, *args):
        self.args = list(args)


class TestEscapeHelpers(unittest.TestCase):
    def test_every_special_is_escaped(self):
        for char in ("_", "*", "`", "["):
            with self.subTest(char=char):
                self.assertEqual(bot.escape_md(f"a{char}b"), f"a\\{char}b")

    def test_ordinary_text_is_untouched(self):
        self.assertEqual(bot.escape_md("ซื้อของที่ตลาด"), "ซื้อของที่ตลาด")

    def test_none_becomes_empty(self):
        """ค่าจากฐานข้อมูลเป็น NULL ได้ ต้องไม่ได้คำว่า None ไปโชว์"""
        self.assertEqual(bot.escape_md(None), "")
        self.assertEqual(bot.escape_code(None), "")

    def test_numbers_survive(self):
        self.assertEqual(bot.escape_md(42), "42")

    def test_code_span_only_needs_the_backtick_gone(self):
        """ข้างใน code span มีแค่ backtick ที่ปิด span ก่อนเวลา"""
        self.assertEqual(bot.escape_code("a`b"), "a'b")
        self.assertEqual(bot.escape_code("keep_the_underscore"), "keep_the_underscore")


class TestCommandsEscapeWhatTheyEcho(BotDbCase):
    async def reply_to(self, handler, *args, update=None):
        update = update or FakeUpdate()
        await handler(update, FakeContext(*args))
        return update.message.replies[-1]

    async def test_adding_a_task_escapes_the_title(self):
        reply = await self.reply_to(bot.task_command, "fix", "user_id")
        self.assertEqual(reply.get("parse_mode"), "Markdown")
        self.assertIn(BREAKING_ESCAPED, reply["text"])
        self.assertNotIn(BREAKING, reply["text"])

    async def test_one_bad_task_does_not_break_the_whole_list(self):
        """นี่คืออาการที่แย่ที่สุด — งานแถวเดียวทำให้ /tasks พังทั้งหน้า"""
        await bot.Storage.add_task(USER_A, BREAKING)
        await bot.Storage.add_task(USER_A, "งานปกติ")

        reply = await self.reply_to(bot.tasks_command)
        self.assertIn(BREAKING_ESCAPED, reply["text"])
        self.assertIn("งานปกติ", reply["text"])
        self.assertEqual(reply["text"].count("_"), reply["text"].count("\\_"))

    async def test_setting_a_reminder_escapes_the_text(self):
        reply = await self.reply_to(bot.remind_command, "30m", "ping", "user_id")
        self.assertIn("user\\_id", reply["text"])

    async def test_listing_reminders_escapes_the_text(self):
        from datetime import datetime

        await bot.Storage.add_reminder(USER_A, BREAKING, datetime(2026, 9, 1, 8, 0))
        reply = await self.reply_to(bot.reminders_command)
        self.assertIn(BREAKING_ESCAPED, reply["text"])

    async def test_listing_notes_escapes_the_content(self):
        await bot.Storage.add_note(USER_A, BREAKING)
        reply = await self.reply_to(bot.notes_command)
        self.assertIn(BREAKING_ESCAPED, reply["text"])

    async def test_start_escapes_the_display_name(self):
        """ชื่อที่ตั้งใน Telegram มี _ * ได้ และคนตั้งกันจริง"""
        update = FakeUpdate(first_name="J_*")
        await bot.start_command(update, FakeContext())
        self.assertIn("J\\_\\*", update.message.replies[-1]["text"])

    async def test_a_title_that_is_only_markdown_still_works(self):
        reply = await self.reply_to(bot.task_command, "***")
        self.assertIn("\\*\\*\\*", reply["text"])


class TestMyStorageSurvivesNulls(BotDbCase):
    """คอลัมน์ในฐานข้อมูลเป็น NULL ได้ ค่าเริ่มต้นของ .get() ช่วยไม่ได้

    .get(key, default) คืน default เฉพาะตอน "ไม่มีคีย์" แต่แถวในฐานข้อมูลมีคีย์
    ครบเสมอ เพียงแต่ค่าเป็น None ได้ แล้ว None[:20] โยน TypeError ส่วน
    escape_code(None) ก็คืนช่องว่างเปล่า ๆ แทนที่จะบอกว่ายังไม่ได้ตั้ง
    """

    async def show(self):
        update = FakeUpdate()
        await bot.mystorage_command(update, FakeContext())
        return update.message.replies[-1]["text"]

    async def test_sheets_without_an_id_does_not_crash(self):
        bot.StorageSettings.set_storage_type(USER_A, "sheets")
        self.assertIn("ยังไม่ได้ตั้ง", await self.show())

    async def test_airtable_without_a_base_says_so_instead_of_blank(self):
        bot.StorageSettings.set_storage_type(USER_A, "airtable")
        self.assertIn("ยังไม่ได้ตั้ง", await self.show())

    async def test_a_configured_sheet_is_shown_truncated(self):
        bot.StorageSettings.set_google_sheets(USER_A, "1" * 40)
        self.assertIn("1" * 20 + "...", await self.show())


class TestNoUnescapedUserTextRemains(unittest.TestCase):
    """กันไม่ให้มีจุดใหม่หลุดไป

    เดินด้วย AST ไม่ใช่นับบรรทัด — จับเฉพาะค่าที่อยู่ใน f-string ของ "การเรียก
    ส่งข้อความที่มี parse_mode='Markdown' จริง ๆ" การกวาดด้วยหน้าต่างบรรทัดให้
    false positive กับข้อความข้างเคียงที่ส่งแบบ plain text
    """

    SEND_METHODS = {"reply_text", "send_message", "edit_message_text"}

    # ค่าที่เราคุมเองทั้งหมด ไม่มีทางมีอักขระ Markdown จากผู้ใช้
    ALLOWED_BARE = {
        "priority_emoji.get(priority, '⚪')",   # emoji จาก dict ของเราเอง
        "emoji",                                # ตัวเดียวกัน ผ่านตัวแปร
        "priority",                             # 'urgent'|'high'|'medium'|'low'
        "task_id", "note_id", "t['id']", "n['id']", "r['id']",   # เลขจากฐานข้อมูล
        "thread_id",                            # int(context.args[0]) หลัง isdigit()
        "moved",                                # COUNT(*) จาก sqlite — int เสมอ
        "len(done)", "voice.duration",
        "remind_at.strftime('%Y-%m-%d %H:%M')", "r['remind_at']",  # เวลา ISO
        "icons.get(storage_type, '📱')", "storage_type.title()", "current.title()",
        "LANGUAGES[target_lang]", "LANGUAGES.get(lang_code, lang_code)",
        "name", "code", "k", "v",               # ค่าคงที่ใน LANGUAGES
        "lang_list",                            # ประกอบจาก LANGUAGES ล้วน
        "'✅ ' if current == 'sheets' else ''",
        "'✅ ' if current == 'airtable' else ''",
        "'✅ ' if current == 'local' else ''",
    }

    def test_every_interpolation_is_escaped_or_known_safe(self):
        import ast
        from pathlib import Path

        tree = ast.parse(Path(bot.__file__).read_text(encoding="utf-8"))
        offenders = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr in self.SEND_METHODS):
                continue
            markdown = any(
                kw.arg == "parse_mode"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value == "Markdown"
                for kw in node.keywords
            )
            if not markdown:
                continue

            # ตรวจทั้ง positional และ keyword — reply_text(text=f"...") ต้องไม่หลุด
            candidates = list(node.args) + [
                kw.value for kw in node.keywords if kw.arg != "parse_mode"
            ]
            for arg in candidates:
                for piece in ast.walk(arg):
                    if not isinstance(piece, ast.FormattedValue):
                        continue
                    expr = ast.unparse(piece.value).strip()
                    if expr.startswith(("escape_md(", "escape_code(")):
                        continue
                    if expr in self.ALLOWED_BARE:
                        continue
                    offenders.append(f"บรรทัด {piece.lineno}: {{{expr}}}")

        self.assertEqual(
            offenders, [],
            "มีค่าที่ส่งลง Markdown โดยไม่ผ่าน escape — ถ้าปลอดภัยจริงให้เพิ่มเข้า "
            "ALLOWED_BARE พร้อมคอมเมนต์บอกเหตุผล",
        )

    def test_the_guard_covers_keyword_arguments_too(self):
        """reply_text(text=f"...") ต้องไม่หลุดตัวกันไปได้"""
        import ast

        tree = ast.parse(
            'update.message.reply_text(text=f"hi {title}", parse_mode="Markdown")'
        )
        call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
        candidates = list(call.args) + [
            kw.value for kw in call.keywords if kw.arg != "parse_mode"
        ]
        found = [
            ast.unparse(p.value)
            for arg in candidates
            for p in ast.walk(arg)
            if isinstance(p, ast.FormattedValue)
        ]
        self.assertEqual(found, ["title"])

    def test_the_guard_would_notice_a_new_offender(self):
        """ตัวกันที่จับอะไรไม่ได้เลย ก็ไม่ต่างจากไม่มี"""
        import ast

        tree = ast.parse(
            'await update.message.reply_text(f"hi {title}", parse_mode="Markdown")'
        )
        found = [
            ast.unparse(p.value)
            for p in ast.walk(tree)
            if isinstance(p, ast.FormattedValue)
        ]
        self.assertEqual(found, ["title"])
        self.assertNotIn("title", self.ALLOWED_BARE)


if __name__ == "__main__":
    unittest.main()
