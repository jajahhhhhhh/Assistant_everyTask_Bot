"""
คัดแยกเจตนาของข้อความด้วยโมเดล แทนกฎ regex

ทำไมต้องมี: ตัวคัดแยกเดิมเป็น regex ล้วน มันจับคำ ไม่ได้เข้าใจประโยค ผลจริงจาก
ประวัติแชทงานรีโนเวตของเจ้าของ — 80 จาก 99 ข้อความขาเข้าถูกจัดเป็น smalltalk
ทั้งที่เนื้อหาเป็นราคางาน ใบเสนอราคา และข้อตกลงงวดเงิน ประโยคอย่าง
"ยอดงาน 30,000 แจ้งเบิก 15,000" ไม่มีคำว่า "ขอ" หรือ "ช่วย" จึงไม่เข้ากฎไหนเลย

หลักการของไฟล์นี้: **กฎคือพื้น โมเดลคือส่วนเพิ่ม** ทุกเส้นทางที่โมเดลตอบไม่ได้
ตอบช้า ตอบผิดรูป หรือตอบค่าที่ schema ไม่รับ จะตกกลับไปใช้ผลของ regex เสมอ
ไม่มีทางที่ข้อความจะไม่ถูกคัดแยกเพราะโมเดลล่ม

ค่าที่คืนต้องผ่าน CHECK ของ chat_messages ได้เสมอ:
    intent   ∈ request | promise | question | decision | smalltalk
    urgency  ∈ low | normal | high
    confidence ∈ [0, 1]
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence

import line_webhook

logger = logging.getLogger(__name__)

MODEL = os.getenv("CLASSIFIER_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))

# ต้องตรงกับ CHECK ใน sql/01_schema.sql — ถ้าที่นั่นเปลี่ยน ที่นี่ต้องเปลี่ยนตาม
INTENTS = ("request", "promise", "question", "decision", "smalltalk")
URGENCIES = ("low", "normal", "high")

# กันไม่ให้ข้อความยาวผิดปกติกินโควตาไปทั้งก้อน ตัดที่ความยาวที่ยังอ่านเจตนาออก
MAX_CHARS = 500

# ส่งทีละก้อนเพื่อไม่ให้เสียค่า overhead ต่อข้อความ และไม่ให้ก้อนใหญ่จนตอบพัง
BATCH_SIZE = int(os.getenv("CLASSIFIER_BATCH", "25"))

_SYSTEM_PROMPT = """คุณคือตัวคัดแยกเจตนาของข้อความแชทงานก่อสร้าง/รีโนเวตภาษาไทย
ผู้พูดคือเจ้าของงานกับผู้รับเหมา

จัดแต่ละข้อความเป็นหนึ่งใน:
- request  ขอให้อีกฝ่ายทำ/ส่ง/จัดหาอะไรบางอย่าง รวมถึงการสั่งของและการนัดหมาย
- promise  ผู้พูดรับปากว่าจะทำเอง
- question ถามเพื่อขอข้อมูล
- decision ตกลง อนุมัติ ยืนยันราคา หรือสรุปข้อสรุป รวมถึงการแจ้งยอดเงินที่ตกลงกัน
- smalltalk ทักทาย ขอบคุณ รับทราบ หรือข้อความที่ไม่มีเนื้องาน

ข้อความที่มีตัวเลขราคา ยอดเงิน ใบเสนอราคา หรืองวดงาน เกือบไม่เคยเป็น smalltalk
ให้ดูว่ามันคือการขอ (request) การรับปาก (promise) หรือการสรุป/อนุมัติ (decision)

urgency: high เมื่อมีคำบอกความเร่ง หรือมีกำหนดเวลาใกล้; low เมื่อบอกว่าไม่รีบ;
นอกนั้น normal

confidence คือความมั่นใจจริงของคุณ 0–1 ระบบใช้ 0.80 เป็นเส้นตัด ต่ำกว่านั้นจะถูก
มองข้าม จึงอย่าให้คะแนนสูงเกินจริงเพื่อให้ผ่านเส้น

ตอบเป็น JSON เท่านั้น: {"results": [{"i": <เลขข้อความ>, "intent": "...",
"urgency": "...", "confidence": 0.0}]} ต้องมีครบทุกเลขที่ได้รับ"""


def _clean(result: Any) -> Optional[Dict[str, Any]]:
    """ยอมรับเฉพาะผลที่ schema เก็บได้จริง นอกนั้นทิ้ง

    โมเดลตอบอะไรกลับมาก็ได้ ถ้าปล่อยผ่านไปถึง INSERT ค่าที่ผิดจะทำให้ CHECK
    ของ sqlite ปฏิเสธทั้งแถว แล้วข้อความนั้นจะหายไปเลย
    """
    if not isinstance(result, dict):
        return None
    intent = result.get("intent")
    urgency = result.get("urgency")
    if intent not in INTENTS or urgency not in URGENCIES:
        return None
    try:
        confidence = float(result.get("confidence"))
    except (TypeError, ValueError):
        return None
    if not 0.0 <= confidence <= 1.0:
        return None
    return {"intent": intent, "urgency": urgency, "confidence": confidence}


def _rules(text: Optional[str]) -> Dict[str, Any]:
    return line_webhook.classify_message(text)


def _client():
    """คืน client ของ OpenAI ถ้าตั้งค่าไว้ ไม่งั้นคืน None

    import ในฟังก์ชันเพื่อให้ไฟล์นี้ import ได้แม้ยังไม่ได้ติดตั้ง openai —
    เทสต์ส่วนใหญ่ไม่ต้องใช้ และ regex ก็ยังทำงานได้โดยไม่มีมัน
    """
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("ไม่มีไลบรารี openai — ใช้กฎแทน")
        return None
    return OpenAI(api_key=api_key)


def classify_batch_sync(texts: Sequence[str]) -> List[Dict[str, Any]]:
    """คัดแยกหลายข้อความในการเรียกครั้งเดียว คืนผลเรียงตามลำดับที่ส่งเข้า

    ทุกช่องที่โมเดลตอบไม่ได้หรือตอบไม่ผ่านการตรวจ จะถูกเติมด้วยผลของ regex
    ความยาวของผลลัพธ์เท่ากับความยาวของ texts เสมอ
    """
    fallback = [_rules(t) for t in texts]
    if not texts:
        return fallback

    client = _client()
    if client is None:
        return fallback

    numbered = [
        {"i": index, "text": (text or "")[:MAX_CHARS]}
        for index, text in enumerate(texts)
    ]
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(numbered, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        payload = json.loads(response.choices[0].message.content)
    except Exception as exc:
        # ไม่เอาข้อความของผู้ให้บริการออกไปไหน มันไปโผล่ในแชตผู้ใช้ได้ (ดู #24)
        logger.error("ตัวคัดแยกด้วยโมเดลล้มเหลว — ใช้กฎแทน: %s", exc, exc_info=exc)
        return fallback

    results = list(fallback)
    seen = 0
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("i"))
        except (TypeError, ValueError):
            continue
        if not 0 <= index < len(results):
            continue
        cleaned = _clean(item)
        if cleaned is not None:
            results[index] = cleaned
            seen += 1

    if seen < len(texts):
        logger.info(
            "โมเดลตอบไม่ครบ %s จาก %s ข้อความ — ที่เหลือใช้กฎ",
            len(texts) - seen, len(texts),
        )
    return results


def classify_all_sync(texts: Sequence[str]) -> List[Dict[str, Any]]:
    """เหมือน classify_batch_sync แต่หั่นเป็นก้อนตาม BATCH_SIZE ให้เอง"""
    results: List[Dict[str, Any]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        results.extend(classify_batch_sync(texts[start:start + BATCH_SIZE]))
    return results


async def classify_message(text: Optional[str]) -> Dict[str, Any]:
    """ตัวคัดแยกข้อความเดียว รูปแบบเดียวกับ line_webhook.classify_message

    เป็น coroutine เพราะ LineWebhookHandler รองรับตัวคัดแยกแบบ async อยู่แล้ว
    (มี timeout และตกกลับไปใช้กฎให้เอง — ดู _classify ใน line_webhook.py)
    """
    import asyncio

    body = (text or "").strip()
    if not body:
        return {"intent": None, "urgency": None, "confidence": None}
    results = await asyncio.to_thread(classify_batch_sync, [body])
    return results[0]
