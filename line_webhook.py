"""
LINE Messaging API webhook — ตัวเติม chat_messages และ task_blocks

โครงตารางอยู่ที่ sql/01_schema.sql (Life OS) ไฟล์นี้คือฝั่ง "คนเขียน" ของ view
ทั้งหมดใน sql/02_views.sql เรื่องยากของ webhook LINE ไม่ใช่การ parse payload
แต่คือ "จังหวะ" ของการเขียน เพราะ

  • LINE ต้องการ 2xx เร็ว ๆ ถ้าช้า/พังจะยิงซ้ำด้วย webhookEventId เดิม
  • reply token ใช้ได้ครั้งเดียวและตายใน ~1 นาที
  • v_reply_latency ถือว่า "มีข้อความ out ในเธรดหลังจากนั้น = คุณตอบแล้ว"
  • v_wait_spans นับเวลารอจาก task_blocks.blocked_at → unblocked_at
  • 04_queries.sql ข้อ E3 บังคับว่า tasks.status='blocked' ต้องตรงกับจำนวน
    task_blocks ที่ยังเปิดอยู่ เสมอ

จังหวะที่ไฟล์นี้ยึด (เลขจังหวะถูกอ้างซ้ำในคอมเมนต์ของโค้ดด้านล่าง)

  จังหวะ 0  ตรวจ X-Line-Signature ก่อนแตะฐานข้อมูล
  จังหวะ 1  จอง webhookEventId — delivery ซ้ำกลายเป็น no-op
  จังหวะ 2  upsert chat_threads/contacts แล้ว INSERT chat_messages(direction='in')
            พร้อม raw_json — commit ให้เสร็จ "ก่อน" ตอบ 200 ข้อความจึงไม่มีวันหาย
            (intent ยังว่างไว้ ไม่รอตัวคัดแยก)
  จังหวะ 3  ตอบ 200 ทันที งานที่เหลือไปทำเบื้องหลัง
  จังหวะ 4  คัดแยก intent/urgency/confidence แล้ว UPDATE แถวเดิม
            เขียน intent กับ confidence คู่กันเสมอ เพราะ view ใช้
            COALESCE(confidence,1.0) — intent ที่ไม่มี confidence จะถูกนับเต็ม 100%
  จังหวะ 5  ปลดบล็อก: ข้อความเข้าจากคนที่เรารออยู่ปิด task_blocks ที่ยังเปิด
            โดยใช้ "เวลาที่เขาตอบ" (sent_at) ไม่ใช่เวลาที่เราประมวลผล
            และอัปเดต tasks + task_events ในทรานแซกชันเดียว (E1/E2/E3)
  จังหวะ 6  คำสั่งจากเจ้าของแชท (สร้างงาน / ประกาศว่าติด / เคลียร์)
  จังหวะ 7  ตอบกลับ: ใช้ reply token ถ้ายังไม่หมดอายุ ไม่งั้น push
            แถว chat_messages(direction='out') เขียน "หลัง" LINE รับเรื่องแล้ว
            เท่านั้น พร้อม responded_at ของคำถามที่ค้างอยู่ในเธรด

รันเดี่ยว ๆ ด้วย `python line_webhook.py` หรือ mount create_app() ในเซิร์ฟเวอร์อื่น
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import inspect
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from aiohttp import ClientError, ClientSession, ClientTimeout, web

logger = logging.getLogger(__name__)

# เวลาที่โปรเซสนี้เริ่ม — /healthz/storage เอาไปเทียบกับเวลาแก้ไขไฟล์ฐานข้อมูล
# ไฟล์ที่เก่ากว่าโปรเซส แปลว่ารอดข้าม deploy มาได้ คือ volume ทำงานจริง
PROCESS_STARTED_AT = time.time()

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

DATA_DIR = os.getenv("DATA_DIR", "data")
DB_PATH = os.getenv("DATABASE_PATH", f"{DATA_DIR}/assistant.db")

LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_API_BASE = os.getenv("LINE_API_BASE", "https://api.line.me/v2/bot")
WEBHOOK_PATH = os.getenv("LINE_WEBHOOK_PATH", "/webhook/line")

# LINE user id ของเจ้าของระบบ — คำสั่ง (สร้างงาน/ประกาศติด) รับเฉพาะจากคนนี้
OWNER_LINE_USER_ID = os.getenv("LINE_OWNER_USER_ID", "")

# reply token อยู่ได้ ~60 วิ เผื่อไว้หน่อยแล้วถอยไป push แทนการเผาโทเคนทิ้ง
REPLY_TOKEN_TTL_SECONDS = float(os.getenv("LINE_REPLY_TOKEN_TTL", "50"))
# เวลาที่ยอมให้ตัวคัดแยก (เช่น LLM) ใช้ ก่อนถอยไปใช้กฎ
CLASSIFY_BUDGET_SECONDS = float(os.getenv("LINE_CLASSIFY_BUDGET", "20"))

# ข้อความตอบรับอัตโนมัติของบอทไม่ใช่ "คุณตอบเขาแล้ว" ถ้าเขียนลง chat_messages
# ทุกครั้ง v_unanswered_now จะว่างเปล่าตลอดกาล ค่าเริ่มต้นจึงเป็นไม่เขียน
LOG_BOT_ACKS = os.getenv("LINE_LOG_BOT_ACKS", "0") == "1"

# /healthz/storage เปิดเผยพาธในเครื่องกับจำนวนแถว ซึ่งมากกว่าที่ /healthz บอก
# บริการนี้ต้องเปิดสู่อินเทอร์เน็ตอยู่แล้วเพราะ LINE ต้องยิง webhook เข้ามาได้
# ถ้าไม่ตั้งค่านี้ endpoint จะไม่มีอยู่เลย
STORAGE_REPORT_TOKEN = os.getenv("STORAGE_REPORT_TOKEN", "")

# OAuth ของ Google Drive — client เป็นของเจ้าของบอท ผู้ใช้แค่กดยินยอม
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
GOOGLE_OAUTH_CALLBACK_PATH = "/oauth/google/callback"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
# ขอสิทธิ์แคบที่สุดที่ยังทำงานได้ — drive.file ให้เห็นเฉพาะไฟล์ที่แอปนี้สร้างเอง
# ไม่ใช่ทั้งไดรฟ์ของผู้ใช้
GOOGLE_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
# ลิงก์ยินยอมมีอายุสั้น ๆ กันคนอื่นเก็บลิงก์เก่าไปผูกไดรฟ์ตัวเองกับบัญชีคนอื่น
OAUTH_STATE_TTL_SECONDS = 15 * 60

MAX_BODY_BYTES = 512 * 1024

PLATFORM = "line"

# reason ที่ใช้ใน task_blocks (ตรงกับที่ 01_schema.sql ระบุไว้)
REASON_PERSON = "รอคนตอบ"
REASON_DOC = "รอเอกสาร"
REASON_MONEY = "รอเงิน"
REASON_GOODS = "รอของ"
REASON_SELF = "รอคิวตัวเอง"
KNOWN_REASONS = (REASON_PERSON, REASON_DOC, REASON_MONEY, REASON_GOODS, REASON_SELF)


class LineApiError(RuntimeError):
    """LINE ตอบกลับมาไม่ใช่ 2xx"""

    def __init__(self, status: int, body: str):
        super().__init__(f"LINE API {status}: {body}")
        self.status = status
        self.body = body


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════════════════════

# ตารางเดียวที่ไฟล์นี้เพิ่มเข้าไปเอง เป็นงานประปาของ webhook ล้วน ๆ
# (dedupe + ผูก LINE message id กับ chat_messages.id สำหรับ unsend)
# ไม่แตะโครงวิเคราะห์ใน 01_schema.sql
PLUMBING_SCHEMA = """
CREATE TABLE IF NOT EXISTS line_webhook_deliveries (
  webhook_event_id TEXT PRIMARY KEY,
  event_type       TEXT,
  retry_key        TEXT,
  line_message_id  TEXT,
  chat_message_id  INTEGER REFERENCES chat_messages(id),
  received_at      TEXT NOT NULL,
  processed_at     TEXT,
  status           TEXT NOT NULL DEFAULT 'received',   -- received|processed|failed|skipped
  error            TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_line_deliveries_msg
  ON line_webhook_deliveries(line_message_id)
  WHERE line_message_id IS NOT NULL;
"""

REQUIRED_TABLES = ("chat_threads", "chat_messages", "tasks", "task_blocks", "task_events", "contacts")


def connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if db_path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


SQL_DIR = Path(__file__).resolve().parent / "sql"


def init_webhook_tables(db_path: str = DB_PATH) -> None:
    """สร้างตารางที่ webhook ต้องใช้ และลง schema ให้เองถ้าฐานข้อมูลยังว่าง

    คอนเทนเนอร์บน Railway เกิดใหม่ทุกครั้งที่ deploy ถ้ารอให้คนไปรัน
    sql/01_schema.sql เองก่อน เว็บจะบูตไม่ขึ้นทุกรอบ จึงลงให้อัตโนมัติเมื่อ
    ยังไม่มีตาราง และไม่แตะอะไรเลยถ้ามีอยู่แล้ว (ทุกไฟล์ใช้ IF NOT EXISTS)
    """
    directory = os.path.dirname(db_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = connect(db_path)
    try:
        if _missing_tables(conn):
            _bootstrap_schema(conn, db_path)
        missing = _missing_tables(conn)
        if missing:
            raise RuntimeError(
                f"ฐานข้อมูล {db_path} ยังไม่มีตาราง {', '.join(missing)} "
                f"และลง schema เองไม่ได้ (ไม่พบ {SQL_DIR}) — รัน sql/01_schema.sql ก่อน"
            )
        conn.executescript(PLUMBING_SCHEMA)
        conn.commit()
    finally:
        conn.close()
    logger.info("LINE webhook plumbing ready at %s", db_path)


def _missing_tables(conn: sqlite3.Connection) -> List[str]:
    existing = {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    return [name for name in REQUIRED_TABLES if name not in existing]


def _bootstrap_schema(conn: sqlite3.Connection, db_path: str) -> None:
    """ลง 01_schema.sql แล้วตามด้วย 02_views.sql (view เป็นของที่รายงานใช้)"""
    schema_file = SQL_DIR / "01_schema.sql"
    if not schema_file.is_file():
        return
    logger.info("ฐานข้อมูล %s ยังว่าง — ลง schema จาก %s", db_path, SQL_DIR)
    conn.executescript(schema_file.read_text(encoding="utf-8"))
    views_file = SQL_DIR / "02_views.sql"
    if views_file.is_file():
        try:
            conn.executescript(views_file.read_text(encoding="utf-8"))
        except sqlite3.Error as exc:
            # view พังไม่ควรทำให้ webhook บูตไม่ขึ้น — ข้อความยังบันทึกได้
            logger.error("ลง 02_views.sql ไม่สำเร็จ: %s", exc)
    conn.commit()


def utc_now() -> str:
    """ISO-8601 UTC ลงท้าย Z ตามกติกาเวลาใน 01_schema.sql"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def event_time(event: Dict[str, Any]) -> str:
    """เวลาของ event จาก LINE (มิลลิวินาที) → ISO UTC; ไม่มีก็ใช้เวลาปัจจุบัน"""
    ts = event.get("timestamp")
    if not isinstance(ts, (int, float)):
        return utc_now()
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ═══════════════════════════════════════════════════════════════════════════════
# SIGNATURE — จังหวะ 0
# ═══════════════════════════════════════════════════════════════════════════════

def verify_signature(channel_secret: str, body: bytes, signature: str) -> bool:
    """เทียบ X-Line-Signature กับ body ดิบแบบ constant time"""
    if not channel_secret or not signature:
        return False
    digest = hmac.new(channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature)


# ═══════════════════════════════════════════════════════════════════════════════
# ตัวคัดแยกข้อความ (กฎล้วน ไม่ใช้เน็ต — reply token รอไม่ได้)
# ═══════════════════════════════════════════════════════════════════════════════

REQUEST_RE = re.compile(
    # ขอ ต้องไม่ใช่ "ขอบคุณ/ขอโทษ/ขอแสดงความ" ซึ่งเป็นคำสุภาพ ไม่ใช่การขอของ
    r"(ขอ(?!บคุณ|บพระคุณ|โทษ|แสดงความ)|ช่วย|รบกวน|ฝาก|กรุณา|ส่ง.*(ให้|มา)|ได้ไหม|ได้มั้ย"
    r"|หน่อย|please|could you|can you)",
    re.IGNORECASE,
)
QUESTION_RE = re.compile(
    r"(\?|ไหม|มั้ย|หรือเปล่า|รึเปล่า|กี่โมง|เท่าไหร่|เท่าไร|เมื่อไหร่|เมื่อไร|ยังไง|อย่างไร|ทำไม|ที่ไหน|อะไร|ใคร)",
    re.IGNORECASE,
)
PROMISE_RE = re.compile(
    r"(เดี๋ยว|จะ(ส่ง|ทำ|โอน|เอา|ให้)|ไว้จะ|พรุ่งนี้จะ|รอแป๊บ|กำลังทำ|จัดให้|will send|on it)",
    re.IGNORECASE,
)
DECISION_RE = re.compile(
    r"(ตกลง|สรุปว่า|เอาตาม|อนุมัติ|ยืนยันตาม|ปิดดีล|ok เอา|โอเคเอา|confirmed|approved)",
    re.IGNORECASE,
)
URGENT_RE = re.compile(r"(ด่วน|เร่ง|ภายในวันนี้|เดี๋ยวนี้|ตอนนี้เลย|urgent|asap)", re.IGNORECASE)
RELAXED_RE = re.compile(r"(ไม่รีบ|เมื่อไหร่ก็ได้|ว่าง ๆ|ว่างๆ|no rush|whenever)", re.IGNORECASE)


def classify_message(text: Optional[str]) -> Dict[str, Any]:
    """คืน intent/urgency/confidence ให้ตรงกับ CHECK constraint ของ chat_messages

    confidence สะท้อนความมั่นใจจริง ๆ ของกฎ ค่าต่ำกว่า cfg.min_classifier_confidence
    (0.80) view จะไม่นับให้เอง จึงไม่ต้องกลัวการเดาผิด — แต่ต้องไม่โกหกว่ามั่นใจ
    """
    body = (text or "").strip()
    if not body:
        return {"intent": None, "urgency": None, "confidence": None}

    if REQUEST_RE.search(body):
        intent, confidence = "request", 0.86
    elif QUESTION_RE.search(body):
        intent, confidence = "question", 0.84
    elif PROMISE_RE.search(body):
        intent, confidence = "promise", 0.78
    elif DECISION_RE.search(body):
        intent, confidence = "decision", 0.76
    else:
        intent, confidence = "smalltalk", 0.55

    if URGENT_RE.search(body):
        urgency = "high"
    elif RELAXED_RE.search(body):
        urgency = "low"
    else:
        urgency = "normal"

    return {"intent": intent, "urgency": urgency, "confidence": confidence}


# ---- หลักฐานว่าการรอจบแล้ว -------------------------------------------------

DOC_DONE_RE = re.compile(r"(ส่งไฟล์|ส่งเอกสาร|แนบมา|ตามไฟล์|ไฟล์อยู่|เอกสารส่งแล้ว)", re.IGNORECASE)
MONEY_DONE_RE = re.compile(r"(โอนแล้ว|จ่ายแล้ว|สลิป|ชำระแล้ว|เงินเข้า|transferred|paid)", re.IGNORECASE)
GOODS_DONE_RE = re.compile(r"(ส่งของแล้ว|ของถึง|จัดส่งแล้ว|เลขพัสดุ|ของออกแล้ว|shipped)", re.IGNORECASE)


def reasons_released_by(message: Dict[str, Any], text: Optional[str]) -> List[str]:
    """ข้อความเข้าหนึ่งข้อความ ปลดการรอแบบไหนได้บ้าง

    'รอคนตอบ' จบทันทีที่เขาพิมพ์อะไรก็ได้กลับมา แต่ 'รอเอกสาร/รอเงิน/รอของ'
    ต้องมีหลักฐาน — ไฟล์แนบ รูปสลิป หรือคำที่บอกว่าส่งแล้วจริง ๆ
    ไม่งั้นเวลารอจะถูกปิดทิ้งทั้งที่ของยังไม่มา และตัวเลขคอขวดจะสวยเกินจริง
    """
    released = [REASON_PERSON]
    message_type = (message or {}).get("type")
    body = text or ""

    if message_type == "file" or DOC_DONE_RE.search(body):
        released.append(REASON_DOC)
    if message_type == "image" or MONEY_DONE_RE.search(body):
        # รูปในบริบทนี้มักเป็นสลิป/รูปเอกสารหน้างาน
        if message_type == "image":
            released.append(REASON_DOC)
        if MONEY_DONE_RE.search(body) or message_type == "image":
            released.append(REASON_MONEY)
    if GOODS_DONE_RE.search(body):
        released.append(REASON_GOODS)

    return sorted(set(released))


# ---- คำสั่งจากเจ้าของ -------------------------------------------------------

TASK_CMD_RE = re.compile(r"^\s*(?:งาน|task|todo)\s*[:：]\s*(.+)$", re.IGNORECASE | re.DOTALL)
BLOCK_CMD_RE = re.compile(r"^\s*(?:ติด|block)\s+#(\d+)\s*(.*)$", re.IGNORECASE | re.DOTALL)
UNBLOCK_CMD_RE = re.compile(r"^\s*(?:เคลียร์|ไม่ติดแล้ว|unblock)\s+#(\d+)\s*$", re.IGNORECASE)


def parse_owner_command(text: Optional[str]) -> Optional[Dict[str, Any]]:
    """แปลงข้อความของเจ้าของเป็นคำสั่ง — คืน None ถ้าเป็นข้อความธรรมดา"""
    body = (text or "").strip()
    if not body:
        return None

    match = TASK_CMD_RE.match(body)
    if match:
        title = match.group(1).strip().split("\n", 1)[0].strip()
        return {"command": "create_task", "title": title[:200]} if title else None

    match = BLOCK_CMD_RE.match(body)
    if match:
        rest = match.group(2).strip()
        reason = next((r for r in KNOWN_REASONS if r in rest), REASON_PERSON)
        mention = re.search(r"@(\S+)", rest)
        return {
            "command": "block",
            "task_id": int(match.group(1)),
            "reason": reason,
            "person": mention.group(1) if mention else None,
        }

    match = UNBLOCK_CMD_RE.match(body)
    if match:
        return {"command": "unblock", "task_id": int(match.group(1))}

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# WRITES — ทุกฟังก์ชันเป็น sync เรียกผ่าน asyncio.to_thread เสมอ
# ═══════════════════════════════════════════════════════════════════════════════

def claim_delivery(
    conn: sqlite3.Connection,
    event: Dict[str, Any],
    retry_key: Optional[str],
) -> bool:
    """จังหวะ 1 — จอง webhookEventId คืน True เฉพาะครั้งแรกที่เห็น

    LINE ยิงซ้ำด้วย id เดิมเมื่อเราตอบช้าหรือพัง PRIMARY KEY ที่นี่คือสิ่งเดียว
    ที่กันไม่ให้ delivery ซ้ำกลายเป็นข้อความซ้ำและเวลารอซ้ำ
    """
    event_id = event.get("webhookEventId")
    if not event_id:
        return True  # payload เก่า/ทดสอบมือ ไม่มีอะไรให้ dedupe

    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO line_webhook_deliveries
            (webhook_event_id, event_type, retry_key, line_message_id, received_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            event_id,
            event.get("type"),
            retry_key,
            (event.get("message") or {}).get("id"),
            utc_now(),
        ),
    )
    return cursor.rowcount == 1


def finish_delivery(
    conn: sqlite3.Connection,
    event_id: Optional[str],
    status: str,
    *,
    chat_message_id: Optional[int] = None,
    error: Optional[str] = None,
) -> None:
    if not event_id:
        return
    conn.execute(
        """
        UPDATE line_webhook_deliveries
           SET status = ?, processed_at = ?, error = ?,
               chat_message_id = COALESCE(?, chat_message_id)
         WHERE webhook_event_id = ?
        """,
        (status, utc_now(), error, chat_message_id, event_id),
    )
    conn.commit()


def upsert_thread(
    conn: sqlite3.Connection,
    external_chat_id: str,
    *,
    is_group: bool,
    sent_at: str,
    title: Optional[str] = None,
) -> sqlite3.Row:
    """หา/สร้าง chat_threads แล้วดัน last_msg_at ให้เป็นเวลาล่าสุดเสมอ"""
    conn.execute(
        """
        INSERT INTO chat_threads (platform, external_chat_id, title, is_group, last_msg_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (platform, external_chat_id) DO UPDATE SET
            title       = COALESCE(chat_threads.title, excluded.title),
            last_msg_at = CASE
                            WHEN chat_threads.last_msg_at IS NULL
                              OR excluded.last_msg_at > chat_threads.last_msg_at
                            THEN excluded.last_msg_at
                            ELSE chat_threads.last_msg_at
                          END
        """,
        (PLATFORM, external_chat_id, title, 1 if is_group else 0, sent_at),
    )
    row = conn.execute(
        "SELECT id, project_id, is_group FROM chat_threads WHERE platform = ? AND external_chat_id = ?",
        (PLATFORM, external_chat_id),
    ).fetchone()
    return row


def upsert_contact(
    conn: sqlite3.Connection,
    line_user_id: Optional[str],
    display_name: Optional[str] = None,
) -> Optional[int]:
    """คนคนเดียวกันต้องอยู่แถวเดียว — UNIQUE(line_user_id) เป็นคนตัดสิน"""
    if not line_user_id:
        return None
    # ยังไม่รู้ชื่อก็ใช้ LINE id ไปก่อน แต่ห้ามเอา id ไปทับชื่อจริงที่มีอยู่แล้ว
    # ไม่งั้น "รอ Farid" ในรายงานจะกลายเป็น "รอ Ufarid" ตั้งแต่ข้อความแรกที่เขาพิมพ์
    conn.execute(
        """
        INSERT INTO contacts (display_name, line_user_id)
        VALUES (COALESCE(?, ?), ?)
        ON CONFLICT (line_user_id) DO UPDATE SET
            display_name = COALESCE(?, contacts.display_name)
        """,
        (display_name, line_user_id, line_user_id, display_name),
    )
    row = conn.execute(
        "SELECT id FROM contacts WHERE line_user_id = ?", (line_user_id,)
    ).fetchone()
    return int(row["id"]) if row else None


def insert_inbound_message(
    conn: sqlite3.Connection,
    *,
    thread_id: int,
    contact_id: Optional[int],
    body: Optional[str],
    sent_at: str,
    project_id: Optional[int],
    raw_event: Dict[str, Any],
) -> int:
    """จังหวะ 2 — แถวข้อความเข้า เขียนก่อนตอบ 200 และก่อนคัดแยก

    intent/urgency/confidence ปล่อยว่างไว้ตั้งใจ: ถ้าใส่ค่ามั่ว ๆ ไปก่อน
    v_reply_latency จะเริ่มนับนาฬิกาให้ข้อความที่ยังไม่รู้ว่าเป็นคำถามหรือเปล่า
    """
    cursor = conn.execute(
        """
        INSERT INTO chat_messages
            (thread_id, contact_id, direction, body, sent_at, project_id, raw_json)
        VALUES (?, ?, 'in', ?, ?, ?, ?)
        """,
        (thread_id, contact_id, body, sent_at, project_id, json.dumps(raw_event, ensure_ascii=False)),
    )
    return int(cursor.lastrowid)


def apply_classification(
    conn: sqlite3.Connection,
    message_id: int,
    result: Dict[str, Any],
) -> None:
    """จังหวะ 4 — intent กับ confidence ต้องลงพร้อมกันเสมอ

    v_reply_latency กรองด้วย COALESCE(m.confidence, 1.0) >= min_classifier_confidence
    แถวที่มี intent แต่ confidence เป็น NULL จึงถูกนับเหมือนมั่นใจ 100%
    """
    intent = result.get("intent")
    confidence = result.get("confidence")
    if intent is not None and confidence is None:
        confidence = 0.5
    conn.execute(
        """
        UPDATE chat_messages
           SET intent = ?, urgency = ?, confidence = ?
         WHERE id = ?
        """,
        (intent, result.get("urgency"), confidence, message_id),
    )
    conn.commit()


def insert_outbound_message(
    conn: sqlite3.Connection,
    *,
    thread_id: int,
    body: str,
    sent_at: str,
    project_id: Optional[int],
    mark_responded: bool,
) -> int:
    """จังหวะ 7 — เขียน "หลัง" LINE รับข้อความแล้วเท่านั้น

    แถว out หนึ่งแถวคือหลักฐานว่า "คุณตอบแล้ว" ในสายตาของ v_reply_latency
    ถ้าเขียนล่วงหน้าแล้วส่งไม่สำเร็จ คำถามที่ยังค้างจะหายจาก v_unanswered_now
    contact_id เป็น NULL เสมอ ตามกติกาใน 01_schema.sql (out = ตัวเราเอง)
    """
    cursor = conn.execute(
        """
        INSERT INTO chat_messages (thread_id, contact_id, direction, body, sent_at, project_id)
        VALUES (?, NULL, 'out', ?, ?, ?)
        """,
        (thread_id, body, sent_at, project_id),
    )
    message_id = int(cursor.lastrowid)

    if mark_responded:
        # ปิดนาฬิกาของคำถาม/คำขอที่ค้างอยู่ในเธรดนี้ ณ เวลาที่ตอบจริง
        conn.execute(
            """
            UPDATE chat_messages
               SET responded_at = ?
             WHERE thread_id = ?
               AND direction = 'in'
               AND responded_at IS NULL
               AND sent_at <= ?
               AND intent IN ('request', 'question')
            """,
            (sent_at, thread_id, sent_at),
        )
    conn.commit()
    return message_id


def mark_message_unsent(conn: sqlite3.Connection, line_message_id: str) -> None:
    """LINE unsend — ลบเนื้อความแต่เก็บแถวไว้ เวลาที่ถามยังนับเป็นคอขวดอยู่"""
    row = conn.execute(
        "SELECT chat_message_id FROM line_webhook_deliveries WHERE line_message_id = ?",
        (line_message_id,),
    ).fetchone()
    if not row or row["chat_message_id"] is None:
        return
    conn.execute(
        "UPDATE chat_messages SET body = '(ยกเลิกข้อความ)', raw_json = NULL WHERE id = ?",
        (row["chat_message_id"],),
    )
    conn.commit()


# ---- task_blocks: E1 / E2 / E3 ---------------------------------------------

def _log_status_change(
    conn: sqlite3.Connection, task_id: int, from_status: Optional[str], to_status: str, at: str
) -> None:
    if from_status == to_status:
        return
    conn.execute(
        "INSERT INTO task_events (task_id, from_status, to_status, at) VALUES (?, ?, ?, ?)",
        (task_id, from_status, to_status, at),
    )


def open_block(
    conn: sqlite3.Connection,
    *,
    task_id: int,
    reason: str,
    contact_id: Optional[int],
    at: str,
) -> Optional[int]:
    """E1 — เปิดแถวใหม่ใน task_blocks พร้อมอัปเดตตัวชี้บน tasks ในทรานแซกชันเดียว

    ปิดแถวที่ยังเปิดค้างของงานนี้ก่อนเสมอ เพื่อให้ "หนึ่งงาน = หนึ่งการรอที่เปิดอยู่"
    ซึ่งเป็นเงื่อนไขที่ E3 ตรวจ และเป็นสิ่งที่ทำให้ tasks.blocked_reason มีความหมาย
    """
    with conn:
        task = conn.execute(
            "SELECT id, status FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if task is None:
            return None
        if task["status"] in ("done", "dropped"):
            return None  # งานปิดไปแล้ว ไม่มีอะไรให้รอ

        conn.execute(
            "UPDATE task_blocks SET unblocked_at = ? WHERE task_id = ? AND unblocked_at IS NULL",
            (at, task_id),
        )
        cursor = conn.execute(
            "INSERT INTO task_blocks (task_id, reason, contact_id, blocked_at) VALUES (?, ?, ?, ?)",
            (task_id, reason, contact_id, at),
        )
        conn.execute(
            """
            UPDATE tasks
               SET status = 'blocked', blocked_since = ?, blocked_reason = ?,
                   blocked_on_contact_id = ?
             WHERE id = ?
            """,
            (at, reason, contact_id, task_id),
        )
        _log_status_change(conn, task_id, task["status"], "blocked", at)
        return int(cursor.lastrowid)


def close_blocks(
    conn: sqlite3.Connection,
    *,
    task_id: int,
    at: str,
) -> bool:
    """E2 — ปิดการรอของงานหนึ่งชิ้น แล้วล้างตัวชี้บน tasks พร้อมกัน"""
    with conn:
        task = conn.execute(
            "SELECT id, status FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if task is None:
            return False
        changed = conn.execute(
            "UPDATE task_blocks SET unblocked_at = ? WHERE task_id = ? AND unblocked_at IS NULL",
            (at, task_id),
        ).rowcount
        if not changed:
            return False

        next_status = task["status"]
        if next_status == "blocked":
            next_status = "doing"
        conn.execute(
            """
            UPDATE tasks
               SET status = ?, blocked_since = NULL, blocked_reason = NULL,
                   blocked_on_contact_id = NULL,
                   started_at = COALESCE(started_at, ?)
             WHERE id = ?
            """,
            (next_status, at, task_id),
        )
        _log_status_change(conn, task_id, task["status"], next_status, at)
        return True


def release_blocks_for_contact(
    conn: sqlite3.Connection,
    *,
    contact_id: int,
    reasons: Sequence[str],
    at: str,
) -> List[Dict[str, Any]]:
    """จังหวะ 5 — คนที่เรารอ ตอบกลับมาแล้ว

    เวลาที่ใช้ปิดคือเวลาที่ "เขาส่งข้อความ" ไม่ใช่เวลาที่เราประมวลผลเสร็จ
    ถ้าใช้ now() เวลารอจะบวกเกินจริงทุกครั้งที่คิวหลังบ้านช้า และ v_wait_spans
    ก็คือฐานของรายงานคอขวดทั้งหมด
    """
    if not reasons:
        return []

    placeholders = ",".join("?" for _ in reasons)
    open_blocks = conn.execute(
        f"""
        SELECT b.id, b.task_id, b.reason, b.blocked_at, t.title, t.status
          FROM task_blocks b
          JOIN tasks t ON t.id = b.task_id
         WHERE b.unblocked_at IS NULL
           AND b.contact_id = ?
           AND b.reason IN ({placeholders})
           AND b.blocked_at <= ?
           AND t.status NOT IN ('done', 'dropped')
        """,
        (contact_id, *reasons, at),
    ).fetchall()

    released: List[Dict[str, Any]] = []
    for block in open_blocks:
        if close_blocks(conn, task_id=int(block["task_id"]), at=at):
            released.append(
                {
                    "block_id": int(block["id"]),
                    "task_id": int(block["task_id"]),
                    "title": block["title"],
                    "reason": block["reason"],
                }
            )
    return released


def create_task(
    conn: sqlite3.Connection,
    *,
    title: str,
    project_id: Optional[int],
    source_message_id: Optional[int],
    at: str,
) -> int:
    """สร้างงานจากแชท — source/source_ref ทำให้กดย้อนกลับไปดูข้อความต้นทางได้"""
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO tasks (title, project_id, status, created_at, source, source_ref)
            VALUES (?, ?, 'inbox', ?, ?, ?)
            """,
            (title, project_id, at, PLATFORM, str(source_message_id) if source_message_id else None),
        )
        task_id = int(cursor.lastrowid)
        _log_status_change(conn, task_id, None, "inbox", at)
        if source_message_id is not None:
            conn.execute(
                "UPDATE chat_messages SET linked_task_id = ? WHERE id = ?",
                (task_id, source_message_id),
            )
    return task_id


def find_contact_by_name(conn: sqlite3.Connection, name: str) -> Optional[int]:
    row = conn.execute(
        "SELECT id FROM contacts WHERE display_name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    return int(row["id"]) if row else None


def check_block_invariant(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """E3 — ควรได้ลิสต์ว่างเสมอ ใช้ใน health check และในเทสต์"""
    rows = conn.execute(
        """
        SELECT t.id, t.status, t.blocked_since,
               (SELECT COUNT(*) FROM task_blocks b
                 WHERE b.task_id = t.id AND b.unblocked_at IS NULL) AS open_blocks
          FROM tasks t
         WHERE (t.status = 'blocked') <> ((SELECT COUNT(*) FROM task_blocks b
                                            WHERE b.task_id = t.id
                                              AND b.unblocked_at IS NULL) > 0)
        """
    ).fetchall()
    return [dict(row) for row in rows]


# ═══════════════════════════════════════════════════════════════════════════════
# LINE MESSAGING API CLIENT
# ═══════════════════════════════════════════════════════════════════════════════

class LineClient:
    """ตัวห่อบาง ๆ ของ reply/push"""

    def __init__(
        self,
        access_token: str,
        *,
        api_base: str = LINE_API_BASE,
        session: Optional[ClientSession] = None,
        timeout: float = 10.0,
    ):
        self._access_token = access_token
        self._api_base = api_base.rstrip("/")
        self._session = session
        self._owns_session = session is None
        self._timeout = ClientTimeout(total=timeout)

    async def _get_session(self) -> ClientSession:
        if self._session is None or self._session.closed:
            self._session = ClientSession(timeout=self._timeout)
            self._owns_session = True
        return self._session

    async def _post(self, path: str, payload: Dict[str, Any]) -> None:
        session = await self._get_session()
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }
        async with session.post(f"{self._api_base}{path}", json=payload, headers=headers) as response:
            if response.status >= 300:
                raise LineApiError(response.status, await response.text())

    async def reply(self, reply_token: str, text: str) -> None:
        await self._post(
            "/message/reply",
            {"replyToken": reply_token, "messages": [{"type": "text", "text": text}]},
        )

    async def push(self, to: str, text: str) -> None:
        await self._post("/message/push", {"to": to, "messages": [{"type": "text", "text": text}]})

    async def profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        try:
            session = await self._get_session()
            headers = {"Authorization": f"Bearer {self._access_token}"}
            async with session.get(f"{self._api_base}/profile/{user_id}", headers=headers) as response:
                if response.status >= 300:
                    return None
                return await response.json()
        except (ClientError, asyncio.TimeoutError):
            return None

    async def close(self) -> None:
        if self._session and self._owns_session and not self._session.closed:
            await self._session.close()


# ═══════════════════════════════════════════════════════════════════════════════
# WEBHOOK HANDLER
# ═══════════════════════════════════════════════════════════════════════════════

class _Job:
    """หนึ่ง event ที่รับไว้แล้ว พร้อมนาฬิกาที่มันต้องแข่งด้วย"""

    __slots__ = (
        "kind", "event", "event_id", "message_id", "thread_id", "project_id",
        "contact_id", "chat_id", "is_group", "line_user_id", "text", "sent_at",
        "reply_token", "deadline",
    )

    def __init__(self, kind: str, event: Dict[str, Any], **fields: Any):
        self.kind = kind
        self.event = event
        self.event_id = event.get("webhookEventId")
        self.reply_token = event.get("replyToken")
        self.deadline = time.monotonic() + REPLY_TOKEN_TTL_SECONDS
        for name, value in fields.items():
            setattr(self, name, value)

    @property
    def budget(self) -> float:
        return self.deadline - time.monotonic()


class LineWebhookHandler:
    def __init__(
        self,
        *,
        db_path: str = DB_PATH,
        channel_secret: str = LINE_CHANNEL_SECRET,
        client: Optional[LineClient] = None,
        classifier: Optional[Callable[[Optional[str]], Any]] = None,
        owner_user_id: str = OWNER_LINE_USER_ID,
        log_bot_acks: bool = LOG_BOT_ACKS,
        reno: Optional[Any] = None,
    ):
        self.db_path = db_path
        self.channel_secret = channel_secret
        self.client = client
        self.classifier = classifier or classify_message
        self.owner_user_id = owner_user_id
        self.log_bot_acks = log_bot_acks
        # สะพานไป Reno Dashboard (reno_bridge.RenoBridge) — ไม่ใส่ก็ทำงานได้ตามปกติ
        self.reno = reno
        self._tasks: set[asyncio.Task] = set()

    # ── จังหวะ 0-3: ในคำขอ HTTP ─────────────────────────────────────────────
    async def handle(self, request: web.Request) -> web.Response:
        body = await request.read()

        if not self.channel_secret:
            logger.error("LINE_CHANNEL_SECRET ไม่ได้ตั้งค่า — ปฏิเสธ webhook")
            return web.json_response({"error": "webhook not configured"}, status=503)

        # จังหวะ 0 — body ที่ไม่มีลายเซ็นถูกต้อง ห้ามแตะฐานข้อมูล
        if not verify_signature(
            self.channel_secret, body, request.headers.get("X-Line-Signature", "")
        ):
            logger.warning("ปฏิเสธ webhook: ลายเซ็นไม่ถูกต้อง")
            return web.json_response({"error": "invalid signature"}, status=401)

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return web.json_response({"error": "invalid payload"}, status=400)

        events = payload.get("events")
        if not isinstance(events, list):
            return web.json_response({"error": "invalid payload"}, status=400)

        # จังหวะ 1-2 — dedupe แล้วเขียนข้อความเข้าให้เสร็จ ยังอยู่ในคำขอ
        jobs, duplicates = await asyncio.to_thread(
            self._intake, events, request.headers.get("X-Line-Retry-Key")
        )

        # จังหวะ 3 — ตอบ LINE ทันที ที่เหลือไปทำเบื้องหลัง
        for job in jobs:
            self._spawn(self._process(job))

        return web.json_response({"accepted": len(jobs), "duplicates": duplicates}, status=200)

    def _intake(
        self, events: List[Dict[str, Any]], retry_key: Optional[str]
    ) -> Tuple[List[_Job], int]:
        jobs: List[_Job] = []
        duplicates = 0
        conn = connect(self.db_path)
        try:
            for event in events:
                if not isinstance(event, dict):
                    continue
                with conn:
                    if not claim_delivery(conn, event, retry_key):
                        duplicates += 1
                        continue
                try:
                    job = self._intake_event(conn, event)
                except Exception as exc:
                    logger.exception("รับ event ไม่สำเร็จ")
                    finish_delivery(conn, event.get("webhookEventId"), "failed", error=str(exc))
                    continue
                if job is not None:
                    jobs.append(job)
        finally:
            conn.close()
        return jobs, duplicates

    def _intake_event(self, conn: sqlite3.Connection, event: Dict[str, Any]) -> Optional[_Job]:
        event_type = event.get("type")
        source = event.get("source") or {}
        chat_id = source.get("groupId") or source.get("roomId") or source.get("userId")
        is_group = source.get("type") in ("group", "room")
        line_user_id = source.get("userId")
        sent_at = event_time(event)

        if event_type == "unsend":
            unsent_id = (event.get("unsend") or {}).get("messageId")
            if unsent_id:
                mark_message_unsent(conn, unsent_id)
            finish_delivery(conn, event.get("webhookEventId"), "processed")
            return None

        if event_type not in ("message", "postback", "follow", "join") or not chat_id:
            finish_delivery(conn, event.get("webhookEventId"), "skipped")
            return None

        message = event.get("message") or {}
        if event_type == "message":
            text = message.get("text") if message.get("type") == "text" else None
            body = text if text is not None else f"({message.get('type', 'unknown')})"
        elif event_type == "postback":
            text = (event.get("postback") or {}).get("data")
            body = text
        else:
            text = None
            body = f"({event_type})"

        # จังหวะ 2 — thread + contact + ข้อความเข้า อยู่ในทรานแซกชันเดียวกัน
        # ทั้งสามอย่างต้องมีหรือไม่มีพร้อมกัน ไม่งั้นจะได้ thread ที่ไม่มีข้อความ
        with conn:
            thread = upsert_thread(conn, chat_id, is_group=is_group, sent_at=sent_at)
            contact_id = upsert_contact(conn, line_user_id)
            message_id = insert_inbound_message(
                conn,
                thread_id=int(thread["id"]),
                contact_id=contact_id,
                body=body,
                sent_at=sent_at,
                project_id=thread["project_id"],
                raw_event=event,
            )
            conn.execute(
                "UPDATE line_webhook_deliveries SET chat_message_id = ? WHERE webhook_event_id = ?",
                (message_id, event.get("webhookEventId")),
            )

        return _Job(
            "message" if event_type == "message" else event_type,
            event,
            message_id=message_id,
            thread_id=int(thread["id"]),
            project_id=thread["project_id"],
            contact_id=contact_id,
            chat_id=chat_id,
            is_group=is_group,
            line_user_id=line_user_id,
            text=text,
            sent_at=sent_at,
        )

    # ── จังหวะ 4-7: เบื้องหลัง ───────────────────────────────────────────────
    async def _process(self, job: _Job) -> None:
        # ทุกขั้นเปิด connection ของตัวเองผ่าน _with_connection — ห้ามถือ
        # connection เดียวข้ามหลาย asyncio.to_thread (sqlite ผูกกับเธรด)
        try:
            replies: List[str] = []

            if job.kind == "message":
                # จังหวะ 4 — คัดแยกแล้วเติมกลับลงแถวเดิม
                result = await self._classify(job)
                await asyncio.to_thread(
                    _with_connection, self.db_path, apply_classification, job.message_id, result
                )

                # จังหวะ 5 — ปลดการรอ ก่อนจะไปคิดเรื่องตอบกลับ
                # ต้องเกิดแม้การตอบกลับจะล้มเหลว เพราะ "เขาตอบแล้ว" เป็นข้อเท็จจริง
                # ที่ไม่ได้ขึ้นกับว่าเราส่งข้อความออกได้หรือไม่
                released = await asyncio.to_thread(
                    _with_connection, self.db_path, self._release, job
                )
                if released:
                    replies.append(self._released_text(released))

                # จังหวะ 6 — คำสั่งของเจ้าของ
                command_reply = await asyncio.to_thread(
                    _with_connection, self.db_path, self._run_owner_command, job
                )
                if command_reply:
                    replies.append(command_reply)

                # จังหวะ 6b — ส่งต่อให้ Reno Dashboard คิดต่อ (งาน/เงิน/สต็อก)
                # อยู่หลังจังหวะ 5 เสมอ: การปลดการรอเป็นข้อเท็จจริงของแชท ส่วน
                # รายการที่เดาได้จากข้อความยังต้องรอเจ้าของยืนยันก่อนเข้า dashboard
                if self.reno is not None:
                    reno_reply = await asyncio.to_thread(
                        _with_connection, self.db_path, self._run_reno, job
                    )
                    if reno_reply:
                        replies.append(reno_reply)

            elif job.kind in ("follow", "join"):
                replies.append(WELCOME_TEXT)

            await asyncio.to_thread(
                _with_connection, self.db_path, finish_delivery,
                job.event_id, "processed", chat_message_id=job.message_id,
            )

            # จังหวะ 7
            if replies:
                await self._respond(job, "\n\n".join(replies))
        except Exception as exc:
            logger.exception("ประมวลผล event %s ไม่สำเร็จ", job.event_id)
            try:
                await asyncio.to_thread(
                    _with_connection, self.db_path, finish_delivery,
                    job.event_id, "failed", error=str(exc),
                )
            except Exception:
                logger.exception("อัปเดตสถานะ delivery ไม่สำเร็จ")

    async def _classify(self, job: _Job) -> Dict[str, Any]:
        """ให้ตัวคัดแยกทำงานภายในงบเวลาของ reply token

        ตัวคัดแยกที่ต่อ LLM ห้ามกิน reply token จนหมด ถ้าเกินงบก็ถอยไปใช้กฎ
        แล้วปล่อยให้จังหวะ 7 ตัดสินใจว่าจะ reply หรือ push
        """
        budget = max(1.0, min(CLASSIFY_BUDGET_SECONDS, job.budget - 5.0))
        try:
            result = self.classifier(job.text)
            if inspect.isawaitable(result):
                result = await asyncio.wait_for(result, timeout=budget)
            return dict(result or {})
        except asyncio.TimeoutError:
            logger.warning("ตัวคัดแยกใช้เวลาเกิน %.1f วิ — ใช้ผลจากกฎแทน", budget)
            return classify_message(job.text)
        except Exception:
            logger.exception("ตัวคัดแยกล้มเหลว — ใช้ผลจากกฎแทน")
            return classify_message(job.text)

    def _release(self, conn: sqlite3.Connection, job: _Job) -> List[Dict[str, Any]]:
        if job.contact_id is None or job.line_user_id == self.owner_user_id:
            return []  # ข้อความของเราเองไม่ใช่การตอบของคนที่เรารอ
        reasons = reasons_released_by(job.event.get("message") or {}, job.text)
        return release_blocks_for_contact(
            conn, contact_id=job.contact_id, reasons=reasons, at=job.sent_at
        )

    @staticmethod
    def _released_text(released: List[Dict[str, Any]]) -> str:
        lines = [f"✅ ปลดการรอแล้ว {len(released)} งาน"]
        for item in released[:5]:
            lines.append(f"• #{item['task_id']} {item['title']} ({item['reason']})")
        return "\n".join(lines)

    def _run_owner_command(self, conn: sqlite3.Connection, job: _Job) -> Optional[str]:
        """จังหวะ 6 — รับคำสั่งเฉพาะจากเจ้าของ ป้องกันคนอื่นแก้งานเราผ่านแชท"""
        if not self.owner_user_id or job.line_user_id != self.owner_user_id:
            return None
        command = parse_owner_command(job.text)
        if not command:
            return None

        if command["command"] == "create_task":
            task_id = create_task(
                conn,
                title=command["title"],
                project_id=job.project_id,
                source_message_id=job.message_id,
                at=job.sent_at,
            )
            return f"📌 สร้างงาน #{task_id} · {command['title']}"

        if command["command"] == "block":
            contact_id = (
                find_contact_by_name(conn, command["person"]) if command["person"] else None
            )
            block_id = open_block(
                conn,
                task_id=command["task_id"],
                reason=command["reason"],
                contact_id=contact_id,
                at=job.sent_at,
            )
            if block_id is None:
                return f"❓ ไม่พบงาน #{command['task_id']} ที่ยังเปิดอยู่"
            who = f" · รอ {command['person']}" if command["person"] else ""
            return f"⏸ งาน #{command['task_id']} ติด: {command['reason']}{who}"

        if command["command"] == "unblock":
            if close_blocks(conn, task_id=command["task_id"], at=job.sent_at):
                return f"▶️ งาน #{command['task_id']} เดินต่อแล้ว"
            return f"❓ งาน #{command['task_id']} ไม่มีการรอที่เปิดค้างอยู่"

        return None

    def _run_reno(self, conn: sqlite3.Connection, job: _Job) -> Optional[str]:
        """คำสั่ง reno ของเจ้าของมาก่อน แล้วค่อยให้ตัวแยกอ่านข้อความธรรมดา"""
        try:
            if self.owner_user_id and job.line_user_id == self.owner_user_id:
                handled = self.reno.handle_command(conn, job.text)
                if handled:
                    return handled
                if parse_owner_command(job.text):
                    # จังหวะ 6 สร้างงานให้แล้ว ไม่ต้องให้ bridge เสนอซ้ำอีกใบ
                    return None
            sender = None
            if job.contact_id is not None and job.line_user_id != self.owner_user_id:
                row = conn.execute(
                    "SELECT display_name FROM contacts WHERE id = ?", (job.contact_id,)
                ).fetchone()
                sender = row["display_name"] if row else None
            return self.reno.on_message(
                conn,
                chat_message_id=job.message_id,
                text=job.text,
                sent_at=job.sent_at,
                sender=sender,
            )
        except Exception:
            # สะพานพังต้องไม่ลาก webhook ลงไปด้วย ข้อความยังถูกบันทึกครบแล้ว
            logger.exception("reno bridge ล้มเหลวสำหรับข้อความ %s", job.message_id)
            return None

    async def _respond(self, job: _Job, text: str) -> None:
        """จังหวะ 7 — ส่งก่อน แล้วค่อยบันทึก

        ลำดับนี้กลับกับข้อความขาเข้าโดยตั้งใจ: ขาเข้าต้องบันทึกก่อนตอบ 200 เพราะ
        ข้อมูลหายไม่ได้ ส่วนขาออกต้องส่งสำเร็จก่อนบันทึก เพราะแถว out ที่ไม่มี
        ข้อความจริงจะไปปิดนาฬิกาของ v_reply_latency ให้คำถามที่ยังไม่มีใครตอบ
        """
        if self.client is None:
            logger.warning("ไม่ได้ตั้งค่า LINE client — ข้ามการตอบกลับ")
            return

        sent = False
        if job.reply_token and job.budget > 1.0:
            try:
                await self.client.reply(job.reply_token, text)
                sent = True
            except (LineApiError, ClientError, asyncio.TimeoutError) as exc:
                logger.warning("reply ไม่สำเร็จ (%s) — ถอยไป push", exc)

        if not sent:
            try:
                await self.client.push(job.chat_id, text)
                sent = True
            except (LineApiError, ClientError, asyncio.TimeoutError) as exc:
                logger.error("push ไม่สำเร็จสำหรับ %s: %s", job.chat_id, exc)
                return

        # ข้อความตอบรับของบอทไม่ใช่คำตอบของเจ้าของ — ไม่ควรไปปิด responded_at
        if self.log_bot_acks:
            await asyncio.to_thread(
                _with_connection,
                self.db_path,
                insert_outbound_message,
                thread_id=job.thread_id,
                body=text,
                sent_at=utc_now(),
                project_id=job.project_id,
                mark_responded=False,
            )

    async def record_owner_reply(
        self, *, thread_id: int, body: str, project_id: Optional[int] = None
    ) -> int:
        """ให้ส่วนอื่นของแอปเรียกเมื่อ "เจ้าของ" ตอบจริง (เช่นส่งจากแดชบอร์ด)

        นี่คือทางเดียวที่ responded_at ควรถูกเติม — การตอบของคน ไม่ใช่ของบอท
        """
        return await asyncio.to_thread(
            _with_connection,
            self.db_path,
            insert_outbound_message,
            thread_id=thread_id,
            body=body,
            sent_at=utc_now(),
            project_id=project_id,
            mark_responded=True,
        )

    # ── งานเบื้องหลัง ────────────────────────────────────────────────────────
    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self, timeout: float = 30.0) -> None:
        """รอให้ event ที่ค้างอยู่ทำงานจบก่อนปิดโปรเซส

        วนจนกว่าคิวจะว่างจริง ไม่ใช่รอแค่รอบเดียว — งานที่ถูกสร้างระหว่างรออยู่
        จะได้ไม่ถูกทิ้งค้างตอนปิดโปรเซส
        """
        deadline = time.monotonic() + timeout
        while self._tasks:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning("ยังมีงานเบื้องหลังค้างอยู่ %d รายการตอนปิด", len(self._tasks))
                return
            await asyncio.wait(set(self._tasks), timeout=remaining)

    async def close(self) -> None:
        await self.drain()
        if self.client is not None:
            await self.client.close()


WELCOME_TEXT = (
    "สวัสดีครับ 👋 ผมจดแชทนี้ให้อัตโนมัติ\n"
    "• `งาน: ...` สร้างงานใหม่\n"
    "• `ติด #12 รอเอกสาร @Farid` บันทึกว่างานติดใคร\n"
    "• `เคลียร์ #12` ปิดการรอ\n"
    "เมื่อคนที่เรารอตอบกลับมา ผมปิดเวลารอให้เองครับ"
)


# ═══════════════════════════════════════════════════════════════════════════════
# APP
# ═══════════════════════════════════════════════════════════════════════════════

def _build_reno_bridge():
    """เปิดสะพาน Reno Dashboard เมื่อ RENO_BRIDGE=1 (ดู RENO_BRIDGE.md)"""
    if os.getenv("RENO_BRIDGE", "0") != "1":
        return None
    try:
        import reno_bridge
    except ImportError:
        logger.error("ตั้ง RENO_BRIDGE=1 แต่ import reno_bridge ไม่ได้")
        return None

    bridge = reno_bridge.RenoBridge(
        auto_approve=os.getenv("RENO_AUTO_APPROVE", "0") == "1"
    )
    conn = connect(DB_PATH)
    try:
        bridge.ensure_schema(conn)
    finally:
        conn.close()
    logger.info("Reno bridge พร้อมใช้งาน (dashboard: %s)", bridge.config["dashboard_path"])
    return bridge


def _drift_snapshot(db_path: str) -> List[Dict[str, Any]]:
    """เปิด–ถาม–ปิด ฐานข้อมูลให้จบในเธรดเดียว

    sqlite3 ผูก connection ไว้กับเธรดที่สร้างมัน (check_same_thread เป็น True
    ตามค่าเริ่มต้น) การแยก connect / query / close ออกเป็น asyncio.to_thread
    คนละครั้ง จึงพังทันทีที่ thread pool แตกเป็นหลายเธรด — ซึ่งเกิดขึ้นเมื่อ
    health check ถูกยิงซ้อนกัน ทำให้ /healthz ตอบ 500 แล้ว Railway รีสตาร์ตวน
    """
    conn = connect(db_path)
    try:
        return check_block_invariant(conn)
    finally:
        conn.close()


def _with_connection(db_path: str, fn, *args, **kwargs):
    """เปิด connection ของตัวเอง เรียก fn แล้วปิด — จบในเธรดเดียว

    sqlite3 ผูก connection กับเธรดที่สร้างมัน (check_same_thread เป็น True ตาม
    ค่าเริ่มต้น) การถือ connection เดียวแล้วส่งข้าม asyncio.to_thread หลายครั้ง
    จึงพังทันทีที่ thread pool แตกเป็นหลายเธรด

    ทุกขั้นใน _process commit ของตัวเองอยู่แล้ว (finish_delivery,
    apply_classification, insert_outbound_message ต่างเรียก conn.commit())
    ไม่มี transaction คร่อมหลายขั้น การเปิด connection ใหม่ต่อขั้นจึงให้ผล
    เหมือนเดิมทุกประการ
    """
    conn = connect(db_path)
    try:
        return fn(conn, *args, **kwargs)
    finally:
        conn.close()


async def _health(request: web.Request) -> web.Response:
    """liveness — ตอบ 200 ตราบใดที่ยังเปิดฐานข้อมูลได้

    ตั้งใจไม่ให้ 500 เพราะข้อมูลไม่สอดคล้อง: health check ของ Railway ผูกกับ
    endpoint นี้ ถ้าตอบ 500 ตอนตัวชี้เพี้ยน คอนเทนเนอร์จะถูกรีสตาร์ตวนไปเรื่อย ๆ
    ทั้งที่ webhook ยังรับข้อความได้ปกติ — ดูความสอดคล้องที่ /healthz/invariants
    """
    handler: LineWebhookHandler = request.app["line_handler"]
    try:
        drift = await asyncio.to_thread(_drift_snapshot, handler.db_path)
    except sqlite3.Error as exc:
        # ครอบทั้งการเปิดและการถาม — ข้อผิดพลาดของ sqlite ต้องไม่กลายเป็น 500
        return web.json_response({"status": "error", "error": str(exc)}, status=503)
    return web.json_response({"status": "ok", "block_pointer_drift": len(drift)})


async def _invariants(request: web.Request) -> web.Response:
    """E3 จาก sql/04_queries.sql — 500 พร้อมรายการงานที่ตัวชี้เพี้ยน"""
    handler: LineWebhookHandler = request.app["line_handler"]
    drift = await asyncio.to_thread(_drift_snapshot, handler.db_path)
    return web.json_response(
        {"status": "ok" if not drift else "drift", "block_pointer_drift": drift},
        status=200 if not drift else 500,
    )


def _storage_snapshot(db_path: str) -> Dict[str, Any]:
    """สำรวจว่าไฟล์ฐานข้อมูลอยู่ที่ไหนและมีอะไรอยู่ — จบในเธรดเดียว"""
    directory = os.path.dirname(os.path.abspath(db_path)) or "/"
    report: Dict[str, Any] = {
        "db_path": os.path.abspath(db_path),
        "data_dir": directory,
        "exists": os.path.exists(db_path),
    }

    # volume ของ Railway ถูก mount เป็นอุปกรณ์คนละตัวกับ root filesystem
    # ถ้า st_dev ตรงกับของ / แปลว่ายังเขียนลงดิสก์ชั่วคราวที่หายทุก deploy
    try:
        report["on_separate_device"] = os.stat(directory).st_dev != os.stat("/").st_dev
    except OSError as exc:
        report["on_separate_device"] = None
        report["device_error"] = str(exc)

    if report["exists"]:
        # ไฟล์อาจหายไประหว่าง exists() กับ stat() — นี่คือรายงาน ไม่ควรพังทั้ง endpoint
        try:
            stat = os.stat(db_path)
        except OSError as exc:
            report["exists"] = False
            report["stat_error"] = str(exc)
        else:
            report["size_bytes"] = stat.st_size
            report["modified_at"] = _iso(stat.st_mtime)
            # ไฟล์เก่ากว่าโปรเซสนี้ = รอดข้าม deploy มา = volume ทำงานจริง
            report["predates_this_process"] = stat.st_mtime < PROCESS_STARTED_AT

    report["process_started_at"] = _iso(PROCESS_STARTED_AT)
    report["rows"] = _row_counts(db_path) if report["exists"] else {}
    return report


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


COUNTED_TABLES = ("tasks", "chat_messages", "chat_threads", "reminders", "notes")


def _row_counts(db_path: str) -> Dict[str, Any]:
    """นับแถวเท่าที่นับได้ ตารางที่ยังไม่มีก็ข้ามไป ไม่ทำให้ทั้ง endpoint พัง"""
    counts: Dict[str, Any] = {}
    try:
        # อ่านอย่างเดียว — connect() ปกติสั่ง PRAGMA journal_mode = WAL ทุกครั้ง
        # ซึ่งเป็นการเขียน รายงานไม่ควรไปแตะสถานะของฐานข้อมูล และจะพังถ้า volume
        # ถูก mount แบบอ่านอย่างเดียว
        conn = sqlite3.connect(f"file:{quote(os.path.abspath(db_path))}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return {"error": str(exc)}
    try:
        for table in COUNTED_TABLES:
            try:
                counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                counts[table] = None      # ตารางยังไม่ถูกสร้าง
    finally:
        conn.close()
    return counts


def _oauth_state_secret() -> bytes:
    """กุญแจเซ็น state — ใช้ client secret ที่มีอยู่แล้วฝั่งเซิร์ฟเวอร์"""
    return GOOGLE_CLIENT_SECRET.encode("utf-8")


def make_oauth_state(user_id: int, issued_at: Optional[float] = None) -> str:
    """เซ็น Telegram user id ลงใน state ของ OAuth

    ถ้า state เป็น user id เปล่า ๆ ใครก็ยิง callback พร้อม id ของคนอื่นเพื่อผูก
    Drive ตัวเองเข้ากับบัญชีคนนั้นได้ ลายเซ็นทำให้ปลอม id ไม่ได้ และเวลาที่ฝัง
    มาด้วยทำให้ลิงก์เก่าหมดอายุ
    """
    stamp = int(time.time() if issued_at is None else issued_at)
    body = f"{user_id}.{stamp}"
    sig = hmac.new(_oauth_state_secret(), body.encode("utf-8"), hashlib.sha256).digest()
    token = base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")
    return f"{body}.{token}"


def verify_oauth_state(state: str, now: Optional[float] = None) -> Optional[int]:
    """คืน user id ถ้าลายเซ็นถูกและยังไม่หมดอายุ ไม่งั้นคืน None"""
    if not GOOGLE_CLIENT_SECRET:
        return None
    parts = state.split(".")
    if len(parts) != 3:
        return None
    raw_user, raw_stamp, _ = parts
    expected = make_oauth_state_from_parts(raw_user, raw_stamp)
    if expected is None or not hmac.compare_digest(expected, state):
        return None
    try:
        stamp = int(raw_stamp)
        user_id = int(raw_user)
    except ValueError:
        return None
    current = time.time() if now is None else now
    if current - stamp > OAUTH_STATE_TTL_SECONDS:
        return None
    return user_id


def make_oauth_state_from_parts(raw_user: str, raw_stamp: str) -> Optional[str]:
    """ประกอบ state ที่ควรจะเป็นขึ้นมาใหม่เพื่อเทียบ — ไม่แปลงชนิดก่อนเทียบ

    เทียบจากสตริงดิบที่ส่งมา ไม่ใช่จาก int ที่แปลงแล้ว มิฉะนั้น "007" กับ "7"
    จะให้ลายเซ็นคนละอันแต่ผ่านทั้งคู่
    """
    body = f"{raw_user}.{raw_stamp}"
    sig = hmac.new(_oauth_state_secret(), body.encode("utf-8"), hashlib.sha256).digest()
    token = base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")
    return f"{body}.{token}"


def google_consent_url(user_id: int) -> Optional[str]:
    """ลิงก์ที่ส่งให้ผู้ใช้กดยินยอม — None ถ้ายังตั้งค่า OAuth ไม่ครบ"""
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and PUBLIC_BASE_URL):
        return None
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": f"{PUBLIC_BASE_URL}{GOOGLE_OAUTH_CALLBACK_PATH}",
        "response_type": "code",
        "scope": GOOGLE_DRIVE_SCOPE,
        # ต้องมีทั้งสองตัวถึงจะได้ refresh token กลับมา ไม่งั้นได้แค่ access token
        # ที่หมดอายุในหนึ่งชั่วโมงแล้วต่อใหม่ไม่ได้
        "access_type": "offline",
        "prompt": "consent",
        "state": make_oauth_state(user_id),
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def _oauth_page(message: str, status: int = 200) -> web.Response:
    """หน้าเปล่า ๆ ที่ผู้ใช้เห็นหลังกดยินยอม — ไม่มีอะไรลับอยู่ในนั้น"""
    body = (
        "<!doctype html><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Assistant everyTask Bot</title>"
        "<body style='font-family:system-ui;padding:2rem;line-height:1.6'>"
        f"<p>{message}</p></body>"
    )
    return web.Response(text=body, content_type="text/html", status=status)


async def _google_oauth_callback(request: web.Request) -> web.Response:
    """รับ code จาก Google แลกเป็น refresh token แล้วเก็บให้ผู้ใช้คนนั้น

    ทั้งหมดนี้เกิดนอกแชต Telegram โดยตั้งใจ — วิธีที่ให้ผู้ใช้พิมพ์ token ใส่แชต
    ทำให้ credential ค้างอยู่ในประวัติแชตถาวร ตรงนี้สิ่งที่วิ่งผ่านมือผู้ใช้มีแค่
    ลิงก์ที่หมดอายุใน 15 นาที
    """
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and PUBLIC_BASE_URL):
        return _oauth_page("ยังไม่ได้ตั้งค่า Google OAuth ฝั่งเซิร์ฟเวอร์", status=503)

    if request.query.get("error"):
        return _oauth_page("ยกเลิกการเชื่อมต่อแล้ว กลับไปที่ Telegram ได้เลย")

    code = request.query.get("code", "")
    state = request.query.get("state", "")
    user_id = verify_oauth_state(state) if state else None
    if not code or user_id is None:
        return _oauth_page("ลิงก์ไม่ถูกต้องหรือหมดอายุแล้ว — พิมพ์ /settings ใหม่", status=400)

    payload = {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": f"{PUBLIC_BASE_URL}{GOOGLE_OAUTH_CALLBACK_PATH}",
        "grant_type": "authorization_code",
    }
    try:
        async with ClientSession() as session:
            async with session.post(GOOGLE_TOKEN_URL, data=payload) as response:
                if response.status != 200:
                    logger.warning("แลก code เป็น token ไม่สำเร็จ (%s)", response.status)
                    return _oauth_page("เชื่อมต่อไม่สำเร็จ ลองใหม่อีกครั้ง", status=502)
                data = await response.json()
    except Exception:
        logger.exception("แลก code เป็น token ไม่สำเร็จ")
        return _oauth_page("เชื่อมต่อไม่สำเร็จ ลองใหม่อีกครั้ง", status=502)

    refresh_token = data.get("refresh_token")
    if not refresh_token:
        # เกิดเมื่อผู้ใช้เคยยินยอมไว้แล้วและ Google ไม่ส่ง refresh token ซ้ำ
        # prompt=consent ควรกันไว้แล้ว แต่ถ้าหลุดมาก็ต้องบอกให้ชัด ไม่ใช่เก็บ
        # ค่าว่างแล้วไปพังตอนใช้งานจริง
        logger.warning("Google ไม่ส่ง refresh token กลับมา (user %s)", user_id)
        return _oauth_page(
            "Google ไม่ได้ส่งสิทธิ์ระยะยาวกลับมา — ถอนสิทธิ์แอปนี้ที่ "
            "myaccount.google.com/permissions แล้วลองใหม่",
            status=400,
        )

    # import ตรงนี้ไม่ใช่บนหัวไฟล์ เพราะ bot.py import โมดูลนี้อยู่ — วนกลับกันไม่ได้
    import bot

    await asyncio.to_thread(bot.StorageSettings.set_google_drive, user_id, refresh_token)
    logger.info("ผูก Google Drive ให้ผู้ใช้ %s แล้ว", user_id)
    return _oauth_page("เชื่อม Google Drive เรียบร้อย กลับไปที่ Telegram ได้เลย ✅")


def _storage_token_ok(request: web.Request) -> bool:
    """โทเคนมาทาง header หรือ query ก็ได้ — query ทำให้เปิดจากเบราว์เซอร์ได้เลย"""
    if not STORAGE_REPORT_TOKEN:
        return False
    supplied = request.headers.get("X-Storage-Token") or request.query.get("token", "")
    return hmac.compare_digest(supplied, STORAGE_REPORT_TOKEN)


async def _storage(request: web.Request) -> web.Response:
    """ฐานข้อมูลอยู่ที่ไหน อยู่บน volume จริงไหม และมีอะไรอยู่ในนั้นบ้าง

    ไม่มีทางเข้า shell ของคอนเทนเนอร์บน Railway การจะรู้ว่า volume mount ติดจริง
    ไหมจึงต้องถามจากในแอปเอง ตัวชี้ขาดคือ on_separate_device — volume ถูก mount
    เป็นอุปกรณ์คนละตัวกับ root filesystem ถ้าเป็น false แปลว่ายังเขียนลงดิสก์
    ชั่วคราวที่หายทุก deploy อยู่

    ต้องมี STORAGE_REPORT_TOKEN ถึงจะเรียกได้ — รายงานนี้บอกพาธในเครื่องและจำนวน
    แถว ซึ่งมากกว่าที่ /healthz บอก และบริการต้องเปิดสู่อินเทอร์เน็ตอยู่แล้วเพราะ
    LINE ต้องยิง webhook เข้ามาได้ ไม่ตั้งค่า = ไม่มี endpoint นี้ ตอบ 404 เหมือน
    พาธที่ไม่มีอยู่จริง ไม่ใช่ 403 ที่ไปบอกคนสแกนว่ามีอะไรซ่อนอยู่ตรงนี้

    ผ่านด่านแล้วตอบ 200 เสมอ นี่คือรายงาน ไม่ใช่ health check — Railway ผูกอยู่
    กับ /healthz ซึ่งไม่ต้องใช้โทเคน
    """
    if not _storage_token_ok(request):
        return web.json_response({"error": "not found"}, status=404)

    handler: LineWebhookHandler = request.app["line_handler"]
    snapshot = await asyncio.to_thread(_storage_snapshot, handler.db_path)
    return web.json_response(snapshot)


def create_app(handler: Optional[LineWebhookHandler] = None) -> web.Application:
    if handler is None:
        init_webhook_tables(DB_PATH)
        handler = LineWebhookHandler(
            db_path=DB_PATH,
            channel_secret=LINE_CHANNEL_SECRET,
            client=LineClient(LINE_CHANNEL_ACCESS_TOKEN),
            reno=_build_reno_bridge(),
        )

    app = web.Application(client_max_size=MAX_BODY_BYTES)
    app["line_handler"] = handler
    app.router.add_post(WEBHOOK_PATH, handler.handle)
    app.router.add_get("/healthz", _health)
    app.router.add_get("/healthz/invariants", _invariants)
    app.router.add_get("/healthz/storage", _storage)
    app.router.add_get(GOOGLE_OAUTH_CALLBACK_PATH, _google_oauth_callback)

    async def _cleanup(app: web.Application) -> None:
        await app["line_handler"].close()

    app.on_cleanup.append(_cleanup)
    return app


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
    if not LINE_CHANNEL_SECRET or not LINE_CHANNEL_ACCESS_TOKEN:
        logger.warning(
            "ยังไม่ได้ตั้ง LINE_CHANNEL_SECRET / LINE_CHANNEL_ACCESS_TOKEN — "
            "webhook จะปฏิเสธทุก delivery จนกว่าจะตั้งค่า"
        )
    web.run_app(create_app(), port=int(os.getenv("PORT", "8080")))


if __name__ == "__main__":
    main()
