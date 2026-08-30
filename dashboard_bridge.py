"""ส่งข้อความที่บอทเก็บไว้ ขึ้นไปให้ dashboard ที่ ch-howtoniksen.com

บอทยังเป็นเจ้าของบทสนทนาเหมือนเดิม — SQLite ที่นี่คือต้นฉบับ ฝั่งโน้นเก็บสำเนา
ไว้อ่านอย่างเดียว เพื่อให้หน้า /messages เห็นว่ามีอะไรค้างอยู่ที่ใครบ้าง

ทำไมเป็นการ "ซิงก์" ไม่ใช่ยิงทันทีตอนข้อความเข้า
    1. ข้อความเก่าเกือบพันข้อความอยู่ในเครื่องแล้วตั้งแต่ก่อนมีสะพานนี้ ต้องมี
       ทางส่งย้อนหลัง
    2. /reclassify เปลี่ยนผลคัดแยกของข้อความเก่า ฝั่งโน้นต้องได้ผลใหม่ด้วย
       ไม่งั้น dashboard จะค้างอยู่กับคำตอบแรกที่ regex เดาไว้ตลอดไป
    3. เส้นทาง webhook เป็นเส้นที่ LINE รอ 200 อยู่ ไม่ควรมีปลายทางที่สามมาถ่วง

ตั้งค่าไม่ครบ = ปิดเงียบ ไม่พัง (กติกาเดียวกับ RENO_BRIDGE และ LINE)

    DASHBOARD_API_URL       เช่น https://ch-howtoniksen.com/api
    DASHBOARD_API_USER      basic auth ของ Caddy
    DASHBOARD_API_PASSWORD

CLI

    python dashboard_bridge.py sync            # ส่งเท่าที่ยังไม่เคยส่ง/ผลเปลี่ยน
    python dashboard_bridge.py sync --limit 50
    python dashboard_bridge.py status
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

DB_PATH = os.getenv(
    "DATABASE_PATH", os.path.join(os.getenv("DATA_DIR", "data"), "assistant.db")
)

# ค่าเริ่มต้นของการซิงก์ครั้งเดียว — ใหญ่พอจะไล่ backlog ได้ในไม่กี่รอบ
# เล็กพอที่ความผิดพลาดจะไม่กินเวลาเป็นสิบนาทีก่อนจะรู้ตัว
DEFAULT_LIMIT = int(os.getenv("DASHBOARD_SYNC_LIMIT", "200"))

REQUEST_TIMEOUT = float(os.getenv("DASHBOARD_TIMEOUT", "15"))

# เส้นทางฝั่ง dashboard — Caddy ตัดคำนำหน้า /api ออกก่อนส่งต่อให้ API
INGEST_PATH = "/v1/renovation/messages:ingest"

# ตารางของสะพานนี้เอง ไม่แตะ schema หลัก — ถอดสะพานออกก็แค่ทิ้งตารางนี้
_SCHEMA = """
CREATE TABLE IF NOT EXISTS dashboard_sync (
  message_id  INTEGER PRIMARY KEY REFERENCES chat_messages(id) ON DELETE CASCADE,
  verdict     TEXT,        -- intent|urgency|confidence ตอนที่ส่งสำเร็จครั้งล่าสุด
  remote_id   TEXT,
  synced_at   TEXT NOT NULL
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    with conn:
        conn.executescript(_SCHEMA)


def is_configured() -> bool:
    return bool(os.getenv("DASHBOARD_API_URL"))


def _auth_header() -> Dict[str, str]:
    """Basic auth ของ Caddy ที่กั้นหน้า /api อยู่

    ไม่มี user/password ก็ยังส่ง — เผื่อมีคนวางสะพานนี้หลัง reverse proxy ที่
    ไม่ได้กั้น การให้ 401 กลับมาแล้วบอกตรง ๆ ดีกว่าปฏิเสธตั้งแต่ยังไม่ลอง
    """
    user = os.getenv("DASHBOARD_API_USER", "")
    password = os.getenv("DASHBOARD_API_PASSWORD", "")
    if not user:
        return {}
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": "Basic " + token}


def verdict_of(row: sqlite3.Row) -> str:
    """ลายนิ้วมือของผลคัดแยก ใช้ตัดสินว่าต้องส่งซ้ำไหม

    เก็บเป็นสตริงเพราะมันไปนั่งใน SQLite และถูกเทียบตรง ๆ อย่างเดียว
    ไม่ได้เอาไปคำนวณต่อ
    """
    return "|".join(
        "" if row[key] is None else str(row[key])
        for key in ("intent", "urgency", "confidence")
    )


def messages_to_sync(conn: sqlite3.Connection, limit: int) -> List[sqlite3.Row]:
    """ข้อความที่ยังไม่เคยส่ง หรือส่งไปแล้วแต่ผลคัดแยกเปลี่ยนไป

    ใหม่ก่อนเก่า — ถ้า backlog ยาวกว่าที่ส่งได้ในรอบเดียว ของที่เพิ่งคุยกันควร
    ขึ้นไปถึงก่อน
    """
    ensure_schema(conn)
    rows = conn.execute(
        """
        SELECT m.id, m.body, m.direction, m.sent_at,
               m.intent, m.urgency, m.confidence,
               t.platform, t.title AS room_name,
               p.name AS site_name,
               c.display_name AS author,
               s.verdict AS synced_verdict
          FROM chat_messages m
          JOIN chat_threads t ON t.id = m.thread_id
     LEFT JOIN projects p ON p.id = COALESCE(m.project_id, t.project_id)
     LEFT JOIN contacts c ON c.id = m.contact_id
     LEFT JOIN dashboard_sync s ON s.message_id = m.id
         WHERE m.body IS NOT NULL AND TRIM(m.body) <> ''
         ORDER BY m.sent_at DESC
        """
    ).fetchall()

    pending = [row for row in rows if row["synced_verdict"] != verdict_of(row)]
    return pending[:limit]


def payload_for(row: sqlite3.Row) -> Dict[str, Any]:
    """แปลงแถวของเราให้เป็นรูปที่ /v1/renovation/messages:ingest รับ

    site_hint ส่งชื่อไซต์ไปตรง ๆ แล้วให้ฝั่งโน้นจับคู่เอง สองระบบตั้งชื่อที่
    เดียวกันคนละแบบและจะเป็นแบบนั้นตลอด — ที่นั่นมีตารางชื่อเล่นและมีสิทธิ์
    ตอบว่า "ไม่รู้" ซึ่งดีกว่าให้ที่นี่เดาแล้วส่งไซต์ผิดขึ้นไป
    """
    payload: Dict[str, Any] = {
        "source": row["platform"],
        "external_id": str(row["id"]),
        "body": row["body"],
        "direction": row["direction"],
        "sent_at": row["sent_at"],
    }
    for key, column in (
        ("room_name", "room_name"),
        ("site_hint", "site_name"),
        ("author", "author"),
        ("intent", "intent"),
        ("urgency", "urgency"),
    ):
        if row[column] is not None:
            payload[key] = row[column]
    if row["confidence"] is not None:
        payload["confidence"] = float(row["confidence"])
    return payload


class DashboardUnavailable(RuntimeError):
    """ปลายทางตอบไม่ได้ — ข้อความยังอยู่ครบที่นี่ ค่อยส่งรอบหน้า"""


def endpoint_url() -> str:
    """ที่อยู่ที่สะพานนี้ยิงไป ในรูปที่เอาไปโชว์ผู้ใช้ได้

    ตัด user:pass ที่อาจฝังมาใน URL ออกก่อนเสมอ — รูปแบบ
    https://user:pass@host/api ใช้ได้จริงและใครก็ตั้งแบบนั้นได้ ถ้าไม่ตัด
    รหัสจะไปโผล่ในแชต Telegram ค้างอยู่ในประวัติ ซึ่งเป็นบั๊กเดียวกับ #24
    ที่เราเพิ่งอุดไป

    คืนสตริงว่างเมื่อยังไม่ได้ตั้งค่า ผู้เรียกจะได้ไม่รายงานที่อยู่ที่ไม่มีจริง
    ตอนที่ยังไม่มีการยิงอะไรออกไปเลย
    """
    base = (os.getenv("DASHBOARD_API_URL") or "").rstrip("/")
    if not base:
        return ""
    parts = urllib.parse.urlsplit(base)
    if parts.username or parts.password:
        host = parts.hostname or ""
        if parts.port:
            host = host + ":" + str(parts.port)
        base = urllib.parse.urlunsplit(
            (parts.scheme, host, parts.path, parts.query, parts.fragment)
        )
    return base + INGEST_PATH


def push(payload: Dict[str, Any]) -> Optional[str]:
    """ส่งข้อความเดียว คืน id ฝั่งโน้น

    โยน DashboardUnavailable เมื่อส่งไม่ได้ ผู้เรียกเป็นคนตัดสินว่าจะหยุดหรือไป
    ต่อ — ที่นี่ไม่กลืนความผิดพลาดเงียบ ๆ เพราะการซิงก์ที่ล้มเหลวทั้งรอบแล้ว
    รายงานว่าสำเร็จ คือสิ่งที่ทำให้ข้อความ 325 ข้อความหายไปโดยไม่มีใครรู้มาแล้ว
    """
    base = (os.getenv("DASHBOARD_API_URL") or "").rstrip("/")
    if not base:
        raise DashboardUnavailable("ยังไม่ได้ตั้ง DASHBOARD_API_URL")

    request = urllib.request.Request(
        base + INGEST_PATH,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **_auth_header()},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # ไม่เอาเนื้อคำตอบของเซิร์ฟเวอร์ออกไปไหน มันมี header ของ auth ปนได้
        raise DashboardUnavailable("HTTP " + str(exc.code)) from exc
    except Exception as exc:
        raise DashboardUnavailable(type(exc).__name__) from exc
    return body.get("id")


def record_synced(
    conn: sqlite3.Connection, message_id: int, verdict: str, remote_id: Optional[str]
) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO dashboard_sync (message_id, verdict, remote_id, synced_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT (message_id) DO UPDATE SET
                verdict = excluded.verdict,
                remote_id = excluded.remote_id,
                synced_at = excluded.synced_at
            """,
            (message_id, verdict, remote_id),
        )


def sync(conn: sqlite3.Connection, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
    """ส่งเท่าที่ค้าง คืนสรุปที่บอกความจริงได้แม้ตอนล้มเหลว

    หยุดทันทีที่ส่งไม่ผ่าน ไม่ไล่ยิงต่อจนครบ — ถ้าปลายทางล่ม ข้อความที่เหลือก็
    ล่มเหมือนกัน การยิงต่ออีกร้อยครั้งได้แค่ทำให้รอนานขึ้นแล้วได้คำตอบเดิม
    """
    rows = messages_to_sync(conn, limit)
    sent, failed_reason = 0, None
    for row in rows:
        try:
            remote_id = push(payload_for(row))
        except DashboardUnavailable as exc:
            failed_reason = str(exc)
            logger.error("ส่งขึ้น dashboard ไม่สำเร็จที่ข้อความ %s: %s", row["id"], exc)
            break
        record_synced(conn, int(row["id"]), verdict_of(row), remote_id)
        sent += 1

    return {
        "pending": len(rows),
        "sent": sent,
        "left": len(rows) - sent,
        "error": failed_reason,
        # ที่อยู่ที่ยิงไปจริง ๆ — 404 อย่างเดียวแยกไม่ออกว่า URL ตั้งผิด
        # (เช่นตก /api) หรือปลายทางไม่มี endpoint นั้น
        # None เมื่อไม่ได้ตั้งค่า: ตอนนั้นยังไม่มีการยิงอะไรออกไป จะรายงาน
        # ที่อยู่ที่ไม่มีจริงไม่ได้
        "endpoint": (endpoint_url() if failed_reason else "") or None,
    }


def status(conn: sqlite3.Connection) -> Dict[str, Any]:
    ensure_schema(conn)
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM chat_messages WHERE body IS NOT NULL AND TRIM(body) <> ''"
    ).fetchone()["n"]
    synced = conn.execute("SELECT COUNT(*) AS n FROM dashboard_sync").fetchone()["n"]
    return {
        "configured": is_configured(),
        "url": os.getenv("DASHBOARD_API_URL", ""),
        "messages": total,
        "synced": synced,
        "pending": len(messages_to_sync(conn, total or 1)),
    }


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("sync", "status"))
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--db", default=DB_PATH)
    args = parser.parse_args(argv)

    conn = _connect(args.db)
    try:
        result = sync(conn, args.limit) if args.command == "sync" else status(conn)
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
