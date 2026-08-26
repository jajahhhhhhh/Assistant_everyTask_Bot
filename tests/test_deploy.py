"""
เทสต์ของสิ่งที่ต้องถูกก่อน deploy — ไม่งั้นคอนเทนเนอร์บูตไม่ขึ้นหรือรีสตาร์ตวน

สามข้อที่พังจริงมาแล้วก่อนหน้านี้
  1. คอนเทนเนอร์ใหม่ไม่มีตาราง → init_webhook_tables โยน error ตอนบูต
  2. railway.json ทับ Procfile จน webhook ไม่ถูกรันเลย
  3. /healthz ตอบ 500 ตอนตัวชี้เพี้ยน → Railway รีสตาร์ตวนทั้งที่ยังรับข้อความได้
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import line_webhook as lw
import app


class TestSchemaBootstrap(unittest.TestCase):
    """ฐานข้อมูลว่างต้องลง schema ให้เองตอนบูต"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmp.name) / "fresh.db")

    def tearDown(self):
        self._tmp.cleanup()

    def tables(self):
        conn = sqlite3.connect(self.db_path)
        try:
            return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()

    def test_fresh_database_gets_the_full_schema(self):
        lw.init_webhook_tables(self.db_path)
        self.assertEqual(set(lw.REQUIRED_TABLES) - self.tables(), set())
        self.assertIn("line_webhook_deliveries", self.tables())

    def test_views_are_installed_too(self):
        lw.init_webhook_tables(self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            views = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
        finally:
            conn.close()
        for view in ("v_blocked_now", "v_unanswered_now", "v_wait_spans"):
            self.assertIn(view, views)

    def test_second_boot_keeps_the_data(self):
        lw.init_webhook_tables(self.db_path)
        conn = lw.connect(self.db_path)
        with conn:
            conn.execute(
                "INSERT INTO tasks (title, status, created_at) VALUES ('งานเก่า', 'inbox', '2026-08-01T00:00:00Z')"
            )
        conn.close()

        lw.init_webhook_tables(self.db_path)  # deploy รอบถัดไป

        conn = lw.connect(self.db_path)
        try:
            self.assertEqual(len(conn.execute("SELECT id FROM tasks").fetchall()), 1)
        finally:
            conn.close()

    def test_missing_sql_directory_still_reports_clearly(self):
        original = lw.SQL_DIR
        lw.SQL_DIR = Path("/nonexistent/sql")
        try:
            with self.assertRaises(RuntimeError) as caught:
                lw.init_webhook_tables(self.db_path)
        finally:
            lw.SQL_DIR = original
        self.assertIn("01_schema.sql", str(caught.exception))


class TestProcessConfig(unittest.TestCase):
    """railway.json กับ Procfile ต้องชี้ไปที่ entry point เดียวกันและมีอยู่จริง"""

    def setUp(self):
        self.railway = json.loads((REPO_ROOT / "railway.json").read_text(encoding="utf-8"))
        self.procfile = dict(
            line.split(":", 1)
            for line in (REPO_ROOT / "Procfile").read_text(encoding="utf-8").strip().splitlines()
        )

    def test_start_command_runs_a_file_that_exists(self):
        command = self.railway["deploy"]["startCommand"]
        script = command.split()[-1]
        self.assertTrue((REPO_ROOT / script).is_file(), f"{script} ไม่มีอยู่จริง")

    def test_procfile_and_railway_agree(self):
        # railway.json ทับ Procfile เสมอ ถ้าสองอันไม่ตรงกัน จะ deploy ไปคนละอย่าง
        self.assertEqual(
            self.railway["deploy"]["startCommand"].strip(),
            self.procfile["web"].strip(),
        )

    def test_health_check_path_is_served(self):
        self.assertEqual(self.railway["deploy"]["healthcheckPath"], "/healthz")

    def test_entry_point_starts_both_halves(self):
        source = (REPO_ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("start_web", source)
        self.assertIn("start_telegram", source)


class TestBootStorageReport(unittest.TestCase):
    """ตอนบูตต้องบอกให้ชัดว่าข้อมูลจะไปอยู่ไหน

    เคสจริงที่เกิดมาแล้ว: DATA_DIR=/data ถูกตั้งไว้ แต่มี DATABASE_PATH=data/assistant.db
    ค้างอยู่ ไฟล์จริงจึงเป็น /app/data/assistant.db ที่หายทุก deploy ส่วน volume
    นอนว่าง ไม่มีอะไรผิดพลาดให้เห็นเลยสักอย่าง
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = (lw.DB_PATH, os.environ.get("DATABASE_PATH"), os.environ.get("DATA_DIR"))
        lw.DB_PATH = str(Path(self._tmp.name) / "assistant.db")

    def tearDown(self):
        lw.DB_PATH = self._saved[0]
        for key, value in (("DATABASE_PATH", self._saved[1]), ("DATA_DIR", self._saved[2])):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    def report(self, db_path=None):
        import bot

        original = bot.DB_PATH
        bot.DB_PATH = db_path or lw.DB_PATH
        try:
            with self.assertLogs("app", level="INFO") as captured:
                app.describe_storage()
            return "\n".join(captured.output)
        finally:
            bot.DB_PATH = original

    def test_always_logs_the_absolute_path(self):
        """path ที่ log ต้องเป็น absolute — 'data/assistant.db' ไม่บอกอะไรเลย"""
        self.assertIn(str(Path(self._tmp.name) / "assistant.db"), self.report())

    def test_warns_when_database_path_is_relative(self):
        os.environ["DATABASE_PATH"] = "data/assistant.db"
        os.environ["DATA_DIR"] = "/data"
        output = self.report()
        self.assertIn("WARNING", output)
        self.assertIn("สัมพัทธ์", output)
        self.assertIn("/data", output)      # ต้องบอกด้วยว่าอะไรถูกทับ

    def test_quiet_when_database_path_is_absolute(self):
        os.environ["DATABASE_PATH"] = "/data/assistant.db"
        self.assertNotIn("สัมพัทธ์", self.report())

    def test_errors_when_the_two_sides_disagree(self):
        """สองฝั่งเขียนคนละไฟล์ = ข้อมูลแตกสองชุดโดยไม่มีใครรู้"""
        output = self.report(db_path=str(Path(self._tmp.name) / "อีกไฟล์.db"))
        self.assertIn("ERROR", output)
        self.assertIn("คนละไฟล์", output)

    def test_warns_when_the_data_directory_is_not_a_volume(self):
        """โฟลเดอร์ชั่วคราวอยู่บน device เดียวกับ / — ต้องเตือนว่าข้อมูลจะหาย"""
        output = self.report()
        self.assertIn("จะหายเมื่อ deploy", output)

    def test_still_answers_when_the_directory_does_not_exist_yet(self):
        """โฟลเดอร์ข้อมูลถูกสร้างใน start_web() ซึ่งรันหลังจากนี้

        ตอนบูตครั้งแรกโฟลเดอร์จึงยังไม่มี ถ้าไม่ไต่ขึ้นไปหาโฟลเดอร์แม่ จะได้
        "ตรวจไม่ได้" แทนคำเตือนที่ต้องการ ซึ่งคือกรณีที่เจอจริง
        """
        lw.DB_PATH = str(Path(self._tmp.name) / "ยังไม่มี" / "assistant.db")
        output = self.report()
        self.assertNotIn("ตรวจไม่ได้", output)
        self.assertIn("จะหายเมื่อ deploy", output)


class TestHealthEndpoints(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmp.name) / "health.db")
        lw.init_webhook_tables(self.db_path)

        self.handler = lw.LineWebhookHandler(db_path=self.db_path, channel_secret="s", client=None)
        self.client = TestClient(TestServer(lw.create_app(self.handler)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self._tmp.cleanup()

    def introduce_drift(self):
        """งานที่ status='blocked' แต่ไม่มี task_blocks ที่เปิดอยู่ (ผิดกฎ E3)"""
        conn = lw.connect(self.db_path)
        with conn:
            conn.execute(
                "INSERT INTO tasks (title, status, created_at, blocked_since, blocked_reason) "
                "VALUES ('งานที่ตัวชี้เพี้ยน', 'blocked', '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z', 'รอคนตอบ')"
            )
        conn.close()

    async def test_healthz_is_green_on_a_clean_database(self):
        response = await self.client.get("/healthz")
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["block_pointer_drift"], 0)

    async def test_healthz_stays_green_when_data_drifts(self):
        self.introduce_drift()
        response = await self.client.get("/healthz")
        self.assertEqual(response.status, 200, "ตอบ 500 ที่นี่ = Railway รีสตาร์ตวน")
        self.assertEqual((await response.json())["block_pointer_drift"], 1)

    async def test_invariant_endpoint_reports_the_drift(self):
        self.introduce_drift()
        response = await self.client.get("/healthz/invariants")
        self.assertEqual(response.status, 500)
        payload = await response.json()
        self.assertEqual(payload["status"], "drift")
        self.assertEqual(payload["block_pointer_drift"][0]["status"], "blocked")

    async def test_invariant_endpoint_is_green_when_consistent(self):
        response = await self.client.get("/healthz/invariants")
        self.assertEqual(response.status, 200)

    async def test_healthz_survives_concurrent_probes(self):
        """คำขอซ้อนกันต้องไม่ทำให้ 500

        sqlite3 ผูก connection กับเธรดที่สร้างมัน เดิม _health แยก connect /
        query / close เป็น asyncio.to_thread คนละครั้ง พอ thread pool แตกเป็น
        หลายเธรดตอนถูกยิงพร้อมกัน จะได้ ProgrammingError → 500 → Railway
        รีสตาร์ตวน ซึ่งเป็นสิ่งที่ docstring ของ _health ตั้งใจกันไว้แต่แรก
        """
        import asyncio

        responses = await asyncio.gather(
            *[self.client.get("/healthz") for _ in range(12)]
        )
        # อ่าน body ให้ครบก่อน — ปล่อย connection คืน pool ไม่ให้ค้าง
        payloads = [await response.json() for response in responses]
        self.assertEqual([r.status for r in responses], [200] * 12)
        self.assertEqual([p["status"] for p in payloads], ["ok"] * 12)

    async def test_invariants_survives_concurrent_probes(self):
        """_invariants เคยมีบั๊กข้ามเธรดแบบเดียวกัน"""
        import asyncio

        responses = await asyncio.gather(
            *[self.client.get("/healthz/invariants") for _ in range(12)]
        )
        payloads = [await response.json() for response in responses]
        self.assertEqual([r.status for r in responses], [200] * 12)
        self.assertEqual([p["status"] for p in payloads], ["ok"] * 12)


if __name__ == "__main__":
    unittest.main()
