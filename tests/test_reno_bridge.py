"""
เทสต์ของ reno_bridge.py — สะพานระหว่าง LINE webhook กับ Reno Dashboard

ไฟล์ HTML ที่ใช้ทดสอบเป็นตัวย่อที่มีโครงเหมือนของจริง (const T0 / P0 / SAMPLE_ITEMS)
เพื่อให้เทสต์ไม่ต้องพึ่งไฟล์ dashboard ของผู้ใช้
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import reno_bridge as rb

REPO_ROOT = Path(__file__).resolve().parent.parent

DASHBOARD_HTML = """<html><script>
const COLS=['Todo','In Progress','Review','Done'];
const T0=[
  {id:1,t:'รื้อฝ้า',n:'✅ เสร็จ',s:'Done',p:'Lipa',type:'Demo',cost:10500,date:'2026-05-18',pri:'High',by:'MR'},
  {id:2,t:"งานที่มี ' และ [ วงเล็บ",n:'',s:'Todo',p:'Chaweng',type:'General',cost:0,date:'',pri:'Low',by:'MR'},
];
const P0=[
  {id:1,date:'18/05/69',desc:'ค่าแรง',proj:'Lipa',who:'MR.HOME KOH SAMUI',amount:10500,status:'paid'},
];
function save(){}
</script></html>
"""

INVENTORY_HTML = """<html><script>
const SAMPLE_ITEMS = [
  {id:'ITM-GH001',sku:'ELTD',name:'ดาวน์ไลท์',cat:'โคมไฟ',icon:'🔆',proj:'Chaweng',qty:10,minQty:4,unit:'ดวง',price:125,loc:'โกดัง',note:''},
];
</script></html>
"""


def make_db(path):
    conn = sqlite3.connect(path)
    conn.executescript((REPO_ROOT / "sql" / "01_schema.sql").read_text())
    conn.executescript(
        """
        INSERT INTO contacts(id,display_name,line_user_id) VALUES(1,'พี่ปอ','Umr');
        INSERT INTO chat_threads(id,platform,external_chat_id,title,is_group)
          VALUES(1,'line','Umr','พี่ปอ',0);
        """
    )
    conn.commit()
    conn.close()


class BridgeCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.db_path = str(self.dir / "test.db")
        make_db(self.db_path)

        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        rb.init_bridge_tables(self.conn)

        (self.dir / "dashboard-final.html").write_text(DASHBOARD_HTML, encoding="utf-8")
        (self.dir / "inventory-system.html").write_text(INVENTORY_HTML, encoding="utf-8")

        self.config = rb.load_config(str(self.dir / "missing.md"))
        self.config["dashboard_path"] = str(self.dir)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def add_message(self, body, sent_at="2026-08-20T10:00:00Z", contact_id=1):
        cursor = self.conn.execute(
            """
            INSERT INTO chat_messages(thread_id,contact_id,direction,body,sent_at,intent,confidence)
            VALUES(1,?,'in',?,?,'request',0.9)
            """,
            (contact_id, body, sent_at),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def rows(self, sql, params=()):
        return [dict(r) for r in self.conn.execute(sql, params)]


# ═══════════════════════════════════════════════════════════════════════════════

class TestConfig(unittest.TestCase):
    def test_defaults_when_no_config_file(self):
        config = rb.load_config("/nonexistent/CONFIG.md")
        self.assertEqual(config["site_a"], "Lipa")
        self.assertEqual(config["site_b"], "Chaweng")
        self.assertFalse(rb.single_site(config))

    def test_reads_markdown_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CONFIG.md"
            path.write_text(
                "| Category | Placeholder | Value |\n"
                "| --- | --- | --- |\n"
                "| Primary site | `~~site-a` | Baan Suan |\n"
                "| Secondary site | `~~site-b` |  |\n"
                "| Contractor | `~~contractor` | Khun Somchai |\n"
                "| Currency symbol | `~~currency-symbol` | $ |\n",
                encoding="utf-8",
            )
            config = rb.load_config(str(path))
        self.assertEqual(config["site_a"], "Baan Suan")
        self.assertEqual(config["contractor"], "Khun Somchai")
        self.assertEqual(config["currency_symbol"], "$")
        self.assertTrue(rb.single_site(config), "ปล่อย ~~site-b ว่าง = โหมดไซต์เดียว")

    def test_reads_list_form_and_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CONFIG.md"
            path.write_text("- ~~site-a: ลิปะน้อย\n- ~~site-a-aliases: บ้านแม่, lipa\n", encoding="utf-8")
            config = rb.load_config(str(path))
        self.assertEqual(config["site_a"], "ลิปะน้อย")
        self.assertIn("บ้านแม่", config["site_a_aliases"])
        self.assertIn("ลิปะน้อย", config["site_a_aliases"], "ชื่อไซต์ต้องเป็น alias ของตัวเอง")


class TestExtraction(unittest.TestCase):
    def setUp(self):
        self.config = rb.load_config("/nonexistent")

    def extract(self, text):
        return rb.extract_items(text, sent_at="2026-08-20T10:00:00Z", config=self.config)

    def test_site_routing(self):
        self.assertEqual(rb.detect_site("งานที่เฉวงชั้น 3", self.config), "Chaweng")
        self.assertEqual(rb.detect_site("ห้องน้ำลิปะน้อย", self.config), "Lipa")

    def test_ambiguous_site_returns_none(self):
        # กฎเหล็กข้อ 1: ห้ามรวมสองไซต์ — เดาไม่ได้ต้องให้คนตัดสิน
        self.assertIsNone(rb.detect_site("ส่งของไปลิปะแล้วแวะเฉวง", self.config))

    def test_advance_request_is_pending_payment(self):
        items = self.extract("พี่ปอขอเบิกค่าแรงงานระบบไฟ 15,000 บาท ตึกเฉวง")
        payment = next(i for i in items if i["kind"] == "payment")
        self.assertEqual(payment["payload"]["amount"], 15000)
        self.assertEqual(payment["payload"]["status"], "pending")
        self.assertEqual(payment["payload"]["project"], "Chaweng")
        self.assertEqual(payment["payload"]["date"], "20/08/69")

    def test_paid_wording_flips_status(self):
        items = self.extract("โอนแล้วนะครับ ค่าแรง 12,450 บาท งานเฉวง")
        payment = next(i for i in items if i["kind"] == "payment")
        self.assertEqual(payment["payload"]["status"], "paid")

    def test_unknown_site_lowers_confidence(self):
        items = self.extract("ขอเบิกค่าแรง 8,000 บาท")
        payment = next(i for i in items if i["kind"] == "payment")
        self.assertIsNone(payment["site"])
        self.assertLess(payment["confidence"], 0.8)

    def test_largest_amount_wins(self):
        items = self.extract("บิลเฉวง ค่าของ 1,250 + ค่าส่ง 200 รวม 1,450 บาท")
        payment = next(i for i in items if i["kind"] == "payment")
        self.assertEqual(payment["payload"]["amount"], 1450)

    def test_task_type_and_status(self):
        items = self.extract("เดินสายไฟชั้น 3 เฉวงเสร็จเรียบร้อยแล้ว")
        task = next(i for i in items if i["kind"] == "task")
        self.assertEqual(task["payload"]["type"], "Electrical")
        self.assertEqual(task["payload"]["status"], "Done")
        self.assertEqual(task["payload"]["date"], "2026-08-20")

    def test_task_waiting_for_decision_is_review(self):
        items = self.extract("รื้อฝ้าชั้น 2 ลิปะ รอยืนยันก่อนนะครับ")
        task = next(i for i in items if i["kind"] == "task")
        self.assertEqual(task["payload"]["status"], "Review")

    def test_urgent_task_priority(self):
        items = self.extract("ด่วน ต้องปูกระเบื้องห้องน้ำลิปะวันนี้")
        task = next(i for i in items if i["kind"] == "task")
        self.assertEqual(task["payload"]["priority"], "High")

    def test_stock_from_a_bill(self):
        items = self.extract("ซื้อดาวน์ไลท์ที่ HomePro 10 ดวง 1,250 บาท ส่งไปเฉวง")
        stock = next(i for i in items if i["kind"] == "stock")
        self.assertEqual(stock["payload"]["qty"], 10)
        self.assertEqual(stock["payload"]["unit"], "ดวง")
        self.assertEqual(stock["payload"]["cat"], "โคมไฟ")
        self.assertEqual(stock["payload"]["price"], 125)

    def test_smalltalk_yields_nothing(self):
        self.assertEqual(self.extract("ครับผม"), [])
        self.assertEqual(self.extract("สวัสดีครับ วันนี้อากาศดี"), [])

    def test_payload_keys_match_dashboard_importer(self):
        # showPreview()/applyAll() ใน dashboard-final.html อ่านคีย์เหล่านี้ตรง ๆ
        items = self.extract("ขอเบิกค่าแรง 9,000 บาท งานเฉวง")
        payment = next(i for i in items if i["kind"] == "payment")
        self.assertEqual(
            set(payment["payload"]), {"date", "desc", "project", "who", "amount", "status"}
        )
        items = self.extract("ติดตั้งสุขภัณฑ์ลิปะ")
        task = next(i for i in items if i["kind"] == "task")
        self.assertEqual(
            set(task["payload"]),
            {"title", "note", "status", "project", "type", "cost", "date", "priority"},
        )


class TestQueue(BridgeCase):
    def test_scan_queues_items(self):
        message_id = self.add_message("ขอเบิกค่าแรงเฉวง 15,000 บาท")
        queued = rb.scan_message(
            self.conn,
            chat_message_id=message_id,
            text="ขอเบิกค่าแรงเฉวง 15,000 บาท",
            sent_at="2026-08-20T10:00:00Z",
            config=self.config,
        )
        self.assertTrue(queued)
        self.assertEqual(rb.get_inbox(self.conn)[0]["status"], "pending")

    def test_rescanning_the_same_message_adds_nothing(self):
        text = "ขอเบิกค่าแรงเฉวง 15,000 บาท"
        message_id = self.add_message(text)
        for _ in range(3):
            rb.scan_message(
                self.conn, chat_message_id=message_id, text=text,
                sent_at="2026-08-20T10:00:00Z", config=self.config,
            )
        self.assertEqual(len(rb.get_inbox(self.conn, "all")), 1)

    def test_backlog_scan_moves_the_cursor(self):
        self.add_message("ขอเบิกค่าแรงเฉวง 15,000 บาท")
        first = rb.scan_backlog(self.conn, self.config)
        self.assertGreater(first, 0)
        self.assertEqual(rb.scan_backlog(self.conn, self.config), 0, "scan ซ้ำต้องไม่ได้อะไรเพิ่ม")

        self.add_message("โอนแล้ว 5,000 บาท งานลิปะ")
        self.assertGreater(rb.scan_backlog(self.conn, self.config), 0)

    def test_import_payload_shape(self):
        message_id = self.add_message("ขอเบิกค่าแรงเฉวง 15,000 บาท")
        rb.scan_message(
            self.conn, chat_message_id=message_id, text="ขอเบิกค่าแรงเฉวง 15,000 บาท",
            sent_at="2026-08-20T10:00:00Z", config=self.config,
        )
        payload = rb.as_import_payload(rb.get_inbox(self.conn), self.config)
        self.assertEqual(set(payload) >= {"tasks", "payments", "summary"}, True)
        self.assertIn("฿15,000", payload["summary"])


class TestArrayWriting(unittest.TestCase):
    def test_appends_before_the_closing_bracket(self):
        source, refs = rb.append_to_array(
            DASHBOARD_HTML, "T0", [{"t": "งานใหม่", "s": "Todo", "p": "Lipa"}], "ทดสอบ"
        )
        self.assertEqual(refs, ["3"], "id ต่อจากตัวที่มากที่สุดเดิม")
        self.assertIn("t:'งานใหม่'", source)
        self.assertIn("const P0=[", source, "array อื่นต้องไม่ถูกแตะ")
        # แถวใหม่ต้องอยู่ในวงเล็บของ T0 ไม่ใช่หลุดออกไปข้างนอก
        start, close = rb._find_array_span(source, "T0")
        self.assertIn("งานใหม่", source[start:close])

    def test_bracket_scan_survives_quotes_and_brackets_in_text(self):
        start, close = rb._find_array_span(DASHBOARD_HTML, "T0")
        segment = DASHBOARD_HTML[start:close]
        self.assertIn("วงเล็บ", segment)
        self.assertNotIn("const P0", segment)

    def test_escapes_quotes_in_values(self):
        source, _ = rb.append_to_array(
            DASHBOARD_HTML, "T0", [{"t": "งาน'พิเศษ", "s": "Todo"}], "ทดสอบ"
        )
        self.assertIn("\\'", source)

    def test_missing_array_raises(self):
        with self.assertRaises(rb.ArrayNotFound):
            rb.append_to_array("<html></html>", "T0", [{"t": "x"}], "m")


class TestApply(BridgeCase):
    def queue(self, text, sent_at="2026-08-20T10:00:00Z"):
        message_id = self.add_message(text, sent_at)
        rb.scan_message(
            self.conn, chat_message_id=message_id, text=text, sent_at=sent_at, config=self.config
        )
        return message_id

    def test_apply_writes_files_tasks_and_links(self):
        message_id = self.queue("เดินสายไฟชั้น 3 เฉวงเสร็จแล้ว เบิกค่าแรง 15,000 บาท")
        items = rb.get_inbox(self.conn)
        rb.set_status(self.conn, [i["id"] for i in items], "approved")

        result = rb.apply_items(self.conn, rb.get_inbox(self.conn, "approved"), self.config)
        self.assertGreaterEqual(result["tasks"] + result["payments"], 2)

        html = (self.dir / "dashboard-final.html").read_text(encoding="utf-8")
        self.assertIn("เดินสายไฟชั้น 3", html)
        self.assertIn("amount:15000", html)
        self.assertTrue((self.dir / "dashboard-final.html.bak").is_file(), "ต้องสำรองไฟล์เดิม")

        # งานต้องโผล่ใน Life OS ด้วย ไม่งั้น view คอขวดมองไม่เห็น
        task = self.rows("SELECT * FROM tasks")[0]
        self.assertEqual(task["source"], "line")
        self.assertEqual(task["source_ref"], str(message_id))
        self.assertEqual(self.rows("SELECT linked_task_id FROM chat_messages")[0]["linked_task_id"], task["id"])
        self.assertTrue(self.rows("SELECT * FROM task_events WHERE task_id=?", (task["id"],)))

        self.assertEqual([i["status"] for i in rb.get_inbox(self.conn, "all")], ["applied"] * 2)

    def test_done_task_lands_as_done(self):
        self.queue("ฉาบผนังห้องน้ำชั้น 1 เฉวงเสร็จเรียบร้อย")
        rb.set_status(self.conn, [i["id"] for i in rb.get_inbox(self.conn)], "approved")
        rb.apply_items(self.conn, rb.get_inbox(self.conn, "approved"), self.config)
        task = self.rows("SELECT * FROM tasks")[0]
        self.assertEqual(task["status"], "done")
        self.assertIsNotNone(task["completed_at"])

    def test_apply_refuses_items_without_a_site(self):
        self.queue("ขอเบิกค่าแรง 8,000 บาท")
        rb.set_status(self.conn, [i["id"] for i in rb.get_inbox(self.conn)], "approved")
        with self.assertRaises(ValueError):
            rb.apply_items(self.conn, rb.get_inbox(self.conn, "approved"), self.config)
        self.assertEqual(rb.get_inbox(self.conn, "approved")[0]["status"], "approved")

    def test_single_site_mode_applies_without_a_site(self):
        self.config["site_b"] = ""
        self.queue("ขอเบิกค่าแรง 8,000 บาท")
        rb.set_status(self.conn, [i["id"] for i in rb.get_inbox(self.conn)], "approved")
        rb.apply_items(self.conn, rb.get_inbox(self.conn, "approved"), self.config)
        html = (self.dir / "dashboard-final.html").read_text(encoding="utf-8")
        self.assertIn("proj:'Lipa'", html)

    def test_dry_run_touches_nothing(self):
        self.queue("ติดตั้งสุขภัณฑ์ลิปะ")
        rb.set_status(self.conn, [i["id"] for i in rb.get_inbox(self.conn)], "approved")
        before = (self.dir / "dashboard-final.html").read_text(encoding="utf-8")
        rb.apply_items(self.conn, rb.get_inbox(self.conn, "approved"), self.config, dry_run=True)
        self.assertEqual((self.dir / "dashboard-final.html").read_text(encoding="utf-8"), before)
        self.assertEqual(rb.get_inbox(self.conn, "approved")[0]["status"], "approved")

    def test_stock_goes_to_the_inventory_file(self):
        self.queue("ซื้อดาวน์ไลท์ HomePro 10 ดวง 1,250 บาท ส่งเฉวง")
        stock = [i for i in rb.get_inbox(self.conn) if i["kind"] == "stock"]
        rb.set_status(self.conn, [i["id"] for i in stock], "approved")
        rb.apply_items(self.conn, rb.get_inbox(self.conn, "approved"), self.config)
        html = (self.dir / "inventory-system.html").read_text(encoding="utf-8")
        self.assertIn("ITM-LINE", html)
        self.assertIn("qty:10", html)


class TestChatCommands(BridgeCase):
    def setUp(self):
        super().setUp()
        self.bridge = rb.RenoBridge(self.config)

    def queue_one(self, text="ขอเบิกค่าแรง 8,000 บาท"):
        message_id = self.add_message(text)
        return self.bridge.on_message(
            self.conn, chat_message_id=message_id, text=text, sent_at="2026-08-20T10:00:00Z"
        )

    def test_summary_warns_about_unknown_site(self):
        summary = self.queue_one()
        self.assertIn("ยังไม่รู้ไซต์", summary)
        self.assertIn("reno site", summary)

    def test_ok_is_blocked_until_the_site_is_set(self):
        self.queue_one()
        reply = self.bridge.handle_command(self.conn, "reno ok")
        self.assertIn("ยังไม่ได้ระบุไซต์", reply)
        self.assertEqual(rb.get_inbox(self.conn, "approved"), [])

    def test_site_then_ok(self):
        self.queue_one()
        item_id = rb.get_inbox(self.conn)[0]["id"]
        self.bridge.handle_command(self.conn, f"reno site #{item_id} เฉวง")
        item = rb.get_inbox(self.conn)[0]
        self.assertEqual(item["site"], "Chaweng")
        self.assertEqual(item["payload"]["project"], "Chaweng")

        self.bridge.handle_command(self.conn, "reno ok")
        self.assertEqual(len(rb.get_inbox(self.conn, "approved")), 1)

    def test_skip(self):
        self.queue_one("ซ่อมประตูลิปะ")
        self.bridge.handle_command(self.conn, "reno skip")
        self.assertEqual(rb.get_inbox(self.conn), [])
        self.assertEqual(len(rb.get_inbox(self.conn, "skipped")), 1)

    def test_plain_text_is_not_a_command(self):
        self.assertIsNone(self.bridge.handle_command(self.conn, "เดี๋ยวส่งบิลให้นะ"))

    def test_auto_approve_mode(self):
        bridge = rb.RenoBridge(self.config, auto_approve=True)
        message_id = self.add_message("ซ่อมประตูลิปะ")
        bridge.on_message(
            self.conn, chat_message_id=message_id, text="ซ่อมประตูลิปะ",
            sent_at="2026-08-20T10:00:00Z",
        )
        self.assertEqual(len(rb.get_inbox(self.conn, "approved")), 1)


class TestSnapshot(BridgeCase):
    def test_reports_waits_and_unanswered(self):
        self.conn.executescript(
            """
            INSERT INTO tasks(id,title,status,created_at,blocked_since,blocked_reason,blocked_on_contact_id)
              VALUES(1,'ตามใบเสนอราคา','blocked','2026-08-01T00:00:00Z','2026-08-19T09:00:00Z','รอคนตอบ',1);
            INSERT INTO task_blocks(task_id,reason,contact_id,blocked_at)
              VALUES(1,'รอคนตอบ',1,'2026-08-19T09:00:00Z');
            INSERT INTO chat_messages(thread_id,contact_id,direction,body,sent_at,intent,confidence)
              VALUES(1,1,'in','ราคาสุดท้ายเท่าไหร่','2026-08-20T10:00:00Z','question',0.95);
            """
        )
        self.conn.commit()

        data = rb.snapshot(self.conn, self.config)
        self.assertEqual(len(data["blocked_now"]), 1)
        self.assertEqual(data["blocked_now"][0]["person"], "พี่ปอ")
        self.assertEqual(len(data["unanswered_now"]), 1)
        self.assertIn("ราคาสุดท้าย", data["unanswered_now"][0]["preview"])
        self.assertIn("Lipa", data["sites"])

    def test_reply_clears_the_unanswered_list(self):
        self.conn.executescript(
            """
            INSERT INTO chat_messages(thread_id,contact_id,direction,body,sent_at,intent,confidence)
              VALUES(1,1,'in','ส่งราคามาหน่อย','2026-08-20T10:00:00Z','request',0.95);
            INSERT INTO chat_messages(thread_id,contact_id,direction,body,sent_at)
              VALUES(1,NULL,'out','ส่งให้แล้วครับ','2026-08-20T11:00:00Z');
            """
        )
        self.conn.commit()
        self.assertEqual(rb.snapshot(self.conn, self.config)["unanswered_now"], [])


class TestCli(BridgeCase):
    def run_cli(self, *args):
        return rb.main(["--db", self.db_path, "--config", str(self.dir / "none.md"), *args])

    def test_scan_pending_approve_apply(self):
        self.add_message("เดินสายไฟชั้น 3 เฉวงเสร็จแล้ว")
        self.assertEqual(self.run_cli("scan"), 0)
        self.assertEqual(self.run_cli("approve"), 0)

        os.environ["RENO_DASH"] = str(self.dir)
        self.assertEqual(self.run_cli("apply", "--dashboard-dir", str(self.dir)), 0)
        html = (self.dir / "dashboard-final.html").read_text(encoding="utf-8")
        self.assertIn("เดินสายไฟชั้น 3", html)

    def test_export_writes_importable_json(self):
        self.add_message("ขอเบิกค่าแรงเฉวง 15,000 บาท")
        self.run_cli("scan")
        out = self.dir / "reno-import.json"
        self.run_cli("export", "--out", str(out))

        payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertTrue(payload["payments"])
        self.assertEqual(payload["payments"][0]["amount"], 15000)
        self.assertIn("summary", payload)


if __name__ == "__main__":
    unittest.main()
