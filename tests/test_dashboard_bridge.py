"""
สะพานส่งข้อความขึ้น dashboard ที่ ch-howtoniksen.com

สองอย่างที่ต้องคุมให้แน่น
    1. ปลายทางล่มต้องไม่ทำให้ข้อมูลที่นี่เสีย และต้อง "บอก" ว่าล่ม — การซิงก์ที่
       ล้มทั้งรอบแล้วรายงานว่าสำเร็จ คือสิ่งที่ทำให้ข้อความ 325 ข้อความหายไป
       โดยไม่มีใครรู้มาแล้วตอน PR #37
    2. ส่งซ้ำต้องไม่เกิดงานซ้ำ แต่ผลจาก /reclassify ที่เปลี่ยนไปต้องได้ขึ้นไป
"""

import unittest
from unittest import mock

import dashboard_bridge
import line_export
from tests._bot_case import BotDbCase, bot

EXPORT = (
    "2026.07.09 วันพฤหัสบดี\n"
    "09:15 Ann Lee ยอดงาน 30,000 บาทแจ้งเบิก 15,000 บาทครับ\n"
    "09:16 Ann Lee ขอใบเสนอราคาด้วยครับ\n"
    "09:20 Bob รับทราบครับ\n"
)

ENV = {
    "DASHBOARD_API_URL": "https://example.invalid/api",
    "DASHBOARD_API_USER": "owner",
    "DASHBOARD_API_PASSWORD": "secret",
}


class TestConfigGate(unittest.TestCase):
    def test_no_url_means_the_bridge_is_off(self):
        with mock.patch.dict(dashboard_bridge.os.environ, {}, clear=True):
            self.assertFalse(dashboard_bridge.is_configured())

    def test_a_url_turns_it_on(self):
        with mock.patch.dict(dashboard_bridge.os.environ, ENV):
            self.assertTrue(dashboard_bridge.is_configured())

    def test_the_password_never_appears_outside_the_auth_header(self):
        with mock.patch.dict(dashboard_bridge.os.environ, ENV):
            header = dashboard_bridge._auth_header()
        self.assertIn("Authorization", header)
        self.assertNotIn("secret", header["Authorization"])   # base64, ไม่ใช่ข้อความเปล่า

    def test_without_a_user_no_auth_header_is_sent(self):
        with mock.patch.dict(dashboard_bridge.os.environ,
                             {"DASHBOARD_API_URL": "https://x.invalid"}, clear=True):
            self.assertEqual(dashboard_bridge._auth_header(), {})


class TestSync(BotDbCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        line_export.import_from_text(
            bot.DB_PATH, EXPORT, owner_name="Bob", file_name="งาน.txt"
        )
        self.conn = bot.connect(bot.DB_PATH)
        dashboard_bridge.ensure_schema(self.conn)

    async def asyncTearDown(self):
        self.conn.close()
        await super().asyncTearDown()

    def sync(self, push):
        with mock.patch.dict(dashboard_bridge.os.environ, ENV):
            with mock.patch.object(dashboard_bridge, "push", push):
                return dashboard_bridge.sync(self.conn)

    def test_every_message_with_a_body_goes_up_once(self):
        sent = []
        result = self.sync(lambda payload: sent.append(payload) or "remote-1")
        self.assertEqual(result["sent"], len(sent))
        self.assertGreater(result["sent"], 0)
        self.assertEqual(result["left"], 0)
        self.assertIsNone(result["error"])

    def test_a_second_run_sends_nothing_new(self):
        """ส่งซ้ำไม่ควรเกิดงานซ้ำ ต่อให้ฝั่งโน้นกันซ้ำให้อยู่แล้วก็ตาม"""
        self.sync(lambda payload: "remote-1")
        second = self.sync(lambda payload: self.fail("ไม่ควรส่งอะไรอีก"))
        self.assertEqual(second["sent"], 0)
        self.assertEqual(second["pending"], 0)

    def test_a_new_verdict_is_sent_again(self):
        """/reclassify เปลี่ยนผลของข้อความเก่า ฝั่งโน้นต้องได้ผลใหม่"""
        self.sync(lambda payload: "remote-1")
        with self.conn:
            self.conn.execute(
                "UPDATE chat_messages SET intent = 'decision', confidence = 0.91 "
                "WHERE direction = 'in'"
            )
        resent = []
        again = self.sync(lambda payload: resent.append(payload) or "remote-1")
        self.assertGreater(again["sent"], 0)
        self.assertTrue(all(p["intent"] == "decision" for p in resent))

    def test_a_failure_stops_and_says_so(self):
        def boom(payload):
            raise dashboard_bridge.DashboardUnavailable("HTTP 401")

        result = self.sync(boom)
        self.assertEqual(result["sent"], 0)
        self.assertEqual(result["error"], "HTTP 401")
        self.assertGreater(result["left"], 0)

    def test_a_failure_halfway_keeps_what_already_went_up(self):
        calls = {"n": 0}

        def flaky(payload):
            calls["n"] += 1
            if calls["n"] > 1:
                raise dashboard_bridge.DashboardUnavailable("URLError")
            return "remote-1"

        first = self.sync(flaky)
        self.assertEqual(first["sent"], 1)
        self.assertEqual(first["error"], "URLError")

        # รอบถัดไปต้องไม่ส่งอันที่ผ่านไปแล้วซ้ำ
        retried = []
        self.sync(lambda payload: retried.append(payload) or "remote-1")
        self.assertEqual(len(retried), first["left"])

    def test_a_failed_run_never_marks_anything_as_synced(self):
        self.sync(lambda payload: (_ for _ in ()).throw(
            dashboard_bridge.DashboardUnavailable("HTTP 500")
        ))
        rows = self.conn.execute("SELECT COUNT(*) AS n FROM dashboard_sync").fetchone()
        self.assertEqual(rows["n"], 0)

    def test_the_payload_carries_what_the_dashboard_needs(self):
        sent = []
        self.sync(lambda payload: sent.append(payload) or "remote-1")
        inbound = [p for p in sent if p["direction"] == "in"]
        self.assertTrue(inbound)
        for payload in inbound:
            self.assertEqual(payload["source"], "line")
            self.assertTrue(payload["external_id"])
            self.assertTrue(payload["body"])
            self.assertIn("sent_at", payload)

    def test_empty_bodies_are_not_forwarded(self):
        with self.conn:
            self.conn.execute(
                "INSERT INTO chat_messages (thread_id, direction, body, sent_at) "
                "SELECT id, 'in', '   ', '2026-07-09T10:00:00' FROM chat_threads LIMIT 1"
            )
        sent = []
        self.sync(lambda payload: sent.append(payload) or "remote-1")
        self.assertTrue(all(p["body"].strip() for p in sent))


class TestVerdictFingerprint(BotDbCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        line_export.import_from_text(
            bot.DB_PATH, EXPORT, owner_name="Bob", file_name="งาน.txt"
        )

    def test_it_changes_when_any_part_of_the_verdict_changes(self):
        conn = bot.connect(bot.DB_PATH)
        try:
            row = conn.execute(
                "SELECT intent, urgency, confidence FROM chat_messages LIMIT 1"
            ).fetchone()
            before = dashboard_bridge.verdict_of(row)
            with conn:
                conn.execute("UPDATE chat_messages SET confidence = 0.99")
            after = dashboard_bridge.verdict_of(
                conn.execute(
                    "SELECT intent, urgency, confidence FROM chat_messages LIMIT 1"
                ).fetchone()
            )
        finally:
            conn.close()
        self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
