"""
ฐานร่วมของเทสต์ที่แตะฐานข้อมูลของ bot.py

bot.py อ่าน DB_PATH ตอนเรียกใช้ ไม่ได้ผูกไว้ตอน import จึงชี้ไปไฟล์ชั่วคราวได้
ด้วยการเปลี่ยนค่าโมดูล ไม่ต้องแก้โครงของ bot.py
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot


class BotDbCase(unittest.IsolatedAsyncioTestCase):
    """เตรียมฐานข้อมูลเปล่าหนึ่งไฟล์ต่อหนึ่งเทสต์"""

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._original_db_path = bot.DB_PATH
        bot.DB_PATH = str(Path(self._tmp.name) / "test.db")
        bot.init_db()

    async def asyncTearDown(self):
        bot.DB_PATH = self._original_db_path
        self._tmp.cleanup()

    def add_project(self, name, kind="personal"):
        """projects.type มี CHECK ห้าค่า เทสต์จึงต้องใส่ค่าที่ผ่านจริง"""
        import sqlite3

        conn = sqlite3.connect(bot.DB_PATH)
        try:
            with conn:
                cursor = conn.execute(
                    "INSERT INTO projects (name, type) VALUES (?, ?)", (name, kind)
                )
                return int(cursor.lastrowid)
        finally:
            conn.close()

    def rows(self, sql, params=()):
        import sqlite3

        conn = sqlite3.connect(bot.DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in conn.execute(sql, params)]
        finally:
            conn.close()
