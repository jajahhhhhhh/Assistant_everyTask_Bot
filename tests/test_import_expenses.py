"""
เทสต์ของ scripts/import_expenses.py

สคริปต์นี้ถูกเรียกเป็น preDeployCommand ข้อกำหนดที่สำคัญที่สุดจึงไม่ใช่เรื่อง
ข้อมูล แต่คือ "ห้ามทำให้ deploy ล้ม" — ทุกทางที่ผิดพลาดต้องจบด้วย exit code 0
"""

import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT))

import import_expenses as ie

SCRIPT = REPO_ROOT / "scripts" / "import_expenses.py"

ROW = {
    "ref": "hetzner:1:aibos",
    "paid_at": "2026-08-03",
    "amount": 40.98,
    "currency": "USD",
    "merchant": "Hetzner Online GmbH",
    "category": "โฮสติ้ง",
    "payment_method": "Pay online",
    "project": "aibos",
    "note": "CPX32 Cloud Server",
    "is_business": 1,
    "is_recurring": 1,
}


class ImporterCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmp.name) / "test.db")
        conn = sqlite3.connect(self.db_path)
        conn.executescript((REPO_ROOT / "sql" / "01_schema.sql").read_text(encoding="utf-8"))
        conn.execute("INSERT INTO projects(id, name, type) VALUES (7, 'NIKSEN', 'niksen')")
        conn.commit()
        conn.close()

    def tearDown(self):
        self._tmp.cleanup()

    def run_import(self, rows, **kwargs):
        conn = sqlite3.connect(self.db_path)
        try:
            with redirect_stdout(io.StringIO()):
                return ie.import_rows(conn, rows, **kwargs)
        finally:
            conn.close()

    def expenses(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM expenses ORDER BY id")]
        finally:
            conn.close()


class TestLoadRows(unittest.TestCase):
    def test_list(self):
        self.assertEqual(len(ie.load_rows(json.dumps([ROW, ROW]))), 2)

    def test_single_object_is_wrapped(self):
        self.assertEqual(len(ie.load_rows(json.dumps(ROW))), 1)

    def test_empty_input(self):
        for raw in (None, "", "   "):
            self.assertEqual(ie.load_rows(raw), [])

    def test_wrong_shape(self):
        with self.assertRaises(ValueError):
            ie.load_rows('"just a string"')

    def test_broken_json_raises(self):
        with self.assertRaises(json.JSONDecodeError):
            ie.load_rows("{not json")


class TestImportRows(ImporterCase):
    def test_inserts_every_field(self):
        self.run_import([ROW])
        row = self.expenses()[0]
        self.assertEqual(row["amount"], 40.98)
        self.assertEqual(row["currency"], "USD")
        self.assertEqual(row["merchant"], "Hetzner Online GmbH")
        self.assertEqual(row["payment_method"], "Pay online")
        self.assertEqual(row["paid_at"], "2026-08-03")
        self.assertEqual(row["is_business"], 1)
        self.assertEqual(row["is_recurring"], 1)
        self.assertEqual(row["verified_by_user"], 1)
        self.assertTrue(row["note"].startswith("[hetzner:1:aibos]"))

    def test_running_twice_changes_nothing(self):
        self.assertEqual(self.run_import([ROW])["inserted"], 1)
        second = self.run_import([ROW])
        self.assertEqual(second, {"inserted": 0, "skipped": 1, "invalid": 0})
        self.assertEqual(len(self.expenses()), 1)

    def test_project_resolved_by_name_or_type(self):
        self.run_import([{**ROW, "ref": "x:1", "project": "niksen"}])
        self.assertEqual(self.expenses()[0]["project_id"], 7)

    def test_unknown_project_stays_null(self):
        self.run_import([{**ROW, "ref": "x:2", "project": "ไม่มีโปรเจกต์นี้"}])
        self.assertIsNone(self.expenses()[0]["project_id"])

    def test_missing_required_field_is_skipped_not_fatal(self):
        summary = self.run_import([{"ref": "x:3", "amount": 10}, ROW])
        self.assertEqual(summary["invalid"], 1)
        self.assertEqual(summary["inserted"], 1)

    def test_amount_that_is_not_a_number(self):
        summary = self.run_import([{**ROW, "ref": "x:4", "amount": "สี่สิบบาท"}])
        self.assertEqual(summary["invalid"], 1)
        self.assertEqual(self.expenses(), [])

    def test_dry_run_writes_nothing(self):
        summary = self.run_import([ROW], dry_run=True)
        self.assertEqual(summary["inserted"], 1)
        self.assertEqual(self.expenses(), [])

    def test_defaults_when_optional_fields_absent(self):
        self.run_import([{"ref": "x:5", "amount": 12.5, "paid_at": "2026-09-01"}])
        row = self.expenses()[0]
        self.assertEqual(row["currency"], "THB")
        self.assertEqual(row["is_business"], 0)
        self.assertIsNone(row["merchant"])


class TestExitCodes(ImporterCase):
    """preDeployCommand ล้ม = deploy ล้ม จึงต้องจบด้วย 0 เสมอ"""

    def run_script(self, env_value=None, extra_args=()):
        import os

        env = dict(os.environ, DATABASE_PATH=self.db_path)
        env.pop(ie.ENV_VAR, None)
        if env_value is not None:
            env[ie.ENV_VAR] = env_value
        return subprocess.run(
            [sys.executable, str(SCRIPT), *extra_args],
            env=env, capture_output=True, text=True,
        )

    def test_no_env_var_is_a_no_op(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0)
        self.assertIn("ไม่มีอะไรให้นำเข้า", result.stdout)
        self.assertEqual(self.expenses(), [])

    def test_broken_json_still_exits_zero(self):
        result = self.run_script("{ไม่ใช่ json")
        self.assertEqual(result.returncode, 0, "ทำให้ deploy ล้มไม่ได้")
        self.assertIn("ล้มเหลว", result.stdout)

    def test_missing_database_still_exits_zero(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--db", "/nonexistent/dir/x.db"],
            env={"EXPENSE_IMPORT_JSON": json.dumps([ROW]), "PATH": "/usr/bin:/bin"},
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)

    def test_happy_path_through_the_cli(self):
        result = self.run_script(json.dumps([ROW]))
        self.assertEqual(result.returncode, 0)
        self.assertIn("เพิ่ม 1", result.stdout)
        self.assertEqual(len(self.expenses()), 1)


if __name__ == "__main__":
    unittest.main()
