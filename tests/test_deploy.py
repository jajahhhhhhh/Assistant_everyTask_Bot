"""
เทสต์ของสิ่งที่ต้องถูกก่อน deploy — ไม่งั้นคอนเทนเนอร์บูตไม่ขึ้นหรือรีสตาร์ตวน

สามข้อที่พังจริงมาแล้วก่อนหน้านี้
  1. คอนเทนเนอร์ใหม่ไม่มีตาราง → init_webhook_tables โยน error ตอนบูต
  2. railway.json ทับ Procfile จน webhook ไม่ถูกรันเลย
  3. /healthz ตอบ 500 ตอนตัวชี้เพี้ยน → Railway รีสตาร์ตวนทั้งที่ยังรับข้อความได้
"""

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import line_webhook as lw


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

    async def test_storage_reports_where_the_database_lives(self):
        """ไม่มีทางเข้า shell ของคอนเทนเนอร์ ต้องถามจากในแอปว่า volume ติดไหม"""
        response = await self.client.get("/healthz/storage")
        self.assertEqual(response.status, 200)
        body = await response.json()

        self.assertEqual(body["db_path"], str(Path(self.db_path).resolve()))
        self.assertTrue(body["exists"])
        self.assertGreater(body["size_bytes"], 0)
        # ตัวชี้ขาดว่า volume mount ติดหรือยัง
        self.assertIn("on_separate_device", body)
        self.assertIn("predates_this_process", body)

    async def test_storage_counts_what_is_actually_stored(self):
        """ตัวเลขต้องขยับตามข้อมูลจริง ไม่งั้นดูไม่ออกว่าข้อมูลรอด deploy ไหม"""
        before = (await (await self.client.get("/healthz/storage")).json())["rows"]
        self.assertEqual(before["tasks"], 0)

        self.introduce_drift()   # เขียนงานหนึ่งแถวลงฐานข้อมูลจริง

        after = (await (await self.client.get("/healthz/storage")).json())["rows"]
        self.assertEqual(after["tasks"], 1)

    async def test_storage_survives_a_database_that_has_no_tables_yet(self):
        """ตารางยังไม่ถูกสร้างต้องได้ null ไม่ใช่ทั้ง endpoint พัง"""
        empty = str(Path(self._tmp.name) / "empty.db")
        sqlite3.connect(empty).close()
        snapshot = lw._storage_snapshot(empty)
        self.assertTrue(snapshot["exists"])
        self.assertIsNone(snapshot["rows"]["tasks"])

    async def test_storage_reports_a_missing_database_without_crashing(self):
        snapshot = lw._storage_snapshot(str(Path(self._tmp.name) / "never-created.db"))
        self.assertFalse(snapshot["exists"])
        self.assertEqual(snapshot["rows"], {})

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
