"""
เทสต์ตัวคัดแยกด้วยโมเดล และ /reclassify

หลักที่ต้องคุมไว้: **กฎคือพื้น โมเดลคือส่วนเพิ่ม** ทุกทางที่โมเดลพัง ตอบช้า
ตอบผิดรูป หรือตอบค่าที่ schema ไม่รับ ต้องตกกลับไปใช้ผลของ regex — ห้ามมีทางที่
ข้อความไม่ถูกคัดแยกเลยเพราะโมเดลล่ม และห้ามมีค่าที่ CHECK ของ sqlite ปฏิเสธ
หลุดไปถึง UPDATE เพราะแถวนั้นจะหายทั้งแถว

อาการจริงที่ทำให้ต้องมีไฟล์นี้: ประวัติแชทงานรีโนเวตของเจ้าของถูก regex จัดเป็น
smalltalk 80 จาก 99 ข้อความขาเข้า ทั้งที่เนื้อหาเป็นราคางานและงวดเงิน
"""

import json
import unittest
from types import SimpleNamespace
from unittest import mock

import line_export
import llm_classifier
from tests._bot_case import BotDbCase, bot


def fake_openai(payload, *, raises=None):
    """client ปลอมที่คืน payload ที่กำหนด — ไม่มีการเรียกเครือข่ายจริงในเทสต์"""
    def create(**kwargs):
        if raises is not None:
            raise raises
        content = payload if isinstance(payload, str) else json.dumps(payload)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


class TestValidation(unittest.TestCase):
    """ค่าที่ schema เก็บไม่ได้ต้องถูกทิ้ง ไม่ใช่ส่งต่อไปให้ sqlite ปฏิเสธ"""

    def test_a_good_result_passes(self):
        self.assertEqual(
            llm_classifier._clean(
                {"intent": "request", "urgency": "high", "confidence": 0.9}
            ),
            {"intent": "request", "urgency": "high", "confidence": 0.9},
        )

    def test_an_intent_outside_the_check_constraint_is_rejected(self):
        self.assertIsNone(
            llm_classifier._clean(
                {"intent": "complaint", "urgency": "high", "confidence": 0.9}
            )
        )

    def test_an_urgency_outside_the_check_constraint_is_rejected(self):
        self.assertIsNone(
            llm_classifier._clean(
                {"intent": "request", "urgency": "critical", "confidence": 0.9}
            )
        )

    def test_confidence_outside_zero_to_one_is_rejected(self):
        for bad in (1.5, -0.2):
            with self.subTest(confidence=bad):
                self.assertIsNone(
                    llm_classifier._clean(
                        {"intent": "request", "urgency": "low", "confidence": bad}
                    )
                )

    def test_confidence_that_is_not_a_number_is_rejected(self):
        self.assertIsNone(
            llm_classifier._clean(
                {"intent": "request", "urgency": "low", "confidence": "สูง"}
            )
        )

    def test_a_non_dict_is_rejected(self):
        for bad in (None, [], "request", 7):
            with self.subTest(value=bad):
                self.assertIsNone(llm_classifier._clean(bad))


class TestFallsBackToRules(unittest.TestCase):
    TEXTS = ["ขอราคาหน่อยครับ", "สวัสดีครับ"]

    def rules(self):
        import line_webhook
        return [line_webhook.classify_message(t) for t in self.TEXTS]

    def test_no_api_key_means_rules(self):
        with mock.patch.object(llm_classifier, "_client", return_value=None):
            self.assertEqual(
                llm_classifier.classify_batch_sync(self.TEXTS), self.rules()
            )

    def test_a_failing_call_falls_back_instead_of_raising(self):
        client = fake_openai(None, raises=RuntimeError("upstream down"))
        with mock.patch.object(llm_classifier, "_client", return_value=client):
            with self.assertLogs("llm_classifier", level="ERROR"):
                got = llm_classifier.classify_batch_sync(self.TEXTS)
        self.assertEqual(got, self.rules())

    def test_unparsable_json_falls_back(self):
        client = fake_openai("ไม่ใช่ JSON เลย")
        with mock.patch.object(llm_classifier, "_client", return_value=client):
            with self.assertLogs("llm_classifier", level="ERROR"):
                got = llm_classifier.classify_batch_sync(self.TEXTS)
        self.assertEqual(got, self.rules())

    def test_the_provider_error_text_never_escapes(self):
        """บทเรียนจาก #24 — ข้อความของผู้ให้บริการต้องไม่ไปโผล่ที่ผู้ใช้"""
        secret = "Incorrect API key provided: 8666****3RAI"
        client = fake_openai(None, raises=RuntimeError(secret))
        with mock.patch.object(llm_classifier, "_client", return_value=client):
            with self.assertLogs("llm_classifier", level="ERROR") as captured:
                got = llm_classifier.classify_batch_sync(self.TEXTS)
        self.assertEqual(got, self.rules())          # ผลที่ผู้ใช้เห็น
        self.assertIn(secret, "\n".join(captured.output))   # รายละเอียดอยู่ใน log

    def test_a_short_answer_leaves_the_rest_on_rules(self):
        """โมเดลตอบมาข้อเดียวจากสองข้อ — อีกข้อต้องไม่กลายเป็นค่าว่าง"""
        client = fake_openai(
            {"results": [{"i": 0, "intent": "request", "urgency": "normal",
                          "confidence": 0.95}]}
        )
        with mock.patch.object(llm_classifier, "_client", return_value=client):
            with self.assertLogs("llm_classifier", level="INFO"):
                got = llm_classifier.classify_batch_sync(self.TEXTS)
        self.assertEqual(got[0]["confidence"], 0.95)
        self.assertEqual(got[1], self.rules()[1])

    def test_an_out_of_range_index_is_ignored(self):
        client = fake_openai(
            {"results": [{"i": 99, "intent": "request", "urgency": "low",
                          "confidence": 0.9}]}
        )
        with mock.patch.object(llm_classifier, "_client", return_value=client):
            with self.assertLogs("llm_classifier", level="INFO"):
                got = llm_classifier.classify_batch_sync(self.TEXTS)
        self.assertEqual(got, self.rules())

    def test_the_result_length_always_matches_the_input(self):
        for payload in ({"results": []}, {}, {"results": [1, 2, 3]}):
            with self.subTest(payload=payload):
                client = fake_openai(payload)
                with mock.patch.object(llm_classifier, "_client", return_value=client):
                    with self.assertLogs("llm_classifier", level="INFO"):
                        got = llm_classifier.classify_batch_sync(self.TEXTS)
                self.assertEqual(len(got), len(self.TEXTS))

    def test_an_empty_input_needs_no_call(self):
        def explode(**kwargs):
            raise AssertionError("ไม่ควรเรียกโมเดลเมื่อไม่มีข้อความ")
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=explode))
        )
        with mock.patch.object(llm_classifier, "_client", return_value=client):
            self.assertEqual(llm_classifier.classify_batch_sync([]), [])


class TestTheRuleFloorItself(unittest.TestCase):
    """กฎคือพื้น ถ้าพื้นผิด โมเดลล่มเมื่อไรก็ผิดตาม

    เจอตอนเขียนเทสต์ของโมเดล: REQUEST_RE จับ "ขอ" ที่อยู่ข้างในคำว่า "ของ" ซึ่ง
    เป็นคำที่โผล่แทบทุกประโยค ผลคือ "ของถึงแล้ว" กลายเป็น request ที่ความมั่นใจ
    0.86 — เหนือ cfg.min_classifier_confidence (0.80) จึงถูก view นับจริง ไม่ใช่
    แค่เดาเล่น ๆ ที่ถูกกรองทิ้ง
    """

    def rules(self, text):
        import line_webhook
        return line_webhook.classify_message(text)["intent"]

    def test_the_word_khong_is_not_a_request(self):
        for text in ("ของถึงแล้ว", "ค่าของอีก 7,500 ครับ", "ของอยู่ที่หน้างาน"):
            with self.subTest(text=text):
                self.assertNotEqual(self.rules(text), "request")

    def test_a_real_request_still_reads_as_one(self):
        for text in ("ขอราคาหน่อยครับ", "ขอของหน่อย", "ช่วยส่งไฟล์ให้หน่อย"):
            with self.subTest(text=text):
                self.assertEqual(self.rules(text), "request")

    def test_the_polite_words_are_still_excluded(self):
        for text in ("ขอบคุณครับ", "ขอโทษครับ"):
            with self.subTest(text=text):
                self.assertNotEqual(self.rules(text), "request")


class TestModelWins(unittest.TestCase):
    def test_the_model_can_rescue_what_the_rules_call_smalltalk(self):
        """ประโยคจริงจากแชทของเจ้าของ — ไม่มีคำว่า "ขอ"/"ช่วย" กฎจึงเดาไม่ออก"""
        import line_webhook
        text = "ยอดงาน 30,000 บาทแจ้งเบิก 15,000 บาทครับ"
        self.assertEqual(line_webhook.classify_message(text)["intent"], "smalltalk")

        client = fake_openai(
            {"results": [{"i": 0, "intent": "decision", "urgency": "normal",
                          "confidence": 0.88}]}
        )
        with mock.patch.object(llm_classifier, "_client", return_value=client):
            got = llm_classifier.classify_batch_sync([text])
        self.assertEqual(got[0]["intent"], "decision")

    def test_batches_are_split_but_every_message_comes_back(self):
        texts = [f"ข้อความที่ {n}" for n in range(7)]
        calls = []

        def fake_batch(chunk, report=None):
            calls.append(len(chunk))
            return [{"intent": "smalltalk", "urgency": "normal", "confidence": 0.5}
                    for _ in chunk]

        with mock.patch.object(llm_classifier, "classify_batch_sync", fake_batch):
            with mock.patch.object(llm_classifier, "BATCH_SIZE", 3):
                got = llm_classifier.classify_all_sync(texts)
        self.assertEqual(len(got), 7)
        self.assertEqual(calls, [3, 3, 1])


EXPORT = (
    "2026.07.09 วันพฤหัสบดี\n"
    "09:15 Ann Lee ยอดงาน 30,000 บาทแจ้งเบิก 15,000 บาทครับ\n"
    "09:16 Bob รับทราบครับ\n"
    "09:17 Ann Lee ค่าของอีก 7,500 ครับ\n"
    "09:18 Bob โอเคครับ\n"
)


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class FakeUpdate:
    def __init__(self):
        self.effective_user = SimpleNamespace(id=7, first_name="J")
        self.message = FakeMessage()


class TestTheReportSaysWhatActuallyRan(unittest.TestCase):
    """อาการจริง: /reclassify ตอบ "ตรวจ 500 เปลี่ยนหมวด 0" ทั้งที่โมเดลไม่ได้ถูก
    เรียกเลย เพราะเวลาโมเดลล้ม โค้ดตกกลับไปใช้ regex ตัวเดิม ซึ่งให้คำตอบเดิมเป๊ะ
    ทุกแถวจึงนับว่า "ไม่เปลี่ยน" เจ้าของอ่านแล้วสรุปว่ากฎเดิมถูกอยู่แล้ว
    """

    TEXTS = ["ขอราคาหน่อยครับ", "สวัสดีครับ"]

    def test_a_working_model_is_counted_as_the_model(self):
        client = fake_openai(
            {"results": [{"i": i, "intent": "decision", "urgency": "normal",
                          "confidence": 0.9} for i in range(2)]}
        )
        report = {}
        with mock.patch.object(llm_classifier, "_client", return_value=client):
            llm_classifier.classify_all_sync(self.TEXTS, report)
        self.assertEqual(report["by_model"], 2)
        self.assertEqual(report["by_rules"], 0)
        self.assertEqual(report.get("failures", []), [])

    def test_a_failing_call_is_counted_as_rules_with_a_reason(self):
        class AuthenticationError(RuntimeError):
            pass

        client = fake_openai(None, raises=AuthenticationError("boom"))
        report = {}
        with mock.patch.object(llm_classifier, "_client", return_value=client):
            with self.assertLogs("llm_classifier", level="ERROR"):
                llm_classifier.classify_all_sync(self.TEXTS, report)
        self.assertEqual(report["by_model"], 0)
        self.assertEqual(report["by_rules"], 2)
        self.assertEqual(report["failures"], ["AuthenticationError"])

    def test_the_reason_never_carries_the_provider_text(self):
        """ต่อจาก #24 — รายงานที่ผู้ใช้เห็นต้องมีแค่ชื่อคลาส ไม่มีเนื้อความ"""
        secret = "Incorrect API key provided: 8666****3RAI"
        client = fake_openai(None, raises=RuntimeError(secret))
        report = {}
        with mock.patch.object(llm_classifier, "_client", return_value=client):
            with self.assertLogs("llm_classifier", level="ERROR"):
                llm_classifier.classify_all_sync(self.TEXTS, report)
        self.assertNotIn(secret, json.dumps(report, ensure_ascii=False))
        self.assertEqual(report["failures"], ["RuntimeError"])

    def test_a_missing_key_says_which_setting_is_missing(self):
        report = {}
        with mock.patch.dict(llm_classifier.os.environ, {"OPENAI_API_KEY": ""}):
            with mock.patch.object(llm_classifier, "_client", return_value=None):
                llm_classifier.classify_all_sync(self.TEXTS, report)
        self.assertEqual(report["failures"], ["ไม่ได้ตั้ง OPENAI_API_KEY"])

    def test_a_partial_answer_splits_the_count(self):
        client = fake_openai(
            {"results": [{"i": 0, "intent": "request", "urgency": "normal",
                          "confidence": 0.9}]}
        )
        report = {}
        with mock.patch.object(llm_classifier, "_client", return_value=client):
            llm_classifier.classify_all_sync(self.TEXTS, report)
        self.assertEqual(report["by_model"], 1)
        self.assertEqual(report["by_rules"], 1)
        self.assertEqual(report["failures"], ["โมเดลตอบไม่ครบ"])

    def test_the_report_is_optional(self):
        """โค้ดเดิมเรียกโดยไม่ส่ง report — ต้องไม่พัง"""
        with mock.patch.object(llm_classifier, "_client", return_value=None):
            self.assertEqual(len(llm_classifier.classify_all_sync(self.TEXTS)), 2)


class TestReclassifyCommand(BotDbCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        line_export.import_from_text(
            bot.DB_PATH, EXPORT, owner_name="Bob", file_name="งาน.txt"
        )

    async def run_command(self):
        update = FakeUpdate()
        await bot.reclassify_command(update, SimpleNamespace(args=[]))
        return update.message.replies

    async def test_without_an_api_key_it_says_so_and_changes_nothing(self):
        with mock.patch.object(bot, "OPENAI_API_KEY", ""):
            replies = await self.run_command()
        self.assertIn("OPENAI_API_KEY", replies[0])

    async def test_it_only_looks_at_low_confidence_inbound_messages(self):
        rows = bot._messages_to_reclassify(bot.DB_PATH, 100)
        bodies = [r["body"] for r in rows]
        self.assertIn("ยอดงาน 30,000 บาทแจ้งเบิก 15,000 บาทครับ", bodies)
        self.assertNotIn("รับทราบครับ", bodies)   # ของ Bob = ขาออก

    async def test_a_confident_row_is_left_alone(self):
        conn = bot.connect(bot.DB_PATH)
        try:
            with conn:
                conn.execute("UPDATE chat_messages SET confidence = 0.95")
        finally:
            conn.close()
        self.assertEqual(bot._messages_to_reclassify(bot.DB_PATH, 100), [])

    async def test_it_warns_when_the_model_never_ran(self):
        """ของจริงที่เจอ: ตอบ "ตรวจ 500 / เปลี่ยนหมวด 0" โดยไม่บอกว่าโมเดลล้ม"""
        client = fake_openai(None, raises=RuntimeError("upstream down"))
        with mock.patch.object(bot, "OPENAI_API_KEY", "sk-test"), \
             mock.patch.object(llm_classifier, "_client", return_value=client):
            with self.assertLogs("llm_classifier", level="ERROR"):
                replies = await self.run_command()
        reply = replies[-1]
        self.assertIn("โมเดลไม่ได้ทำงานเลย", reply)
        self.assertIn("RuntimeError", reply)
        self.assertNotIn("upstream down", reply)

    async def test_a_working_model_gets_no_warning(self):
        client = fake_openai(
            {"results": [{"i": i, "intent": "decision", "urgency": "normal",
                          "confidence": 0.88} for i in range(10)]}
        )
        with mock.patch.object(bot, "OPENAI_API_KEY", "sk-test"), \
             mock.patch.object(llm_classifier, "_client", return_value=client):
            replies = await self.run_command()
        self.assertNotIn("⚠️", replies[-1])

    async def test_it_says_how_many_messages_became_usable(self):
        """ตัวเลขที่เจ้าของอยากรู้จริง: ได้ข้อความคืนมาใช้งานกี่ข้อความ

        ทุกแถวที่เข้า /reclassify ยังไม่ถึงเกณฑ์ 0.80 ของ view — ถ้าโมเดลตอบมา
        ต่ำกว่าเกณฑ์อีก ข้อความก็ยังถูกมองข้ามเหมือนเดิม "เปลี่ยนหมวด" จึงไม่ใช่
        คำตอบว่างานคืบหน้าไหม
        """
        client = fake_openai(
            {"results": [{"i": i, "intent": "decision", "urgency": "normal",
                          "confidence": 0.42} for i in range(10)]}
        )
        with mock.patch.object(bot, "OPENAI_API_KEY", "sk-test"), \
             mock.patch.object(llm_classifier, "_client", return_value=client):
            replies = await self.run_command()
        self.assertIn("ผ่านเกณฑ์ 0.8 แล้ว: 0", replies[-1])

    async def test_the_new_verdict_is_written_and_counted(self):
        client = fake_openai(
            {"results": [{"i": i, "intent": "decision", "urgency": "normal",
                          "confidence": 0.88} for i in range(10)]}
        )
        with mock.patch.object(bot, "OPENAI_API_KEY", "sk-test"), \
             mock.patch.object(llm_classifier, "_client", return_value=client):
            replies = await self.run_command()
        self.assertIn("คัดแยกใหม่แล้ว", replies[-1])
        rows = self.rows(
            "SELECT intent, confidence FROM chat_messages WHERE direction = 'in'"
        )
        for row in rows:
            self.assertEqual(row["intent"], "decision")
            self.assertEqual(row["confidence"], 0.88)

    async def test_nothing_to_do_is_reported_plainly(self):
        conn = bot.connect(bot.DB_PATH)
        try:
            with conn:
                conn.execute("UPDATE chat_messages SET confidence = 0.95")
        finally:
            conn.close()
        with mock.patch.object(bot, "OPENAI_API_KEY", "sk-test"):
            replies = await self.run_command()
        self.assertIn("ไม่มีข้อความที่ต้องคัดแยกใหม่", replies[0])


if __name__ == "__main__":
    unittest.main()
