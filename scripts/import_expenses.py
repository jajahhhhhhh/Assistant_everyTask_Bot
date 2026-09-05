"""นำรายจ่ายจากบิลเข้าตาราง expenses — รันซ้ำได้ ไม่เกิดแถวซ้ำ

ทำไมต้องมี
    ไม่มีทางอื่นในระบบที่เขียน `expenses` เลย บิลที่จ่ายผ่านบัตรอัตโนมัติ
    (Hetzner, โฮสติ้ง, ค่าบริการรายเดือน) จึงไม่เคยโผล่ในรายงานเงิน
    ทั้งที่ v_spend_* พร้อมอ่านอยู่แล้ว

ข้อมูลมาจากไหน
    รีโปนี้เป็น public — ตัวเลขบิลจึงไม่ถูกเก็บไว้ในโค้ด สคริปต์อ่าน JSON จาก
    ตัวแปรแวดล้อม EXPENSE_IMPORT_JSON (ตั้งไว้ฝั่ง Railway ซึ่งเป็นที่ส่วนตัว)
    หรือจากไฟล์ที่ระบุด้วย --file

    EXPENSE_IMPORT_JSON='[{"ref":"hetzner:088001081598:aibos","paid_at":"2026-08-03",
      "amount":40.98,"currency":"USD","merchant":"Hetzner Online GmbH",
      "category":"โฮสติ้ง","payment_method":"Pay online","project":"aibos",
      "note":"CPX32 Cloud Server 600 ชม. + Primary IPv4"}]'

    ref เป็นกุญแจกันซ้ำ เก็บไว้หน้า note เป็น [ref] — รันกี่รอบก็ได้แถวเดียว

ทำไมไม่เคยคืน exit code ที่ไม่ใช่ 0
    สคริปต์นี้ถูกเรียกเป็น preDeployCommand ถ้ามันล้ม deploy จะล้มตาม
    บิลที่ลงไม่ได้ไม่ควรทำให้บอททั้งตัวขึ้นไม่ได้ — log ไว้แล้วปล่อยผ่าน

ใช้
    python scripts/import_expenses.py              # อ่านจาก EXPENSE_IMPORT_JSON
    python scripts/import_expenses.py --file bills.json
    python scripts/import_expenses.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ENV_VAR = "EXPENSE_IMPORT_JSON"
REQUIRED_FIELDS = ("ref", "amount", "paid_at")


def db_path() -> str:
    """ที่เดียวกับที่ app.py และ line_webhook.py ใช้"""
    return os.getenv("DATABASE_PATH") or os.path.join(os.getenv("DATA_DIR", "data"), "assistant.db")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_rows(raw: Optional[str]) -> List[Dict[str, Any]]:
    """แปลง JSON เป็นลิสต์ของรายจ่าย — รับทั้ง object เดี่ยวและลิสต์"""
    if not raw or not raw.strip():
        return []
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("ต้องเป็น JSON array ของรายจ่าย")
    return data


def resolve_project(conn: sqlite3.Connection, hint: Optional[str]) -> Optional[int]:
    """หา project จากชื่อหรือ type — ไม่เจอก็ปล่อย NULL ดีกว่าผูกผิดโปรเจกต์"""
    if not hint:
        return None
    row = conn.execute(
        "SELECT id FROM projects WHERE lower(name) LIKE ? OR lower(type) LIKE ? LIMIT 1",
        (f"%{hint.lower()}%", f"%{hint.lower()}%"),
    ).fetchone()
    return int(row[0]) if row else None


def already_imported(conn: sqlite3.Connection, ref: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM expenses WHERE note LIKE ?", (f"[{ref}]%",)
    ).fetchone() is not None


def import_rows(
    conn: sqlite3.Connection, rows: List[Dict[str, Any]], *, dry_run: bool = False
) -> Dict[str, int]:
    """คืนสรุปจำนวน — inserted / skipped (มีอยู่แล้ว) / invalid"""
    summary = {"inserted": 0, "skipped": 0, "invalid": 0}
    now = _now()

    for row in rows:
        missing = [f for f in REQUIRED_FIELDS if not row.get(f)]
        if missing:
            print(f"[expenses] ข้าม: ขาดฟิลด์ {', '.join(missing)} — {row}", flush=True)
            summary["invalid"] += 1
            continue

        ref = str(row["ref"])
        if already_imported(conn, ref):
            print(f"[expenses] มีอยู่แล้ว {ref}", flush=True)
            summary["skipped"] += 1
            continue

        try:
            amount = float(row["amount"])
        except (TypeError, ValueError):
            print(f"[expenses] ข้าม {ref}: amount ไม่ใช่ตัวเลข ({row['amount']!r})", flush=True)
            summary["invalid"] += 1
            continue

        project_id = resolve_project(conn, row.get("project"))
        note = f"[{ref}] {row.get('note', '')}".strip()

        if dry_run:
            print(f"[expenses] (dry-run) จะเพิ่ม {ref} {amount} project_id={project_id}", flush=True)
            summary["inserted"] += 1
            continue

        conn.execute(
            """
            INSERT INTO expenses
                (amount, currency, category, note, merchant, payment_method, paid_at, created_at,
                 verified_by_user, project_id, is_business, is_recurring)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                amount,
                row.get("currency", "THB"),
                row.get("category"),
                note,
                row.get("merchant"),
                row.get("payment_method"),
                row["paid_at"],
                now,
                int(row.get("verified", 1)),
                project_id,
                int(row.get("is_business", 0)),
                int(row.get("is_recurring", 0)),
            ),
        )
        summary["inserted"] += 1
        print(
            f"[expenses] เพิ่ม {ref} {row.get('currency', 'THB')} {amount} "
            f"project_id={project_id}",
            flush=True,
        )

    if not dry_run:
        conn.commit()
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="นำรายจ่ายจากบิลเข้าตาราง expenses")
    parser.add_argument("--file", help=f"ไฟล์ JSON (ค่าเริ่มต้นอ่านจาก ${ENV_VAR})")
    parser.add_argument("--db", default=None, help="พาธฐานข้อมูล (ค่าเริ่มต้นตาม DATABASE_PATH)")
    parser.add_argument("--dry-run", action="store_true", help="ดูว่าจะเพิ่มอะไร โดยไม่เขียนจริง")
    args = parser.parse_args(argv)

    raw = Path(args.file).read_text(encoding="utf-8") if args.file else os.getenv(ENV_VAR)
    rows = load_rows(raw)
    if not rows:
        print(f"[expenses] ไม่มีอะไรให้นำเข้า (${ENV_VAR} ว่าง)", flush=True)
        return 0

    path = args.db or db_path()
    print(f"[expenses] db={path} · {len(rows)} รายการ", flush=True)
    conn = sqlite3.connect(path)
    try:
        summary = import_rows(conn, rows, dry_run=args.dry_run)
    finally:
        conn.close()

    print(
        f"[expenses] เพิ่ม {summary['inserted']} · มีอยู่แล้ว {summary['skipped']} "
        f"· ข้าม {summary['invalid']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # preDeployCommand: ถ้าคืนค่าไม่ใช่ 0 deploy จะล้มทั้งรอบ
        print(f"[expenses] ล้มเหลว: {exc}", flush=True)
        sys.exit(0)
