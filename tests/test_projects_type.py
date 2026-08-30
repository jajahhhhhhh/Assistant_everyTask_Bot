"""
projects.type ต้องรับชื่อธุรกิจใหม่ได้โดยไม่ต้องแก้ schema

รายการตายตัวเดิม ('chowrest','niksen','barbar','b52','personal') ล้าสมัยไปแล้ว
จริง ๆ — b52 ปิดไป barbar ไม่ได้ทำต่อ ส่วน Airbnb ซึ่งเป็นรายได้หลักช่องทางเดียว
ที่มีอยู่ตอนนี้ ไม่เคยอยู่ในรายการเลย
"""

import sqlite3
import unittest
from pathlib import Path

SCHEMA = Path(__file__).resolve().parent.parent / "sql" / "01_schema.sql"


class TestProjectType(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(SCHEMA.read_text(encoding="utf-8"))

    def tearDown(self):
        self.conn.close()

    def insert(self, name, type_):
        with self.conn:
            self.conn.execute(
                "INSERT INTO projects (name, type) VALUES (?, ?)", (name, type_)
            )

    def test_a_business_line_the_schema_never_heard_of_is_accepted(self):
        self.insert("Airbnb Host", "airbnb")
        row = self.conn.execute("SELECT type FROM projects WHERE name = ?", ("Airbnb Host",)).fetchone()
        self.assertEqual(row[0], "airbnb")

    def test_the_original_types_still_work(self):
        for index, type_ in enumerate(("chowrest", "niksen", "personal")):
            with self.subTest(type=type_):
                self.insert(f"งาน {index}", type_)

    def test_an_empty_type_is_still_refused(self):
        """ยังต้องบังคับให้ระบุอะไรสักอย่าง ไม่ใช่ปล่อยผ่านทุกกรณี"""
        for bad in ("", "   "):
            with self.subTest(value=repr(bad)):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.insert(f"งาน{bad!r}", bad)

    def test_the_default_the_bot_uses_when_creating_a_room_still_passes(self):
        import bot
        self.insert("ห้องใหม่", bot.DEFAULT_PROJECT_TYPE)
