"""
Reno bridge — ต่อ LINE webhook เข้ากับ Reno Dashboard และสกิล reno-*

`line_webhook.py` รับข้อความจาก LINE แล้วเก็บลง `chat_messages` (Life OS schema)
ส่วนสกิล reno-* ทำงานกับ array ในไฟล์ HTML ของ Dashboard J
(`T0`/`P0` ใน dashboard-final.html และ `SAMPLE_ITEMS` ใน inventory-system.html)
ไฟล์นี้คือสะพานระหว่างสองฝั่งนั้น

    LINE ─► chat_messages ─► reno_inbox (รอยืนยัน) ─► ยืนยัน ─► tasks + ไฟล์ HTML
                                    │
                                    └─► JSON รูปแบบเดียวกับที่ Dashboard import อยู่แล้ว

ทำไมต้องมีคิว `reno_inbox` คั่นกลาง
    ตัวแยกข้อความเป็นแค่กฎ ไม่ใช่ความจริง เดาผิดได้ตลอด การเขียนตรงเข้า dashboard
    จะทำให้ตัวเลขงบและงานเพี้ยนโดยไม่มีใครรู้ คิวนี้ให้เจ้าของกดยืนยันก่อน
    และทำให้ข้อความเดิมถูก import ซ้ำไม่ได้ (UNIQUE ต่อ message + fingerprint)

ทำไมต้องผูกกลับเข้า `tasks`
    งานที่เข้ามาทาง LINE ต้องปรากฏใน view คอขวด (v_task_cycle, v_blocked_now)
    ไม่งั้น "รอพี่ปอตอบ" จะนับเวลารอไม่ได้ เพราะงานไปอยู่แต่ใน HTML

CLI สำหรับสกิล (ดู RENO_BRIDGE.md ว่าสกิลไหนเรียกอันไหน)

    python reno_bridge.py scan                 # แยกข้อความใหม่เข้าคิว
    python reno_bridge.py pending --json       # คิวที่รอยืนยัน (รูปแบบ import ของ dashboard)
    python reno_bridge.py approve --ids 1,2    # ยืนยัน
    python reno_bridge.py skip --ids 3
    python reno_bridge.py apply                # เขียนเข้าไฟล์ HTML + tasks
    python reno_bridge.py export --out reno-import.json
    python reno_bridge.py status --json        # ภาพรวมต่อไซต์ + งานที่ติด + คำถามค้าง
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DATABASE_PATH", os.path.join(os.getenv("DATA_DIR", "data"), "assistant.db"))
CONFIG_PATH = os.getenv("RENO_CONFIG", "CONFIG.md")

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG — ค่าจาก CONFIG.md ที่ reno-setup สร้าง (รูปแบบตาม CONNECTORS.md)
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_CONFIG: Dict[str, Any] = {
    "site_a": "Lipa",
    "site_b": "Chaweng",
    "contractor": "MR.HOME KOH SAMUI",
    "currency": "THB",
    "currency_symbol": "฿",
    "language": "Thai",
    "dashboard_path": ".",
}

# คำที่ใช้ตัดสินว่าข้อความพูดถึงไซต์ไหน — ต่อท้ายได้ใน CONFIG.md ด้วย ~~site-a-aliases
DEFAULT_ALIASES: Dict[str, List[str]] = {
    "site_a": ["ลิปะน้อย", "ลิปะ", "ลิปะน้อย", "บ้าน", "lipa", "lipa noi"],
    "site_b": ["เฉวง", "ตึก", "3 ชั้น", "สามชั้น", "ชั้น 2", "ชั้น 3", "chaweng"],
}

_PLACEHOLDER_KEYS = {
    "site-a": "site_a",
    "site-b": "site_b",
    "contractor": "contractor",
    "currency": "currency",
    "currency-symbol": "currency_symbol",
    "language": "language",
    "dashboard-path": "dashboard_path",
    "site-a-aliases": "site_a_aliases",
    "site-b-aliases": "site_b_aliases",
}

# รับทั้ง `| ~~site-a | Baan Suan |` และ `- ~~site-a: Baan Suan` (backtick มีหรือไม่มีก็ได้)
_CONFIG_LINE_RE = re.compile(r"~~([a-z-]+)`?\s*[|:]\s*([^|\n]*)")


def load_config(path: str = CONFIG_PATH) -> Dict[str, Any]:
    """อ่าน CONFIG.md แบบทนรูปแบบ — รับทั้งตาราง markdown และ `- ~~key: value`

    ไม่มีไฟล์ก็ใช้ค่าเริ่มต้น (สองไซต์ Lipa/Chaweng) เพื่อให้ webhook รันได้เลย
    """
    config = dict(DEFAULT_CONFIG)
    config["site_a_aliases"] = list(DEFAULT_ALIASES["site_a"])
    config["site_b_aliases"] = list(DEFAULT_ALIASES["site_b"])

    file_path = Path(path)
    if not file_path.is_file():
        return config

    for line in file_path.read_text(encoding="utf-8").splitlines():
        match = _CONFIG_LINE_RE.search(line)
        if not match:
            continue
        key = _PLACEHOLDER_KEYS.get(match.group(1))
        if not key:
            continue
        value = match.group(2).strip().strip("`\"'").strip()
        if value in ("", "—", "-"):
            # ช่องว่างของ ~~site-b คือการบอกว่า "มีไซต์เดียว" ไม่ใช่ "ยังไม่ได้ตั้ง"
            if key == "site_b":
                config["site_b"] = ""
            continue
        if key.endswith("_aliases"):
            config[key] = [part.strip() for part in value.split(",") if part.strip()]
        else:
            config[key] = value

    # ชื่อไซต์ที่ผู้ใช้ตั้งเอง ต้องนับเป็น alias ของตัวเองด้วย
    for slot in ("site_a", "site_b"):
        name = config.get(slot)
        if name:
            aliases = config.setdefault(f"{slot}_aliases", [])
            if name.lower() not in [a.lower() for a in aliases]:
                aliases.append(name)
    return config


def single_site(config: Dict[str, Any]) -> bool:
    """โหมดไซต์เดียวตาม CONNECTORS.md — ปล่อย ~~site-b ว่างไว้"""
    return not config.get("site_b")


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEMA — ตารางของ bridge เท่านั้น ไม่แตะโครงวิเคราะห์
# ═══════════════════════════════════════════════════════════════════════════════

SCHEMA = """
CREATE TABLE IF NOT EXISTS reno_inbox (
  id              INTEGER PRIMARY KEY,
  chat_message_id INTEGER NOT NULL REFERENCES chat_messages(id),
  kind            TEXT NOT NULL,          -- task | payment | stock
  site            TEXT,                   -- ชื่อไซต์ หรือ NULL เมื่อเดาไม่ได้
  payload         TEXT NOT NULL,          -- JSON รูปแบบเดียวกับที่ dashboard import
  fingerprint     TEXT NOT NULL,
  confidence      REAL NOT NULL,
  status          TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|applied|skipped
  task_id         INTEGER REFERENCES tasks(id),
  dashboard_ref   TEXT,
  created_at      TEXT NOT NULL,
  decided_at      TEXT,
  applied_at      TEXT
);

-- ข้อความเดิมต้องไม่กลายเป็นรายการซ้ำ ไม่ว่าจะ scan กี่รอบ
CREATE UNIQUE INDEX IF NOT EXISTS ux_reno_inbox_fp
  ON reno_inbox(chat_message_id, kind, fingerprint);

CREATE INDEX IF NOT EXISTS ix_reno_inbox_status ON reno_inbox(status);

CREATE TABLE IF NOT EXISTS reno_state (
  key   TEXT PRIMARY KEY,
  value TEXT
);
"""


def init_bridge_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _thai_date(iso_or_text: Optional[str]) -> str:
    """วันที่แบบที่ P0 ใช้ (DD/MM/YY พ.ศ. สองหลัก) เช่น 10/06/69"""
    if not iso_or_text:
        return ""
    try:
        stamp = datetime.strptime(iso_or_text[:10], "%Y-%m-%d")
    except ValueError:
        return iso_or_text
    return f"{stamp.day:02d}/{stamp.month:02d}/{(stamp.year + 543) % 100:02d}"


# ═══════════════════════════════════════════════════════════════════════════════
# ตัวแยกข้อความ (กฎล้วน — ทำงานในจังหวะเบื้องหลังของ webhook)
# ═══════════════════════════════════════════════════════════════════════════════

AMOUNT_RE = re.compile(r"(?:฿|บาท\s*)?([0-9][0-9,]{2,})(?:\.\-|\s*บาท|\s*฿)?")
# ไม่รวม "ชั้น" — "ตึก 3 ชั้น" คือจำนวนชั้นของอาคาร ไม่ใช่จำนวนของในคลัง
QTY_RE = re.compile(r"(\d+)\s*(ชิ้น|ดวง|ม้วน|ชุด|กล่อง|ตัว|แผ่น|ถุง|อัน|หลอด|เมตร|ท่อน)")
ORDER_RE = re.compile(r"(?:HomePro|Global House|โฮมโปร|โกลบอล)[^\n]{0,40}?#?\s*([A-Z0-9-]{6,})", re.IGNORECASE)

PAY_RE = re.compile(r"(เบิก|โอน|จ่าย|ค่าแรง|ค่าวัสดุ|บิล|ใบกำกับ|invoice|มัดจำ|ยอด)", re.IGNORECASE)
PAID_RE = re.compile(r"(โอนแล้ว|จ่ายแล้ว|โอนให้แล้ว|ชำระแล้ว|ได้รับเงินแล้ว|transferred|paid)", re.IGNORECASE)
# คู่กับ QTY_RE เสมอ — ลำพังคำว่า "เบิก" ยังไม่พอ ต้องมีจำนวน+หน่วยของจริงด้วย
STOCK_RE = re.compile(
    r"(ซื้อ|รับของ|ของเข้า|เบิก|ใช้|สต็อก|สต๊อก|สั่งของ|ของถึง|จัดส่ง|HomePro|Global House)",
    re.IGNORECASE,
)
# ของออกจากคลัง ต่างจากของเข้า — สกิล reno-stock-move ใช้ค่านี้เลือก in/out
STOCK_OUT_RE = re.compile(r"(เบิกของ|เบิกออก|ตัดสต็อก|ใช้ไป|เอาไปใช้|เบิก[^\n]{0,30}?ไป)", re.IGNORECASE)
# เฉพาะคำที่เป็น "การลงมือทำ" จริง ๆ — คำกว้างอย่าง งาน/ทำ ใช้ไม่ได้
# เพราะ "ค่าแรงงาน" ในข้อความเบิกเงินจะกลายเป็นการ์ดงานผีทุกครั้ง
TASK_RE = re.compile(
    r"(รื้อ|ทุบ|เลาะ|ก่อ|ฉาบ|ทาสี|ปูกระเบื้อง|ปูพื้น|ติดตั้ง|ซ่อม|เดินสาย|เดินท่อ|วัดขนาด|วัด"
    r"|ตัด|เท(?:ปรับ|พื้น)?|เก็บงาน|เปลี่ยน|เจาะ|กั้นห้อง|ต่อท่อ)",
    re.IGNORECASE,
)

TYPE_RULES: Sequence[Tuple[str, re.Pattern]] = (
    ("Electrical", re.compile(r"(ไฟฟ้า|สายไฟ|ปลั๊ก|สวิตช์|ดาวน์ไลท์|โคมไฟ|มิเตอร์|คอนซูมเมอร์|หลอด)")),
    ("Plumbing", re.compile(r"(ประปา|ท่อ|สุขภัณฑ์|ฝักบัว|ชักโครก|อ่างล้าง|วาล์ว|สายชำระ|ซิงค์)")),
    ("Demo", re.compile(r"(รื้อ|ทุบ|เลาะ|ถอด)")),
    ("Structure", re.compile(r"(ก่อ|เสา|โครงเหล็ก|หลังคา|คาน|ผนัง|กั้นห้อง|โครงสร้าง)")),
    ("Finishing", re.compile(r"(ฉาบ|ทาสี|กระเบื้อง|ฝ้า|เคาน์เตอร์|ประตู|บัว|ปูพื้น)")),
)

DONE_RE = re.compile(r"(เสร็จ|เรียบร้อย|✅|จบแล้ว|ปิดงาน|done)", re.IGNORECASE)
DOING_RE = re.compile(r"(กำลัง|เริ่มแล้ว|ลงมือ|ทำอยู่|ระหว่าง)", re.IGNORECASE)
REVIEW_RE = re.compile(r"(รอ(ยืนยัน|ตัดสินใจ|อนุมัติ|ตรวจ|เลือก)|ขอความเห็น|ช่วยดู|ถามว่า)", re.IGNORECASE)
HIGH_RE = re.compile(r"(ด่วน|เร่ง|วันนี้|รีบ|urgent)", re.IGNORECASE)
LOW_RE = re.compile(r"(ไม่รีบ|ไว้ก่อน|เดี๋ยวค่อย|เมื่อไหร่ก็ได้)", re.IGNORECASE)

CAT_RULES: Sequence[Tuple[str, str, re.Pattern]] = (
    ("โคมไฟ", "💡", re.compile(r"(โคม|ดาวน์ไลท์|หลอด|ไฟกิ่ง)")),
    ("ไฟฟ้า", "🔌", re.compile(r"(สายไฟ|ปลั๊ก|สวิตช์|เต้ารับ|มิเตอร์|เบรกเกอร์)")),
    ("ห้องน้ำ", "🚿", re.compile(r"(สุขภัณฑ์|ฝักบัว|ชักโครก|อ่างล้าง|สายชำระ)")),
    ("เครื่องมือ", "🔧", re.compile(r"(สว่าน|ดอกสว่าน|เครื่องมือ|บันได|คีม)")),
    ("เฟอร์นิเจอร์", "🪑", re.compile(r"(เคาน์เตอร์|ตู้|โต๊ะ|เตียง|เก้าอี้)")),
    ("ชุดเครื่องนอน", "🛏", re.compile(r"(ที่นอน|ผ้าปู|หมอน|ผ้าห่ม)")),
)


def _amount(text: str) -> int:
    """ยอดเงินที่มากที่สุดในข้อความ — บิลมักมีทั้งยอดย่อยและยอดรวม เอายอดรวม"""
    values = []
    for raw in AMOUNT_RE.findall(text):
        try:
            value = int(raw.replace(",", ""))
        except ValueError:
            continue
        if value >= 100:  # ต่ำกว่านี้มักเป็นเลขห้อง/จำนวนชิ้น ไม่ใช่เงิน
            values.append(value)
    return max(values) if values else 0


def detect_site(text: str, config: Dict[str, Any]) -> Optional[str]:
    """หาไซต์จากคำในข้อความ — เจอทั้งสองไซต์ถือว่าไม่ชัด คืน None ให้คนตัดสิน

    กฎเหล็กข้อ 1 ของโปรเจกต์คือห้ามรวม Lipa/Chaweng เข้าด้วยกันเอง
    การเดาผิดแย่กว่าการถาม
    """
    body = (text or "").lower()
    hits = []
    for slot in ("site_a", "site_b"):
        name = config.get(slot)
        if not name:
            continue
        for alias in config.get(f"{slot}_aliases", []):
            if alias.lower() in body:
                hits.append(name)
                break
    unique = list(dict.fromkeys(hits))
    return unique[0] if len(unique) == 1 else None


def detect_type(text: str) -> str:
    for name, pattern in TYPE_RULES:
        if pattern.search(text):
            return name
    return "General"


def detect_status(text: str) -> str:
    if DONE_RE.search(text):
        return "Done"
    if REVIEW_RE.search(text):
        return "Review"
    if DOING_RE.search(text):
        return "In Progress"
    return "Todo"


def detect_priority(text: str) -> str:
    if HIGH_RE.search(text):
        return "High"
    if LOW_RE.search(text):
        return "Low"
    return "Medium"


def detect_category(text: str) -> Tuple[str, str]:
    for name, icon, pattern in CAT_RULES:
        if pattern.search(text):
            return name, icon
    return "อื่นๆ", "📦"


def extract_items(
    text: Optional[str],
    *,
    sent_at: str,
    config: Dict[str, Any],
    sender: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """แปลงข้อความ LINE หนึ่งข้อความเป็นรายการ task/payment/stock

    payload ของแต่ละรายการใช้รูปแบบเดียวกับที่ dashboard import อยู่แล้ว
    (ดู showPreview/applyAll ใน dashboard-final.html) จะได้ไม่ต้องแปลงสองรอบ
    """
    body = (text or "").strip()
    if len(body) < 4:
        return []

    site = detect_site(body, config)
    date_iso = sent_at[:10]
    items: List[Dict[str, Any]] = []
    amount = _amount(body)

    # ── เงิน ──────────────────────────────────────────────────────────────────
    if PAY_RE.search(body) and amount > 0:
        order = ORDER_RE.search(body)
        items.append(
            {
                "kind": "payment",
                "site": site,
                # ยังไม่รู้ไซต์ ให้ความมั่นใจต่ำลง คนจะได้เห็นว่าต้องเลือกเอง
                "confidence": 0.85 if site else 0.5,
                "payload": {
                    "date": _thai_date(date_iso),
                    "desc": body[:180] + (f" | {order.group(1)}" if order else ""),
                    "project": site or "",
                    "who": sender or config["contractor"],
                    "amount": amount,
                    "status": "paid" if PAID_RE.search(body) else "pending",
                },
            }
        )

    # ── สต็อก ─────────────────────────────────────────────────────────────────
    qty_match = QTY_RE.search(body)
    if STOCK_RE.search(body) and qty_match:
        category, icon = detect_category(body)
        quantity = int(qty_match.group(1))
        items.append(
            {
                "kind": "stock",
                "site": site,
                "confidence": 0.6 if site else 0.4,
                "payload": {
                    "sku": "",
                    "name": body[:80],
                    "cat": category,
                    "icon": icon,
                    "proj": site or "",
                    "qty": quantity,
                    "minQty": 1,
                    "unit": qty_match.group(2),
                    "price": amount // quantity if amount and quantity else 0,
                    "loc": "",
                    "note": f"จาก LINE {date_iso}",
                    # ของเข้าคลังเป็นค่าเริ่มต้น เว้นแต่ข้อความบอกว่าเบิกออกไปใช้
                    "operation": "out" if STOCK_OUT_RE.search(body) else "in",
                },
            }
        )

    # ── งาน ───────────────────────────────────────────────────────────────────
    # ข้อความเรื่องเงินล้วน ๆ ไม่มีคำว่าลงมือทำ จึงไม่กลายเป็นการ์ดงาน
    if TASK_RE.search(body):
        status = detect_status(body)
        items.append(
            {
                "kind": "task",
                "site": site,
                "confidence": 0.7 if site else 0.45,
                "payload": {
                    "title": _task_title(body),
                    "note": body[:300],
                    "status": status,
                    "project": site or "",
                    "type": detect_type(body),
                    "cost": amount if PAY_RE.search(body) else 0,
                    "date": date_iso if status == "Done" else "",
                    "priority": detect_priority(body),
                },
            }
        )

    return items


def _task_title(body: str) -> str:
    """ตัดท่อนเรื่องเงินออกจากชื่องาน

    "เดินสายไฟชั้น 3 เสร็จแล้ว ขอเบิกค่าแรง 15,000" → ชื่องานคือท่อนแรก
    ส่วนยอดเงินไปอยู่ในรายการ payment ของตัวเองแล้ว ไม่ต้องแปะไว้บนหัวการ์ด
    """
    first_line = body.split("\n", 1)[0].strip()
    cut = re.search(r"\s*(ขอเบิก|เบิกค่า|เบิกเงิน|ค่าแรง|ยอด|บิล)", first_line)
    if cut and len(first_line[: cut.start()].strip()) >= 8:
        first_line = first_line[: cut.start()].strip()
    return first_line[:120]


def fingerprint(kind: str, payload: Dict[str, Any]) -> str:
    raw = kind + "|" + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════════════════════════
# คิว
# ═══════════════════════════════════════════════════════════════════════════════

def queue_items(
    conn: sqlite3.Connection,
    chat_message_id: int,
    items: Sequence[Dict[str, Any]],
) -> List[int]:
    """ใส่รายการเข้าคิว — INSERT OR IGNORE กัน scan ซ้ำไม่ให้เกิดรายการซ้ำ"""
    queued: List[int] = []
    with conn:
        for item in items:
            payload = json.dumps(item["payload"], ensure_ascii=False)
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO reno_inbox
                    (chat_message_id, kind, site, payload, fingerprint, confidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_message_id,
                    item["kind"],
                    item.get("site"),
                    payload,
                    fingerprint(item["kind"], item["payload"]),
                    item.get("confidence", 0.5),
                    _now(),
                ),
            )
            if cursor.rowcount == 1:
                queued.append(int(cursor.lastrowid))
    return queued


def scan_message(
    conn: sqlite3.Connection,
    *,
    chat_message_id: int,
    text: Optional[str],
    sent_at: str,
    config: Dict[str, Any],
    sender: Optional[str] = None,
) -> List[int]:
    """เรียกจาก webhook หลังคัดแยก intent เสร็จ (จังหวะ 4)"""
    items = extract_items(text, sent_at=sent_at, config=config, sender=sender)
    if not items:
        return []
    return queue_items(conn, chat_message_id, items)


def scan_backlog(conn: sqlite3.Connection, config: Dict[str, Any]) -> int:
    """แยกข้อความขาเข้าที่ยังไม่เคย scan — ใช้ตอน bridge เพิ่งติดตั้งทีหลัง"""
    row = conn.execute(
        "SELECT value FROM reno_state WHERE key = 'last_scanned_message_id'"
    ).fetchone()
    cursor_id = int(row["value"]) if row else 0

    messages = conn.execute(
        """
        SELECT m.id, m.body, m.sent_at, c.display_name
          FROM chat_messages m
          LEFT JOIN contacts c ON c.id = m.contact_id
         WHERE m.direction = 'in' AND m.id > ?
         ORDER BY m.id
        """,
        (cursor_id,),
    ).fetchall()

    queued = 0
    highest = cursor_id
    for message in messages:
        queued += len(
            scan_message(
                conn,
                chat_message_id=int(message["id"]),
                text=message["body"],
                sent_at=message["sent_at"],
                config=config,
                sender=message["display_name"],
            )
        )
        highest = max(highest, int(message["id"]))

    with conn:
        conn.execute(
            "INSERT INTO reno_state (key, value) VALUES ('last_scanned_message_id', ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (str(highest),),
        )
    return queued


def get_inbox(
    conn: sqlite3.Connection, status: str = "pending", ids: Optional[Sequence[int]] = None
) -> List[Dict[str, Any]]:
    # ดึง sent_at ของข้อความต้นทางมาด้วย — สกิลต้องการวันที่แบบ ISO ส่วน dashboard
    # ต้องการ DD/MM/YY เก็บอันเดียวแล้วแปลงตอนใช้ ดีกว่าเก็บวันที่ซ้ำสองรูปแบบ
    sql = (
        "SELECT r.*, m.sent_at AS message_sent_at "
        "FROM reno_inbox r LEFT JOIN chat_messages m ON m.id = r.chat_message_id WHERE 1=1"
    )
    params: List[Any] = []
    if status != "all":
        sql += " AND r.status = ?"
        params.append(status)
    if ids:
        sql += f" AND r.id IN ({','.join('?' for _ in ids)})"
        params.extend(ids)
    sql += " ORDER BY r.id"

    rows = []
    for row in conn.execute(sql, params):
        item = dict(row)
        item["payload"] = json.loads(item["payload"])
        rows.append(item)
    return rows


def set_status(
    conn: sqlite3.Connection, ids: Sequence[int], status: str
) -> int:
    if not ids:
        return 0
    with conn:
        changed = conn.execute(
            f"""
            UPDATE reno_inbox
               SET status = ?, decided_at = ?
             WHERE id IN ({','.join('?' for _ in ids)})
               AND status IN ('pending', 'approved', 'skipped')
            """,
            (status, _now(), *ids),
        ).rowcount
    return changed


def as_import_payload(items: Sequence[Dict[str, Any]], config: Dict[str, Any]) -> Dict[str, Any]:
    """รูปแบบเดียวกับที่ showPreview() ใน dashboard-final.html รับ

    วางไฟล์นี้ในช่อง "📋 วางข้อความ" ได้เลย ไม่ต้องพึ่งการเรียก AI จากเบราว์เซอร์
    """
    tasks = [i["payload"] for i in items if i["kind"] == "task"]
    payments = [i["payload"] for i in items if i["kind"] == "payment"]
    stock = [i["payload"] for i in items if i["kind"] == "stock"]
    symbol = config["currency_symbol"]
    total = sum(p.get("amount", 0) for p in payments)
    summary = (
        f"จาก LINE: {len(tasks)} งาน · {len(payments)} รายการเงิน"
        + (f" รวม {symbol}{total:,}" if total else "")
        + (f" · {len(stock)} รายการสต็อก" if stock else "")
    )
    return {
        "tasks": tasks,
        "payments": payments,
        "stock": stock,
        "summary": summary,
        "ids": [i["id"] for i in items],
    }


def as_skill_payload(items: Sequence[Dict[str, Any]], config: Dict[str, Any]) -> Dict[str, Any]:
    """รูปแบบตาม skills/reno-ingest-chat/references/schemas.md

    ต่างจากรูปแบบของ dashboard สองจุด: payment ใช้ vendor/note และวันที่เป็น ISO
    ส่วน stock มี operation/category/location สกิลปลายทางจึงรับไปใช้ต่อได้ทันที
    """
    tasks, payments, stock = [], [], []
    for item in items:
        payload = item["payload"]
        iso = (item.get("message_sent_at") or item["created_at"])[:10]
        if item["kind"] == "task":
            tasks.append(payload)
        elif item["kind"] == "payment":
            payments.append(
                {
                    "vendor": payload.get("who") or config["contractor"],
                    "amount": payload.get("amount", 0),
                    "status": payload.get("status", "pending"),
                    "project": payload.get("project") or None,
                    "date": iso,
                    "note": payload.get("desc", ""),
                }
            )
        else:
            stock.append(
                {
                    "name": payload.get("name", ""),
                    "sku": payload.get("sku", ""),
                    "operation": payload.get("operation", "in"),
                    "qty": payload.get("qty", 0),
                    "unit": payload.get("unit", ""),
                    "project": payload.get("proj") or None,
                    "price": payload.get("price", 0),
                    "category": payload.get("cat", "อื่นๆ"),
                    "location": payload.get("loc", ""),
                }
            )

    unassigned = [
        i["id"] for i in items if not (i["payload"].get("project") or i["payload"].get("proj"))
    ]
    return {
        "summary": {
            "tasks": len(tasks),
            "payments": len(payments),
            "stock_items": len(stock),
            "status_updates": 0,
            "unclassified": len(unassigned),
        },
        "tasks": tasks,
        "payments": payments,
        "stock": stock,
        # bridge ไม่เดาว่าข้อความไหนเป็นการอัปเดตงานเดิม — ปล่อยให้สกิลจับคู่เอง
        "status_updates": [],
        "unclassified": [],
        "inbox_ids": [i["id"] for i in items],
        "needs_site": unassigned,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# เขียนเข้าไฟล์ HTML ของ dashboard
# ═══════════════════════════════════════════════════════════════════════════════

class ArrayNotFound(RuntimeError):
    pass


def _find_array_span(source: str, name: str) -> Tuple[int, int]:
    """หาช่วงของ `const NAME = [ ... ]` โดยนับวงเล็บและข้ามข้อความในเครื่องหมายคำพูด"""
    match = re.search(rf"const\s+{re.escape(name)}\s*=\s*\[", source)
    if not match:
        raise ArrayNotFound(f"ไม่พบ array {name}")

    index = match.end() - 1  # ตำแหน่งของ [
    depth = 0
    quote: Optional[str] = None
    escaped = False
    while index < len(source):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "\"'`":
            quote = char
        elif char in "[{(":
            depth += 1
        elif char in "]})":
            depth -= 1
            if depth == 0:
                return match.start(), index
        index += 1
    raise ArrayNotFound(f"array {name} ปิดไม่ครบ")


def _js_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ")
    return f"'{text}'"


def _js_object(fields: Dict[str, Any]) -> str:
    return "{" + ",".join(f"{key}:{_js_value(value)}" for key, value in fields.items()) + "}"


def _next_numeric_id(body: str) -> int:
    ids = [int(match) for match in re.findall(r"\bid:(\d+)", body)]
    return (max(ids) + 1) if ids else 1


def append_to_array(source: str, name: str, rows: Sequence[Dict[str, Any]], marker: str) -> Tuple[str, List[str]]:
    """แทรกแถวใหม่ก่อนวงเล็บปิดของ array พร้อมคอมเมนต์บอกที่มา"""
    if not rows:
        return source, []

    start, close = _find_array_span(source, name)
    body = source[start:close]
    next_id = _next_numeric_id(body)

    lines = [f"\n  // ── {marker} ──"]
    refs: List[str] = []
    for offset, row in enumerate(rows):
        fields = dict(row)
        if isinstance(fields.get("id"), str):
            refs.append(fields["id"])
        else:
            fields["id"] = next_id + offset
            refs.append(str(fields["id"]))
        ordered = {"id": fields.pop("id"), **fields}
        lines.append("  " + _js_object(ordered) + ",")

    insertion = "\n".join(lines) + "\n"
    return source[:close] + insertion + source[close:], refs


def _task_row(payload: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "t": payload.get("title", ""),
        "n": payload.get("note", ""),
        "s": payload.get("status") or "Todo",
        "p": payload.get("project") or config["site_a"],
        "type": payload.get("type") or "General",
        "cost": payload.get("cost") or 0,
        "date": payload.get("date") or "",
        "pri": payload.get("priority") or "Medium",
        "by": "MR",
    }


def _payment_row(payload: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "date": payload.get("date") or "",
        "desc": payload.get("desc", ""),
        "proj": payload.get("project") or config["site_a"],
        "who": payload.get("who") or config["contractor"],
        "amount": payload.get("amount") or 0,
        "status": payload.get("status") or "pending",
    }


def _stock_row(payload: Dict[str, Any], config: Dict[str, Any], sequence: int) -> Dict[str, Any]:
    return {
        "id": f"ITM-LINE{sequence:03d}",
        "sku": payload.get("sku") or "",
        "name": payload.get("name", ""),
        "cat": payload.get("cat") or "อื่นๆ",
        "icon": payload.get("icon") or "📦",
        "proj": payload.get("proj") or config["site_a"],
        "qty": payload.get("qty") or 0,
        "minQty": payload.get("minQty") or 1,
        "unit": payload.get("unit") or "ชิ้น",
        "price": payload.get("price") or 0,
        "loc": payload.get("loc") or "",
        "note": payload.get("note") or "",
    }


def apply_items(
    conn: sqlite3.Connection,
    items: Sequence[Dict[str, Any]],
    config: Dict[str, Any],
    *,
    dashboard_dir: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """เขียนรายการที่ยืนยันแล้วเข้าไฟล์ HTML และสร้าง tasks ใน Life OS

    งานทุกใบที่เขียนลง dashboard จะมีแถวใน `tasks` คู่กันเสมอ (source='line',
    source_ref = chat_messages.id) เพื่อให้ view คอขวดมองเห็น ไม่งั้นงานที่มาจาก
    LINE จะหายไปจากรายงานทั้งหมด
    """
    unresolved = [i for i in items if not (i["payload"].get("project") or i["payload"].get("proj"))]
    if unresolved and not single_site(config):
        raise ValueError(
            "มี %d รายการที่ยังไม่ระบุไซต์ — ตั้ง project ก่อน apply "
            "(กฎเหล็กข้อ 1: ห้ามรวมสองไซต์เข้าด้วยกันเอง)" % len(unresolved)
        )

    directory = Path(dashboard_dir or config["dashboard_path"])
    dashboard_file = directory / "dashboard-final.html"
    inventory_file = directory / "inventory-system.html"

    tasks = [i for i in items if i["kind"] == "task"]
    payments = [i for i in items if i["kind"] == "payment"]
    stock = [i for i in items if i["kind"] == "stock"]

    result: Dict[str, Any] = {
        "tasks": len(tasks),
        "payments": len(payments),
        "stock": len(stock),
        "files": [],
        "dry_run": dry_run,
    }
    marker = f"จาก LINE webhook {_now()[:10]}"

    if tasks or payments:
        if not dashboard_file.is_file():
            raise FileNotFoundError(f"ไม่พบ {dashboard_file} — ตั้ง ~~dashboard-path ใน CONFIG.md")
        source = dashboard_file.read_text(encoding="utf-8")
        source, task_refs = append_to_array(
            source, "T0", [_task_row(i["payload"], config) for i in tasks], marker
        )
        source, pay_refs = append_to_array(
            source, "P0", [_payment_row(i["payload"], config) for i in payments], marker
        )
        if not dry_run:
            shutil.copy2(dashboard_file, dashboard_file.with_suffix(".html.bak"))
            dashboard_file.write_text(source, encoding="utf-8")
        result["files"].append(str(dashboard_file))
    else:
        task_refs, pay_refs = [], []

    if stock:
        if not inventory_file.is_file():
            raise FileNotFoundError(f"ไม่พบ {inventory_file} — ตั้ง ~~dashboard-path ใน CONFIG.md")
        source = inventory_file.read_text(encoding="utf-8")
        rows = [
            _stock_row(item["payload"], config, item["id"]) for item in stock
        ]
        source, stock_refs = append_to_array(source, "SAMPLE_ITEMS", rows, marker)
        if not dry_run:
            shutil.copy2(inventory_file, inventory_file.with_suffix(".html.bak"))
            inventory_file.write_text(source, encoding="utf-8")
        result["files"].append(str(inventory_file))
    else:
        stock_refs = []

    if dry_run:
        return result

    stamp = _now()
    with conn:
        for item, ref in zip(tasks, task_refs):
            payload = item["payload"]
            cursor = conn.execute(
                """
                INSERT INTO tasks (title, note, status, created_at, completed_at, source, source_ref)
                VALUES (?, ?, ?, ?, ?, 'line', ?)
                """,
                (
                    payload.get("title", "")[:200],
                    payload.get("note"),
                    "done" if payload.get("status") == "Done" else "inbox",
                    stamp,
                    stamp if payload.get("status") == "Done" else None,
                    str(item["chat_message_id"]),
                ),
            )
            task_id = int(cursor.lastrowid)
            conn.execute(
                "INSERT INTO task_events (task_id, from_status, to_status, at) VALUES (?, NULL, ?, ?)",
                (task_id, "done" if payload.get("status") == "Done" else "inbox", stamp),
            )
            conn.execute(
                "UPDATE chat_messages SET linked_task_id = ? WHERE id = ? AND linked_task_id IS NULL",
                (task_id, item["chat_message_id"]),
            )
            conn.execute(
                "UPDATE reno_inbox SET status='applied', applied_at=?, task_id=?, dashboard_ref=? WHERE id=?",
                (stamp, task_id, ref, item["id"]),
            )
        for item, ref in zip(payments + stock, pay_refs + stock_refs):
            conn.execute(
                "UPDATE reno_inbox SET status='applied', applied_at=?, dashboard_ref=? WHERE id=?",
                (stamp, ref, item["id"]),
            )
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# ภาพรวมสำหรับ reno-status / reno-weekly-brief
# ═══════════════════════════════════════════════════════════════════════════════

def snapshot(conn: sqlite3.Connection, config: Dict[str, Any]) -> Dict[str, Any]:
    """สิ่งที่มีแต่ฝั่ง webhook เท่านั้นที่รู้ — งานที่ติดใคร และคำถามที่ยังไม่ตอบ

    สกิล reno-status อ่าน Kanban กับการเงินจากไฟล์ HTML อยู่แล้ว ตรงนี้จึงเติม
    เฉพาะส่วนที่ไฟล์ HTML ไม่มี ไม่ไปนับตัวเลขซ้ำกับมัน
    """
    pending = get_inbox(conn, "pending")
    approved = get_inbox(conn, "approved")

    blocked = [
        dict(row)
        for row in conn.execute(
            """
            SELECT t.id AS task_id, t.title, t.blocked_reason AS reason,
                   COALESCE(c.display_name, '(ไม่ได้ติดที่คน)') AS person,
                   b.blocked_at,
                   ROUND((julianday('now') - julianday(b.blocked_at)) * 24.0, 1) AS waiting_hours
              FROM task_blocks b
              JOIN tasks t ON t.id = b.task_id
              LEFT JOIN contacts c ON c.id = b.contact_id
             WHERE b.unblocked_at IS NULL
             ORDER BY b.blocked_at
            """
        )
    ]

    unanswered = [
        dict(row)
        for row in conn.execute(
            """
            SELECT m.id AS message_id,
                   COALESCE(c.display_name, '(ไม่ทราบผู้ส่ง)') AS person,
                   substr(m.body, 1, 80) AS preview,
                   m.sent_at,
                   ROUND((julianday('now') - julianday(m.sent_at)) * 24.0, 1) AS waiting_hours
              FROM chat_messages m
              LEFT JOIN contacts c ON c.id = m.contact_id
             WHERE m.direction = 'in'
               AND m.responded_at IS NULL
               AND m.intent IN ('request', 'question')
               AND COALESCE(m.confidence, 1.0) >= 0.80
               AND NOT EXISTS (SELECT 1 FROM chat_messages o
                                WHERE o.thread_id = m.thread_id
                                  AND o.direction = 'out'
                                  AND o.sent_at > m.sent_at)
             ORDER BY m.sent_at
            """
        )
    ]

    sites: Dict[str, Any] = {}
    for slot in ("site_a", "site_b"):
        name = config.get(slot)
        if not name:
            continue
        sites[name] = {
            "inbox_pending": sum(1 for i in pending if i["site"] == name),
            "blocked": sum(1 for b in blocked if name.lower() in (b["title"] or "").lower()),
        }

    return {
        "sites": sites,
        "inbox_pending": len(pending),
        "inbox_approved": len(approved),
        "inbox_unassigned_site": sum(1 for i in pending if not i["site"]),
        "blocked_now": blocked,
        "unanswered_now": unanswered,
        "generated_at": _now(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ตัวต่อกับ webhook
# ═══════════════════════════════════════════════════════════════════════════════

class RenoBridge:
    """ตัวที่ line_webhook.LineWebhookHandler เรียกในจังหวะเบื้องหลัง"""

    def __init__(self, config: Optional[Dict[str, Any]] = None, *, auto_approve: bool = False):
        self.config = config or load_config()
        # เดาผิดแล้วเขียนทับงบจริงคือความเสียหาย ค่าเริ่มต้นจึงให้คนกดยืนยัน
        self.auto_approve = auto_approve

    def ensure_schema(self, conn: sqlite3.Connection) -> None:
        init_bridge_tables(conn)

    def on_message(
        self,
        conn: sqlite3.Connection,
        *,
        chat_message_id: int,
        text: Optional[str],
        sent_at: str,
        sender: Optional[str] = None,
    ) -> Optional[str]:
        """คืนข้อความสรุปให้ webhook เอาไปตอบ หรือ None ถ้าไม่มีอะไรเข้าคิว"""
        queued = scan_message(
            conn,
            chat_message_id=chat_message_id,
            text=text,
            sent_at=sent_at,
            config=self.config,
            sender=sender,
        )
        if not queued:
            return None
        if self.auto_approve:
            set_status(conn, queued, "approved")
        return self.summarize(get_inbox(conn, "all", queued))

    def summarize(self, items: Sequence[Dict[str, Any]]) -> str:
        symbol = self.config["currency_symbol"]
        lines = [f"📥 จับได้ {len(items)} รายการจากข้อความนี้"]
        for item in items:
            payload = item["payload"]
            site = item["site"] or "❓ยังไม่รู้ไซต์"
            if item["kind"] == "payment":
                lines.append(
                    f"• #{item['id']} 💰 {symbol}{payload['amount']:,} "
                    f"({'จ่ายแล้ว' if payload['status'] == 'paid' else 'รอจ่าย'}) · {site}"
                )
            elif item["kind"] == "stock":
                lines.append(f"• #{item['id']} 📦 {payload['name'][:40]} ×{payload['qty']} · {site}")
            else:
                lines.append(f"• #{item['id']} 🔧 {payload['title'][:40]} [{payload['status']}] · {site}")
        if any(not item["site"] for item in items) and not single_site(self.config):
            lines.append(
                f"⚠️ ระบุไซต์ด้วย: `reno site #<id> {self.config['site_a']}"
                f"|{self.config['site_b']}`"
            )
        lines.append("ยืนยัน: `reno ok` · ทิ้ง: `reno skip #<id>`")
        return "\n".join(lines)

    # ── คำสั่งในแชทของเจ้าของ ────────────────────────────────────────────────
    def handle_command(self, conn: sqlite3.Connection, text: Optional[str]) -> Optional[str]:
        body = (text or "").strip()
        match = re.match(r"^reno\s+(ok|skip|site|list)\b(.*)$", body, re.IGNORECASE | re.DOTALL)
        if not match:
            return None
        action = match.group(1).lower()
        rest = match.group(2).strip()
        ids = [int(value) for value in re.findall(r"#(\d+)", rest)]

        if action == "list":
            pending = get_inbox(conn, "pending")
            return self.summarize(pending) if pending else "📭 คิวว่าง"

        if action == "ok":
            targets = ids or [item["id"] for item in get_inbox(conn, "pending")]
            if not targets:
                return "📭 ไม่มีรายการรอยืนยัน"
            unknown = [
                item["id"]
                for item in get_inbox(conn, "pending", targets)
                if not item["site"] and not single_site(self.config)
            ]
            if unknown:
                return "⚠️ ยังไม่ได้ระบุไซต์: " + ", ".join(f"#{i}" for i in unknown)
            return f"✅ ยืนยัน {set_status(conn, targets, 'approved')} รายการ"

        if action == "skip":
            targets = ids or [item["id"] for item in get_inbox(conn, "pending")]
            return f"🗑 ทิ้ง {set_status(conn, targets, 'skipped')} รายการ"

        if action == "site":
            site = self._match_site(rest)
            if not site or not ids:
                return f"ใช้แบบนี้: `reno site #12 {self.config['site_a']}`"
            with conn:
                for item_id in ids:
                    row = conn.execute(
                        "SELECT payload, kind FROM reno_inbox WHERE id = ?", (item_id,)
                    ).fetchone()
                    if not row:
                        continue
                    payload = json.loads(row["payload"])
                    payload["proj" if row["kind"] == "stock" else "project"] = site
                    conn.execute(
                        "UPDATE reno_inbox SET site = ?, payload = ? WHERE id = ?",
                        (site, json.dumps(payload, ensure_ascii=False), item_id),
                    )
            return f"📍 ตั้งไซต์ {site} ให้ " + ", ".join(f"#{i}" for i in ids)
        return None

    def _match_site(self, text: str) -> Optional[str]:
        return detect_site(text, self.config)


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_bridge_tables(conn)
    return conn


def _parse_ids(value: Optional[str]) -> List[int]:
    if not value:
        return []
    return [int(part) for part in re.split(r"[,\s]+", value.strip()) if part]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Reno bridge — LINE webhook ↔ Reno Dashboard")
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--config", default=CONFIG_PATH)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("scan", help="แยกข้อความ LINE ที่ยังไม่เคย scan เข้าคิว")

    for name in ("pending", "approved"):
        p = sub.add_parser(name, help=f"รายการสถานะ {name}")
        p.add_argument("--json", action="store_true")
        p.add_argument("--schema", choices=("dashboard", "skills"), default="dashboard")

    for name in ("approve", "skip"):
        p = sub.add_parser(name, help=f"เปลี่ยนสถานะเป็น {name}")
        p.add_argument("--ids", help="เว้นว่าง = ทุกรายการที่รออยู่")

    p = sub.add_parser("apply", help="เขียนรายการที่ยืนยันแล้วเข้าไฟล์ HTML + tasks")
    p.add_argument("--ids")
    p.add_argument("--dashboard-dir")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("export", help="เขียน JSON สำหรับวางในช่อง 'วางข้อความ' ของ dashboard")
    p.add_argument("--out", default="reno-import.json")
    p.add_argument("--status", default="pending")
    p.add_argument("--schema", choices=("dashboard", "skills"), default="dashboard")

    p = sub.add_parser("status", help="ภาพรวม: คิว + งานที่ติด + คำถามค้าง")
    p.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    config = load_config(args.config)
    conn = _connect(args.db)

    try:
        if args.command == "scan":
            print(json.dumps({"queued": scan_backlog(conn, config)}, ensure_ascii=False))

        elif args.command in ("pending", "approved"):
            items = get_inbox(conn, args.command)
            if args.json:
                shape = as_skill_payload if args.schema == "skills" else as_import_payload
                print(json.dumps(shape(items, config), ensure_ascii=False, indent=2))
            else:
                print(RenoBridge(config).summarize(items) if items else "📭 คิวว่าง")

        elif args.command in ("approve", "skip"):
            ids = _parse_ids(args.ids) or [i["id"] for i in get_inbox(conn, "pending")]
            status = "approved" if args.command == "approve" else "skipped"
            print(json.dumps({status: set_status(conn, ids, status)}, ensure_ascii=False))

        elif args.command == "apply":
            ids = _parse_ids(args.ids)
            items = get_inbox(conn, "approved", ids or None)
            if not items:
                print(json.dumps({"applied": 0, "reason": "ไม่มีรายการที่ยืนยันแล้ว"}, ensure_ascii=False))
                return 0
            result = apply_items(
                conn, items, config, dashboard_dir=args.dashboard_dir, dry_run=args.dry_run
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if not args.dry_run:
                print(
                    "หมายเหตุ: เบราว์เซอร์ที่เคยเปิด dashboard จะยังเห็นข้อมูลเดิมจาก "
                    "localStorage — ใช้ `export` แล้ววางในช่อง 'วางข้อความ' แทน "
                    "หรือล้าง localStorage",
                    file=sys.stderr,
                )

        elif args.command == "export":
            items = get_inbox(conn, args.status)
            shape = as_skill_payload if args.schema == "skills" else as_import_payload
            payload = shape(items, config)
            Path(args.out).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps({"written": args.out, "items": len(items)}, ensure_ascii=False))

        elif args.command == "status":
            data = snapshot(conn, config)
            if args.json:
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                print(f"📥 คิวรอยืนยัน {data['inbox_pending']} · ยืนยันแล้ว {data['inbox_approved']}")
                for block in data["blocked_now"]:
                    print(f"⏸ #{block['task_id']} {block['title']} — รอ {block['person']} "
                          f"{block['waiting_hours']} ชม.")
                for message in data["unanswered_now"]:
                    print(f"❓ {message['person']}: {message['preview']} ({message['waiting_hours']} ชม.)")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    raise SystemExit(main())
