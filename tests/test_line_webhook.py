"""
เทสต์ของ line_webhook.py — เน้นเรื่อง "จังหวะ" ของการเขียน

รันด้วย pytest หรือ python -m unittest ก็ได้ (ใช้ unittest ล้วน ไม่ต้องมี plugin)
ทุกเทสต์รัน sql/01_schema.sql จริงบนไฟล์ชั่วคราว แล้วบางเทสต์รัน 02_views.sql
ต่อ เพื่อยืนยันว่า view ที่รายงานจริงใช้ อ่านสิ่งที่ handler เขียนได้
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import line_webhook as lw

REPO_ROOT = Path(__file__).resolve().parent.parent
SECRET = "test-channel-secret"
OWNER = "Uowner"
FARID = "Ufarid"


def sign(body: bytes) -> str:
    return base64.b64encode(
        hmac.new(SECRET.encode(), body, hashlib.sha256).digest()
    ).decode()


def ts(iso: str) -> int:
    """ISO UTC -> LINE timestamp (ms)"""
    import datetime as dt

    return int(
        dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
        .replace(tzinfo=dt.timezone.utc)
        .timestamp()
        * 1000
    )


def message_event(
    *,
    event_id: str,
    user_id: str,
    text: str = None,
    at: str = "2026-08-20T10:00:00Z",
    chat_id: str = None,
    message_type: str = "text",
    reply_token: str = "rt-1",
):
    message = {"id": f"m-{event_id}", "type": message_type}
    if text is not None:
        message["text"] = text
    return {
        "type": "message",
        "webhookEventId": event_id,
        "timestamp": ts(at),
        "replyToken": reply_token,
        "source": {"type": "user", "userId": user_id} if chat_id is None else
                  {"type": "group", "groupId": chat_id, "userId": user_id},
        "message": message,
    }


class FakeLineClient:
    """เก็บสิ่งที่ถูกส่งออกไว้ตรวจ แทนที่จะยิงเน็ตจริง"""

    def __init__(self, fail_reply=False, fail_push=False):
        self.replies = []
        self.pushes = []
        self.fail_reply = fail_reply
        self.fail_push = fail_push

    async def reply(self, reply_token, text):
        if self.fail_reply:
            raise lw.LineApiError(400, "Invalid reply token")
        self.replies.append((reply_token, text))

    async def push(self, to, text):
        if self.fail_push:
            raise lw.LineApiError(500, "boom")
        self.pushes.append((to, text))

    async def close(self):
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# ฟังก์ชันล้วน
# ═══════════════════════════════════════════════════════════════════════════════

class TestSignature(unittest.TestCase):
    def test_valid(self):
        body = b'{"events":[]}'
        self.assertTrue(lw.verify_signature(SECRET, body, sign(body)))

    def test_tampered_body(self):
        body = b'{"events":[]}'
        self.assertFalse(lw.verify_signature(SECRET, b'{"events":[1]}', sign(body)))

    def test_missing_pieces(self):
        self.assertFalse(lw.verify_signature("", b"x", "sig"))
        self.assertFalse(lw.verify_signature(SECRET, b"x", ""))


class TestClassifier(unittest.TestCase):
    def test_request_beats_question(self):
        result = lw.classify_message("ส่งไฟล์สัญญาให้หน่อยได้ไหม")
        self.assertEqual(result["intent"], "request")
        self.assertGreaterEqual(result["confidence"], 0.8)

    def test_question(self):
        self.assertEqual(lw.classify_message("พรุ่งนี้เข้าไซต์กี่โมง")["intent"], "question")

    def test_smalltalk_stays_below_view_threshold(self):
        # 0.55 < cfg.min_classifier_confidence (0.80) → view ไม่นับ ถูกต้องแล้ว
        result = lw.classify_message("ขอบคุณครับ")
        self.assertEqual(result["intent"], "smalltalk")
        self.assertLess(result["confidence"], 0.8)

    def test_urgency(self):
        self.assertEqual(lw.classify_message("ขอด่วนเลยครับ")["urgency"], "high")
        self.assertEqual(lw.classify_message("ขอไฟล์ ไม่รีบครับ")["urgency"], "low")

    def test_empty(self):
        self.assertIsNone(lw.classify_message(None)["intent"])


class TestReleaseRules(unittest.TestCase):
    def test_plain_text_only_releases_person(self):
        self.assertEqual(lw.reasons_released_by({"type": "text"}, "ครับผม"), [lw.REASON_PERSON])

    def test_file_releases_document_wait(self):
        released = lw.reasons_released_by({"type": "file"}, None)
        self.assertIn(lw.REASON_DOC, released)

    def test_money_needs_evidence(self):
        self.assertNotIn(lw.REASON_MONEY, lw.reasons_released_by({"type": "text"}, "เดี๋ยวโอนนะ"))
        self.assertIn(lw.REASON_MONEY, lw.reasons_released_by({"type": "text"}, "โอนแล้วนะครับ"))


class TestOwnerCommandParser(unittest.TestCase):
    def test_create_task(self):
        self.assertEqual(
            lw.parse_owner_command("งาน: ตามใบเสนอราคา"),
            {"command": "create_task", "title": "ตามใบเสนอราคา"},
        )

    def test_block(self):
        self.assertEqual(
            lw.parse_owner_command("ติด #12 รอเอกสาร @Farid"),
            {"command": "block", "task_id": 12, "reason": "รอเอกสาร", "person": "Farid"},
        )

    def test_block_defaults_to_person_reason(self):
        self.assertEqual(lw.parse_owner_command("ติด #7")["reason"], lw.REASON_PERSON)

    def test_unblock(self):
        self.assertEqual(lw.parse_owner_command("เคลียร์ #7"), {"command": "unblock", "task_id": 7})

    def test_plain_chat_is_not_a_command(self):
        self.assertIsNone(lw.parse_owner_command("ไปกินข้าวกัน"))


# ═══════════════════════════════════════════════════════════════════════════════
# ปลายทางถึงปลายทาง
# ═══════════════════════════════════════════════════════════════════════════════

class WebhookCase(unittest.IsolatedAsyncioTestCase):
    with_views = False

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmp.name, "test.db")

        conn = sqlite3.connect(self.db_path)
        conn.executescript((REPO_ROOT / "sql" / "01_schema.sql").read_text())
        if self.with_views:
            conn.executescript((REPO_ROOT / "sql" / "02_views.sql").read_text())
        conn.executescript(
            """
            INSERT INTO projects(id,name,type) VALUES(1,'Chowrest','chowrest');
            INSERT INTO contacts(id,display_name,line_user_id) VALUES(1,'Farid','Ufarid');
            INSERT INTO tasks(id,title,project_id,status,created_at)
              VALUES(1,'ตามสัญญาผู้รับเหมา',1,'doing','2026-08-01T00:00:00Z');
            """
        )
        conn.commit()
        conn.close()

        lw.init_webhook_tables(self.db_path)

        self.line = FakeLineClient()
        self.handler = lw.LineWebhookHandler(
            db_path=self.db_path,
            channel_secret=SECRET,
            client=self.line,
            owner_user_id=OWNER,
        )
        self.client = TestClient(TestServer(lw.create_app(self.handler)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self._tmp.cleanup()

    async def post(self, events, signature=None):
        body = json.dumps({"destination": "Ubot", "events": events}).encode()
        response = await self.client.post(
            lw.WEBHOOK_PATH,
            data=body,
            headers={
                "X-Line-Signature": signature or sign(body),
                "Content-Type": "application/json",
            },
        )
        payload = await response.json()
        await self.handler.drain()  # รอให้จังหวะ 4-7 ทำงานจบก่อนตรวจ
        return response, payload

    def query(self, sql, params=()):
        conn = lw.connect(self.db_path)
        try:
            return [dict(row) for row in conn.execute(sql, params)]
        finally:
            conn.close()

    def assert_no_pointer_drift(self):
        conn = lw.connect(self.db_path)
        try:
            self.assertEqual(lw.check_block_invariant(conn), [], "E3: ตัวชี้บน tasks เพี้ยน")
        finally:
            conn.close()

    def open_wait(self, *, contact_id=1, reason=lw.REASON_PERSON, at="2026-08-19T09:00:00Z"):
        conn = lw.connect(self.db_path)
        try:
            lw.open_block(conn, task_id=1, reason=reason, contact_id=contact_id, at=at)
        finally:
            conn.close()


class TestIntake(WebhookCase):
    async def test_bad_signature_writes_nothing(self):
        response, _ = await self.post(
            [message_event(event_id="e1", user_id=FARID, text="สวัสดี")], signature="nope"
        )
        self.assertEqual(response.status, 401)
        self.assertEqual(self.query("SELECT id FROM chat_messages"), [])

    async def test_unconfigured_secret_is_503(self):
        self.handler.channel_secret = ""
        response, _ = await self.post([message_event(event_id="e1", user_id=FARID, text="hi")])
        self.assertEqual(response.status, 503)

    async def test_message_is_stored_with_thread_and_contact(self):
        response, payload = await self.post(
            [message_event(event_id="e1", user_id=FARID, text="ส่งไฟล์สัญญาให้หน่อยได้ไหม")]
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["accepted"], 1)

        rows = self.query("SELECT * FROM chat_messages")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["direction"], "in")
        self.assertEqual(row["sent_at"], "2026-08-20T10:00:00Z")  # เวลาของ LINE ไม่ใช่เวลาเรา
        self.assertEqual(row["contact_id"], 1)                     # ผูกกับ Farid แถวเดิม
        self.assertEqual(row["intent"], "request")                 # จังหวะ 4 เติมให้ทีหลัง
        self.assertIsNotNone(row["confidence"])                    # ห้ามมี intent โดยไม่มี confidence
        self.assertIsNotNone(row["raw_json"])

        thread = self.query("SELECT * FROM chat_threads")[0]
        self.assertEqual(thread["platform"], "line")
        self.assertEqual(thread["external_chat_id"], FARID)
        self.assertEqual(thread["last_msg_at"], "2026-08-20T10:00:00Z")

    async def test_retry_with_same_event_id_is_a_noop(self):
        event = message_event(event_id="dup", user_id=FARID, text="ทวงงานหน่อย")
        await self.post([event])
        _, payload = await self.post([event])

        self.assertEqual(payload, {"accepted": 0, "duplicates": 1})
        self.assertEqual(len(self.query("SELECT id FROM chat_messages")), 1)

    async def test_group_message_creates_group_thread(self):
        await self.post(
            [message_event(event_id="g1", user_id=FARID, text="โอเคครับ", chat_id="Cgroup")]
        )
        thread = self.query("SELECT * FROM chat_threads")[0]
        self.assertEqual(thread["external_chat_id"], "Cgroup")
        self.assertEqual(thread["is_group"], 1)

    async def test_non_text_message_keeps_a_row(self):
        await self.post([message_event(event_id="s1", user_id=FARID, message_type="sticker")])
        row = self.query("SELECT * FROM chat_messages")[0]
        self.assertEqual(row["body"], "(sticker)")
        self.assertIsNone(row["intent"])  # ไม่มีข้อความ ก็ไม่เดา

    async def test_unsend_scrubs_body_but_keeps_the_row(self):
        await self.post([message_event(event_id="u1", user_id=FARID, text="พิมพ์ผิด")])
        await self.post(
            [
                {
                    "type": "unsend",
                    "webhookEventId": "u2",
                    "timestamp": ts("2026-08-20T10:05:00Z"),
                    "source": {"type": "user", "userId": FARID},
                    "unsend": {"messageId": "m-u1"},
                }
            ]
        )
        row = self.query("SELECT * FROM chat_messages")[0]
        self.assertEqual(row["body"], "(ยกเลิกข้อความ)")


class TestWaitLifecycle(WebhookCase):
    async def test_reply_from_the_person_closes_the_wait_at_their_send_time(self):
        self.open_wait(at="2026-08-19T09:00:00Z")

        await self.post(
            [
                message_event(
                    event_id="r1", user_id=FARID, text="ได้ครับ เดี๋ยวจัดให้",
                    at="2026-08-20T10:00:00Z",
                )
            ]
        )

        block = self.query("SELECT * FROM task_blocks")[0]
        # ปิดด้วยเวลาที่เขาตอบ ไม่ใช่เวลาที่คิวหลังบ้านทำงานเสร็จ
        self.assertEqual(block["unblocked_at"], "2026-08-20T10:00:00Z")

        task = self.query("SELECT * FROM tasks WHERE id=1")[0]
        self.assertEqual(task["status"], "doing")
        self.assertIsNone(task["blocked_since"])
        self.assertIsNone(task["blocked_reason"])
        self.assertIsNone(task["blocked_on_contact_id"])
        self.assert_no_pointer_drift()

        events = self.query("SELECT * FROM task_events ORDER BY id")
        self.assertEqual(events[-1]["to_status"], "doing")
        self.assertEqual(events[-1]["at"], "2026-08-20T10:00:00Z")

        self.assertTrue(self.line.replies)  # แจ้งเจ้าของว่าปลดแล้ว

    async def test_document_wait_survives_a_bare_reply(self):
        self.open_wait(reason=lw.REASON_DOC)

        await self.post([message_event(event_id="d1", user_id=FARID, text="รับทราบครับ")])

        block = self.query("SELECT * FROM task_blocks")[0]
        self.assertIsNone(block["unblocked_at"], "คำพูดลอย ๆ ไม่ใช่เอกสาร")
        self.assertEqual(self.query("SELECT status FROM tasks WHERE id=1")[0]["status"], "blocked")
        self.assert_no_pointer_drift()

    async def test_document_wait_closes_on_a_file(self):
        self.open_wait(reason=lw.REASON_DOC)

        await self.post(
            [message_event(event_id="d2", user_id=FARID, message_type="file",
                           at="2026-08-20T11:00:00Z")]
        )

        block = self.query("SELECT * FROM task_blocks")[0]
        self.assertEqual(block["unblocked_at"], "2026-08-20T11:00:00Z")
        self.assert_no_pointer_drift()

    async def test_someone_else_talking_does_not_close_your_wait(self):
        self.open_wait(contact_id=1)

        await self.post(
            [message_event(event_id="o1", user_id="Ustranger", text="สวัสดีครับ")]
        )

        self.assertIsNone(self.query("SELECT * FROM task_blocks")[0]["unblocked_at"])
        self.assert_no_pointer_drift()

    async def test_second_reply_does_not_reopen_or_double_close(self):
        self.open_wait()
        await self.post([message_event(event_id="x1", user_id=FARID, text="ครับ")])
        await self.post(
            [message_event(event_id="x2", user_id=FARID, text="ครับผม", at="2026-08-20T12:00:00Z")]
        )

        blocks = self.query("SELECT * FROM task_blocks")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["unblocked_at"], "2026-08-20T10:00:00Z")
        self.assert_no_pointer_drift()


class TestConcurrentEvents(WebhookCase):
    """หลาย event ในชุดเดียวต้องประมวลผลครบทุกตัว

    _process เคยเปิด connection เดียวแล้วส่งข้าม asyncio.to_thread หลายครั้ง
    (apply_classification, _release, _run_owner_command, _run_reno,
    finish_delivery) พอ event เข้ามาพร้อมกัน thread pool แตกเป็นหลายเธรด
    sqlite จึงโยน ProgrammingError กลางทาง ผลคือข้อความถูกบันทึกใน
    chat_messages แต่จังหวะ 4-7 ตายเงียบ ๆ — ไม่ปลดการรอ ไม่ทำคำสั่ง
    ไม่ตอบกลับ และแถว delivery ค้างที่ received
    """

    async def test_every_event_in_a_batch_is_processed(self):
        events = [
            message_event(event_id=f"conc{i}", user_id=FARID, text=f"ข้อความที่ {i}")
            for i in range(8)
        ]
        await self.post(events)

        self.assertEqual(len(self.query("SELECT id FROM chat_messages")), 8)

        statuses = self.query(
            "SELECT status, COUNT(*) AS n FROM line_webhook_deliveries GROUP BY status"
        )
        self.assertEqual(
            {row["status"]: row["n"] for row in statuses},
            {"processed": 8},
            "จังหวะ 4-7 ต้องจบครบทุก event ไม่ค้างที่ received และไม่ failed",
        )

    async def test_waits_still_close_when_events_arrive_together(self):
        """การปลดการรอต้องไม่หายไปเพราะ event เข้ามาพร้อมกัน"""
        self.open_wait()
        events = [
            message_event(event_id=f"batch{i}", user_id=FARID, text=f"ครับ {i}")
            for i in range(6)
        ]
        await self.post(events)

        blocks = self.query("SELECT * FROM task_blocks")
        self.assertEqual(len(blocks), 1)
        self.assertIsNotNone(blocks[0]["unblocked_at"], "การรอต้องถูกปลด")
        self.assert_no_pointer_drift()

    async def test_owner_replies_survive_arriving_together(self):
        """record_owner_reply ก็ถือ connection ข้ามเธรดแบบเดียวกัน

        เปิดที่เธรดหนึ่ง เขียนที่อีกเธรด ปิดที่เธรดที่สาม พอเรียกพร้อมกันจาก
        แดชบอร์ด thread pool แตกเป็นหลายเธรด การตอบของเจ้าของจึงหายเงียบ ๆ
        """
        await self.post([message_event(event_id="q9", user_id=FARID, text="ราคาเท่าไหร่")])
        thread_id = self.query("SELECT id FROM chat_threads")[0]["id"]

        await asyncio.gather(
            *(
                self.handler.record_owner_reply(thread_id=thread_id, body=f"ตอบที่ {i}")
                for i in range(8)
            )
        )

        outbound = self.query("SELECT id FROM chat_messages WHERE direction='out'")
        self.assertEqual(len(outbound), 8, "การตอบของเจ้าของต้องถูกบันทึกครบทุกครั้ง")


class TestOwnerCommands(WebhookCase):
    async def test_create_task_links_back_to_the_message(self):
        await self.post([message_event(event_id="c1", user_id=OWNER, text="งาน: ตามใบเสนอราคา")])

        task = self.query("SELECT * FROM tasks WHERE id > 1")[0]
        message = self.query("SELECT * FROM chat_messages")[0]
        self.assertEqual(task["title"], "ตามใบเสนอราคา")
        self.assertEqual(task["source"], "line")
        self.assertEqual(task["source_ref"], str(message["id"]))
        self.assertEqual(message["linked_task_id"], task["id"])
        self.assertEqual(
            self.query("SELECT * FROM task_events WHERE task_id=?", (task["id"],))[0]["to_status"],
            "inbox",
        )

    async def test_block_command_opens_one_wait_and_mirrors_the_pointer(self):
        await self.post(
            [message_event(event_id="b1", user_id=OWNER, text="ติด #1 รอเอกสาร @Farid",
                           at="2026-08-20T09:00:00Z")]
        )

        block = self.query("SELECT * FROM task_blocks")[0]
        self.assertEqual(block["reason"], "รอเอกสาร")
        self.assertEqual(block["contact_id"], 1)
        self.assertEqual(block["blocked_at"], "2026-08-20T09:00:00Z")

        task = self.query("SELECT * FROM tasks WHERE id=1")[0]
        self.assertEqual(task["status"], "blocked")
        self.assertEqual(task["blocked_reason"], "รอเอกสาร")
        self.assertEqual(task["blocked_on_contact_id"], 1)
        self.assert_no_pointer_drift()

    async def test_blocking_twice_closes_the_previous_wait(self):
        await self.post(
            [message_event(event_id="b1", user_id=OWNER, text="ติด #1 รอเอกสาร @Farid",
                           at="2026-08-20T09:00:00Z")]
        )
        await self.post(
            [message_event(event_id="b2", user_id=OWNER, text="ติด #1 รอเงิน @Farid",
                           at="2026-08-20T15:00:00Z")]
        )

        blocks = self.query("SELECT * FROM task_blocks ORDER BY id")
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["unblocked_at"], "2026-08-20T15:00:00Z")
        self.assertIsNone(blocks[1]["unblocked_at"])
        self.assert_no_pointer_drift()

    async def test_unblock_command(self):
        self.open_wait()
        await self.post(
            [message_event(event_id="ub", user_id=OWNER, text="เคลียร์ #1",
                           at="2026-08-20T16:00:00Z")]
        )
        self.assertEqual(
            self.query("SELECT * FROM task_blocks")[0]["unblocked_at"], "2026-08-20T16:00:00Z"
        )
        self.assert_no_pointer_drift()

    async def test_commands_from_strangers_are_ignored(self):
        await self.post([message_event(event_id="ns", user_id=FARID, text="งาน: แอบสร้างงาน")])
        self.assertEqual(len(self.query("SELECT id FROM tasks")), 1)


class TestReplyTiming(WebhookCase):
    async def test_reply_token_is_used_first(self):
        self.open_wait()
        await self.post([message_event(event_id="t1", user_id=FARID, text="ครับ")])
        self.assertEqual(len(self.line.replies), 1)
        self.assertEqual(self.line.pushes, [])

    async def test_dead_token_falls_back_to_push(self):
        self.open_wait()
        self.handler.client = FakeLineClient(fail_reply=True)
        await self.post([message_event(event_id="t2", user_id=FARID, text="ครับ")])
        self.assertEqual(len(self.handler.client.pushes), 1)

    async def test_expired_budget_skips_the_reply_token(self):
        self.open_wait()
        original = lw.REPLY_TOKEN_TTL_SECONDS
        lw.REPLY_TOKEN_TTL_SECONDS = -1.0  # โทเคนตายไปแล้ว
        try:
            await self.post([message_event(event_id="t3", user_id=FARID, text="ครับ")])
        finally:
            lw.REPLY_TOKEN_TTL_SECONDS = original
        self.assertEqual(self.line.replies, [])
        self.assertEqual(len(self.line.pushes), 1)

    async def test_bot_ack_is_not_recorded_as_your_reply(self):
        self.open_wait()
        await self.post([message_event(event_id="a1", user_id=FARID, text="ขอไฟล์หน่อยครับ")])
        # ตอบรับอัตโนมัติไปแล้ว แต่ต้องไม่มีแถว out ที่จะไปปิดนาฬิกาใน v_reply_latency
        self.assertTrue(self.line.replies)
        self.assertEqual(self.query("SELECT id FROM chat_messages WHERE direction='out'"), [])

    async def test_failed_send_writes_no_outbound_row(self):
        self.handler.client = FakeLineClient(fail_reply=True, fail_push=True)
        self.handler.log_bot_acks = True
        self.open_wait()
        await self.post([message_event(event_id="f1", user_id=FARID, text="ครับ")])
        self.assertEqual(self.query("SELECT id FROM chat_messages WHERE direction='out'"), [])

    async def test_owner_reply_fills_responded_at(self):
        await self.post([message_event(event_id="q1", user_id=FARID, text="ราคาสุดท้ายเท่าไหร่")])
        thread_id = self.query("SELECT id FROM chat_threads")[0]["id"]

        await self.handler.record_owner_reply(thread_id=thread_id, body="ห้าหมื่นครับ")

        inbound = self.query("SELECT * FROM chat_messages WHERE direction='in'")[0]
        self.assertIsNotNone(inbound["responded_at"])
        outbound = self.query("SELECT * FROM chat_messages WHERE direction='out'")[0]
        self.assertIsNone(outbound["contact_id"])  # out = ตัวเราเอง ตามกติกาของ schema


class TestViewsSeeWhatWeWrite(WebhookCase):
    """เขียนผ่าน webhook แล้วอ่านด้วย view จริงจาก 02_views.sql"""

    with_views = True

    async def test_unanswered_and_blocked_views(self):
        self.open_wait(at="2026-08-19T09:00:00Z")
        await self.post(
            [message_event(event_id="v1", user_id=FARID, text="ส่งใบเสนอราคาให้หน่อยได้ไหม")]
        )

        unanswered = self.query("SELECT * FROM v_unanswered_now")
        self.assertEqual(len(unanswered), 1)
        self.assertEqual(unanswered[0]["person"], "Farid")
        self.assertEqual(unanswered[0]["platform"], "line")

        # ข้อความนั้นปิดการรอไปด้วย (รอคนตอบ) จึงไม่ควรเหลือใน v_blocked_now
        self.assertEqual(self.query("SELECT * FROM v_blocked_now"), [])

    async def test_wait_hours_land_in_the_bottleneck_view(self):
        self.open_wait(at="2026-08-20T00:00:00Z")
        await self.post(
            [message_event(event_id="v2", user_id=FARID, text="ครับผม", at="2026-08-20T06:00:00Z")]
        )

        spans = self.query("SELECT * FROM v_wait_spans")
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0]["person"], "Farid")
        self.assertEqual(spans[0]["still_waiting"], 0)
        self.assertAlmostEqual(spans[0]["wait_hours"], 6.0, places=1)


class TestRenoBridgeWiring(WebhookCase):
    """LINE → chat_messages → คิวของ reno_bridge → ยืนยัน — ผ่าน webhook จริง"""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        import reno_bridge

        self.rb = reno_bridge
        config = reno_bridge.load_config("/nonexistent/CONFIG.md")
        self.handler.reno = reno_bridge.RenoBridge(config)
        conn = lw.connect(self.db_path)
        try:
            self.handler.reno.ensure_schema(conn)
        finally:
            conn.close()

    def inbox(self, status="pending"):
        conn = lw.connect(self.db_path)
        try:
            return self.rb.get_inbox(conn, status)
        finally:
            conn.close()

    async def test_message_from_the_contractor_lands_in_the_queue(self):
        await self.post(
            [message_event(event_id="rb1", user_id=FARID,
                           text="ขอเบิกค่าแรงงานระบบไฟ 15,000 บาท ตึกเฉวง")]
        )

        items = self.inbox()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["kind"], "payment")
        self.assertEqual(items[0]["site"], "Chaweng")
        self.assertEqual(items[0]["payload"]["amount"], 15000)
        self.assertEqual(items[0]["payload"]["who"], "Farid")  # ชื่อผู้ส่งจริง ไม่ใช่ค่าเริ่มต้น
        self.assertIn("จับได้", self.line.replies[0][1])

    async def test_nothing_is_written_to_the_dashboard_before_the_owner_confirms(self):
        await self.post(
            [message_event(event_id="rb2", user_id=FARID, text="ขอเบิกค่าแรง 8,000 บาท")]
        )
        self.assertEqual(self.inbox("approved"), [])

        item_id = self.inbox()[0]["id"]
        await self.post(
            [message_event(event_id="rb3", user_id=OWNER, text=f"reno site #{item_id} เฉวง",
                           at="2026-08-20T10:05:00Z")]
        )
        await self.post(
            [message_event(event_id="rb4", user_id=OWNER, text="reno ok",
                           at="2026-08-20T10:06:00Z")]
        )
        self.assertEqual(len(self.inbox("approved")), 1)

    async def test_reno_failure_does_not_break_the_webhook(self):
        class Exploding:
            def handle_command(self, *a, **k):
                raise RuntimeError("boom")

            def on_message(self, *a, **k):
                raise RuntimeError("boom")

        self.handler.reno = Exploding()
        response, _ = await self.post(
            [message_event(event_id="rb5", user_id=FARID, text="ขอเบิกค่าแรง 8,000 บาท")]
        )
        self.assertEqual(response.status, 200)
        # ข้อความยังถูกบันทึกครบ และ event ไม่ถูกทำเครื่องหมายว่าล้มเหลว
        self.assertEqual(len(self.query("SELECT id FROM chat_messages")), 1)
        self.assertEqual(
            self.query("SELECT status FROM line_webhook_deliveries")[0]["status"], "processed"
        )

    async def test_owner_task_command_is_not_queued_twice(self):
        await self.post(
            [message_event(event_id="rb7", user_id=OWNER, text="งาน: ติดตั้งสุขภัณฑ์ลิปะ")]
        )
        # จังหวะ 6 สร้าง task ให้แล้ว bridge ต้องไม่เสนอการ์ดใบเดียวกันซ้ำ
        self.assertEqual(len(self.query("SELECT id FROM tasks WHERE id > 1")), 1)
        self.assertEqual(self.inbox(), [])

    async def test_the_wait_is_still_released_when_reno_also_runs(self):
        self.open_wait()
        await self.post(
            [message_event(event_id="rb6", user_id=FARID,
                           text="รื้อฝ้าชั้น 2 เฉวงเสร็จแล้วครับ")]
        )
        self.assertIsNotNone(self.query("SELECT * FROM task_blocks")[0]["unblocked_at"])
        self.assertTrue(self.inbox())
        self.assert_no_pointer_drift()


if __name__ == "__main__":
    unittest.main()
