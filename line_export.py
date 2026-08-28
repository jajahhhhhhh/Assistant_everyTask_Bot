"""
อ่านไฟล์ประวัติแชทที่ export จากแอป LINE แล้วนำเข้าตารางกลาง

ทำไมต้องมีไฟล์นี้: webhook รับได้เฉพาะข้อความที่เข้ามา "หลังจาก" ต่อ webhook แล้ว
บทสนทนาก่อนหน้านั้นทั้งหมดไม่มีทางเข้าระบบเลย ไฟล์ export คือทางเดียวที่จะเอา
ประวัติเก่ากลับมา

แยกตัว parse ออกจากตัวเขียนฐานข้อมูลโดยตั้งใจ — parse_export() รับข้อความล้วน
คืนโครงสร้างล้วน ทดสอบได้โดยไม่ต้องมีฐานข้อมูล ส่วน import_export() เป็นตัวเดียว
ที่แตะ SQLite

หมายเหตุเรื่องรูปแบบไฟล์: LINE เปลี่ยนรูปแบบตามภาษาและเวอร์ชันของแอป โค้ดนี้จึง
ยอมรับหลายรูปแบบ และ **คืนบรรทัดที่อ่านไม่ออกกลับมาเสมอ** ไม่ทิ้งเงียบ ๆ ผู้เรียก
ต้องรายงานจำนวนนั้นให้ผู้ใช้เห็น ไม่งั้นไฟล์ที่ parse ได้ครึ่งเดียวจะดูเหมือน
สำเร็จเต็มร้อย
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import line_webhook

PLATFORM = "line"

# ── หัวไฟล์ ────────────────────────────────────────────────────────────────────

# "[LINE] Chat with Farid" / "[LINE] แชทกับ Farid" / "[LINE] Chat in ชื่อกลุ่ม"
_TITLE_RE = re.compile(
    r"^\[LINE\]\s*(?:Chat (?:with|in)|แชทกับ|แชทใน)\s*(?P<title>.+?)\s*$"
)
# บรรทัด "Saved on:" / "บันทึกเมื่อ:" ไม่มีข้อมูลที่ต้องใช้ ข้ามได้
_SAVED_RE = re.compile(r"^(?:Saved on|บันทึกเมื่อ)\s*[:：]")

# ── บรรทัดวันที่ ───────────────────────────────────────────────────────────────

# 2026/08/25, 2026-08-25, 2026.08.25 — อาจมีชื่อวันต่อท้ายในวงเล็บ
_DATE_YMD_RE = re.compile(
    r"^(?P<y>\d{4})[/\-.](?P<m>\d{1,2})[/\-.](?P<d>\d{1,2})"
    r"\s*(?:\(.*\)|[^\d]{1,20})?\s*$"
)
# 25/08/2026 — วันขึ้นก่อน (ไทยและยุโรปใช้แบบนี้) อาจมีชื่อวันนำหน้าหรือต่อท้าย
_DATE_DMY_RE = re.compile(
    r"^(?:[^\d,]{1,12},\s*)?(?P<d>\d{1,2})[/\-.](?P<m>\d{1,2})[/\-.](?P<y>\d{4})"
    r"\s*(?:\(.*\))?\s*$"
)

# ── บรรทัดข้อความ ──────────────────────────────────────────────────────────────

# เวลา แล้วชื่อ แล้วเนื้อความ — คั่นด้วย tab เป็นหลัก ถ้า tab หายก็ยอมรับช่องว่างซ้ำ
_MESSAGE_RE = re.compile(
    r"^(?P<time>\d{1,2}[:.]\d{2})\s*(?P<ampm>AM|PM|am|pm)?"
    r"(?:\t+|\s{2,})(?P<sender>[^\t]{1,80}?)(?:\t+|\s{2,})(?P<body>.*)$"
)
# บางรุ่นมีแค่เวลากับเนื้อความ (แชทเดี่ยวที่ไม่ใส่ชื่อ) — ไม่รู้ผู้ส่ง ต้องข้าม
_TIME_ONLY_RE = re.compile(r"^(?P<time>\d{1,2}[:.]\d{2})(?:\s*(?:AM|PM|am|pm))?\s*$")

# บางรุ่นคั่นด้วยช่องว่างเดียว ("10:37 MR.HOME KOH SAMUI ครับผม") ตัดด้วยตำแหน่ง
# ไม่ได้เพราะชื่อคนมีช่องว่างได้ จับแค่เวลากับส่วนที่เหลือ แล้วให้ _learn_senders
# หาว่าชื่อจบตรงไหน
_MESSAGE_LOOSE_RE = re.compile(
    r"^(?P<time>\d{1,2}[:.]\d{2})\s*(?P<ampm>AM|PM|am|pm)?[ ]+(?P<tail>\S.*)$"
)

# เพดานของชื่อผู้ส่งที่ยอมให้เดา กันกรณีที่ความถี่หลอกให้กินเนื้อความไปทั้งบรรทัด
_MAX_SENDER_TOKENS = 6
_MAX_SENDER_CHARS = 80

# ปีพุทธศักราชในไฟล์ที่ export จากแอปภาษาไทย
_BUDDHIST_OFFSET = 543
_BUDDHIST_MIN_YEAR = 2400


@dataclass
class ParsedMessage:
    """ข้อความหนึ่งบรรทัดในไฟล์ export"""

    sent_at: str        # ISO 8601 ตามเวลาที่เขียนในไฟล์ ไม่มี timezone
    sender: str
    body: str
    line_no: int


@dataclass
class ParsedExport:
    """ผลการอ่านไฟล์ — รวมสิ่งที่อ่านไม่ออกไว้ด้วยเสมอ"""

    title: Optional[str] = None
    messages: List[ParsedMessage] = field(default_factory=list)
    skipped: List[Tuple[int, str]] = field(default_factory=list)

    @property
    def senders(self) -> List[str]:
        """รายชื่อผู้ส่งที่พบ เรียงตามจำนวนข้อความจากมากไปน้อย"""
        counts: Dict[str, int] = {}
        for message in self.messages:
            counts[message.sender] = counts.get(message.sender, 0) + 1
        return sorted(counts, key=lambda name: (-counts[name], name))


def _normalise_year(year: int) -> int:
    """แปลง พ.ศ. เป็น ค.ศ. — แอป LINE ภาษาไทย export ปีพุทธมา"""
    return year - _BUDDHIST_OFFSET if year >= _BUDDHIST_MIN_YEAR else year


def _parse_date_line(line: str) -> Optional[Tuple[int, int, int]]:
    match = _DATE_YMD_RE.match(line) or _DATE_DMY_RE.match(line)
    if not match:
        return None
    year = _normalise_year(int(match.group("y")))
    month = int(match.group("m"))
    day = int(match.group("d"))
    try:
        datetime(year, month, day)
    except ValueError:
        return None          # 2026/13/45 ไม่ใช่วันที่ ปล่อยให้ไปเป็นบรรทัดอื่น
    return year, month, day


def _parse_time(raw: str, ampm: Optional[str]) -> Optional[Tuple[int, int]]:
    hour_text, minute_text = re.split(r"[:.]", raw)
    hour, minute = int(hour_text), int(minute_text)
    if ampm:
        marker = ampm.lower()
        if marker == "pm" and hour != 12:
            hour += 12
        elif marker == "am" and hour == 12:
            hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def _clean(raw_line: str) -> str:
    """ตัดสิ่งที่มองไม่เห็นออกจากบรรทัด

    ไฟล์จาก LINE บน Windows มี \r ติดมา และบางรุ่นแทรก zero-width space
    (หมวด Cf) ระหว่างตัวอักษร ถ้าไม่ตัดออก regex จะไม่ match โดยที่ตาเปล่า
    มองไม่เห็นว่าเพราะอะไร
    """
    line = raw_line.replace("\r", "")
    return "".join(ch for ch in line if unicodedata.category(ch) != "Cf")


def title_from_filename(file_name: Optional[str]) -> Optional[str]:
    """ดึงชื่อห้องจากชื่อไฟล์ เผื่อไฟล์ไม่มีบรรทัดหัวเรื่อง

    LINE บางรุ่นไม่เขียนบรรทัด "[LINE] Chat with ..." ลงในไฟล์เลย เหลือชื่อห้อง
    อยู่แค่ในชื่อไฟล์ ("[LINE] Chat with MR.HOME.txt") ถ้าไม่ใช้ตรงนี้ ห้องที่
    นำเข้าจะไม่มีชื่อ และ guess_owner() ซึ่งอาศัยชื่อห้องก็เดาไม่ได้ไปด้วย
    """
    if not file_name:
        return None
    stem = re.sub(r"\.txt$", "", file_name.strip(), flags=re.IGNORECASE)
    match = _TITLE_RE.match(stem)
    if match:
        return match.group("title").strip()
    stem = stem.strip()
    return stem or None


def _learn_senders(tails: List[str]) -> List[str]:
    """เดาชุดชื่อผู้ส่งจากบรรทัดที่คั่นด้วยช่องว่างเดียว

    ปัญหา: "10:37 MR.HOME KOH SAMUI ครับผม" ตัดตรงช่องว่างที่เท่าไรก็ผิดได้หมด
    เพราะชื่อคนมีช่องว่างอยู่ข้างใน

    ทางออกคือใช้ความถี่ ชื่อผู้ส่งซ้ำทุกบรรทัดที่คนนั้นพิมพ์ ส่วนคำแรกของเนื้อความ
    ไม่ซ้ำแบบนั้น จึงขยาย prefix ทีละคำตราบใดที่ "จำนวนครั้งไม่ลดลง" พอจำนวนลด
    แปลว่าเลยชื่อไปแตะเนื้อความแล้ว — "MR.HOME"(172) → "MR.HOME KOH"(172) →
    "MR.HOME KOH SAMUI"(172) → "MR.HOME KOH SAMUI รูป"(66) จึงหยุดที่สามคำ

    ตัวที่โผล่ครั้งเดียวเดาไม่ได้ ไม่มีอะไรให้เทียบความถี่ ปล่อยให้ไปกอง
    ในบรรทัดที่อ่านไม่ออกดีกว่าเดาแล้วตัดชื่อผิด
    """
    counts: Dict[str, int] = {}
    children: Dict[str, set] = {}
    for tail in tails:
        # split() ไม่ใช่ split(" ") — ช่องว่างสองตัวติดกันทำให้ split(" ") คืน token
        # ว่างออกมา แล้ว token ว่างนั้นถูกต่อเป็นชื่อ ได้ชื่อที่ลงท้ายด้วยช่องว่าง
        tokens = tail.split()
        for size in range(1, min(len(tokens), _MAX_SENDER_TOKENS) + 1):
            prefix = " ".join(tokens[:size])
            if len(prefix) > _MAX_SENDER_CHARS:
                break
            counts[prefix] = counts.get(prefix, 0) + 1
            if size < len(tokens):
                children.setdefault(prefix, set()).add(tokens[size])

    learned = []
    for head in {tail.split()[0] for tail in tails if tail.split()}:
        if counts.get(head, 0) < 2:
            continue
        name = head
        while True:
            following = children.get(name)
            if not following or len(following) != 1:
                break
            candidate = f"{name} {next(iter(following))}"
            if counts.get(candidate) != counts[name]:
                break
            if len(candidate.split()) > _MAX_SENDER_TOKENS:
                break
            if len(candidate) > _MAX_SENDER_CHARS:
                break
            name = candidate
        # ไม่เคยมีอะไรตามหลังเลยสักครั้ง = เป็นบรรทัดระบบทั้งบรรทัด ไม่ใช่ชื่อคน
        # ("12:14 ยกเลิกข้อความแล้ว" ซ้ำสามครั้ง ไม่มีเนื้อความต่อท้ายเลย)
        if name not in children:
            continue
        learned.append(name)
    return learned


def _sender_pattern(name: str) -> "re.Pattern":
    """regex ของชื่อหนึ่งชื่อ ที่ยอมให้ช่องว่างระหว่างคำเป็นกี่ตัวก็ได้

    ชื่อที่เรียนมาถูกประกอบใหม่ด้วยช่องว่างเดียวเสมอ แต่บรรทัดดิบอาจมีสองตัว
    เทียบด้วย startswith ตรง ๆ จะไม่ match แล้วบรรทัดนั้นกลายเป็นอ่านไม่ออก
    """
    spaced = r"[ \t]+".join(re.escape(token) for token in name.split())
    return re.compile(rf"{spaced}(?:[ \t]+(?P<body>.*))?$")


def _split_by_sender(tail: str, senders: List[str]) -> Optional[Tuple[str, str]]:
    """ตัดชื่อผู้ส่งออกจากหัวบรรทัด ลองชื่อยาวก่อนเสมอ

    ถ้าชื่อหนึ่งเป็นคำขึ้นต้นของอีกชื่อ ("Ann" กับ "Ann Lee") การลองชื่อสั้นก่อน
    จะตัดผิดและเอาส่วนที่เหลือของชื่อไปนับเป็นเนื้อความ
    """
    for name in senders:
        match = _sender_pattern(name).match(tail)
        if match:
            return name, (match.group("body") or "")
    return None


def _prescan(text: str) -> Tuple[List[str], List[str]]:
    """กวาดทั้งไฟล์หนึ่งรอบก่อน parse จริง คืน (ชื่อที่รู้แน่, ส่วนหลังเวลาที่ยังไม่รู้)

    ต้องกวาดให้จบก่อนเพราะชื่อผู้ส่งในรูปแบบช่องว่างเดียวเดาได้จากความถี่ทั้งไฟล์
    เท่านั้น บรรทัดเดียวบอกไม่ได้ว่าชื่อจบตรงไหน

    บรรทัดที่คั่นด้วย tab ตัดได้แน่นอนอยู่แล้ว ชื่อจากบรรทัดพวกนั้นจึงเชื่อถือได้
    เอามาสมทบเป็นคำใบ้ให้บรรทัดที่คั่นด้วยช่องว่างเดียวในไฟล์เดียวกันด้วย
    """
    known: List[str] = []
    tails: List[str] = []
    for raw_line in text.splitlines():
        line = _clean(raw_line)
        if not line.strip():
            continue
        strict = _MESSAGE_RE.match(line)
        if strict:
            known.append(strict.group("sender").strip())
            continue
        if _parse_date_line(line.strip()):
            continue
        match = _MESSAGE_LOOSE_RE.match(line)
        if match:
            tails.append(match.group("tail").strip())
    return known, tails


def parse_export(text: str, *, fallback_title: Optional[str] = None) -> ParsedExport:
    """อ่านไฟล์ export ทั้งไฟล์

    บรรทัดที่ไม่เข้ารูปแบบไหนเลยจะถูกเก็บไว้ใน .skipped พร้อมเลขบรรทัด ไม่ถูกทิ้ง
    เงียบ ๆ เพราะไฟล์ที่ parse ได้ครึ่งเดียวหน้าตาเหมือนไฟล์ที่สำเร็จทุกประการ

    fallback_title ใช้เมื่อไฟล์ไม่มีบรรทัดหัวเรื่อง — ปกติส่งชื่อไฟล์เข้ามา
    """
    result = ParsedExport()
    current_date: Optional[Tuple[int, int, int]] = None

    # รูปแบบคั่นด้วย tab อ่านได้ทีละบรรทัด แต่รูปแบบคั่นด้วยช่องว่างเดียวต้องรู้
    # ชุดชื่อผู้ส่งก่อนถึงจะตัดถูก จึงกวาดหาชื่อให้ครบก่อนหนึ่งรอบ
    known_senders, loose_tails = _prescan(text)
    loose_senders = sorted(
        set(_learn_senders(loose_tails)) | set(known_senders), key=len, reverse=True
    )

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = _clean(raw_line)

        if not line.strip():
            continue

        if result.title is None:
            title_match = _TITLE_RE.match(line)
            if title_match:
                result.title = title_match.group("title").strip()
                continue
        if _SAVED_RE.match(line):
            continue

        parsed_date = _parse_date_line(line.strip())
        if parsed_date:
            current_date = parsed_date
            continue

        # ลองรูปแบบที่คั่นชัดเจน (tab หรือช่องว่างตั้งแต่สองตัว) ก่อนเสมอ
        # เพราะไม่ต้องเดาอะไรเลย แล้วค่อยตกมาที่รูปแบบช่องว่างเดียว
        parts = None
        strict = _MESSAGE_RE.match(line)
        if strict:
            parts = (
                strict.group("time"),
                strict.group("ampm"),
                strict.group("sender").strip(),
                strict.group("body").strip(),
            )
        else:
            loose = _MESSAGE_LOOSE_RE.match(line)
            if loose and loose_senders:
                split = _split_by_sender(loose.group("tail").strip(), loose_senders)
                if split:
                    parts = (loose.group("time"), loose.group("ampm"), *split)

        if parts:
            time_text, ampm, sender, body = parts
            if current_date is None:
                # ข้อความก่อนเจอบรรทัดวันที่ — ไม่มีทางรู้ว่าวันไหน
                result.skipped.append((line_no, line))
                continue
            clock = _parse_time(time_text, ampm)
            if clock is None:
                result.skipped.append((line_no, line))
                continue
            year, month, day = current_date
            hour, minute = clock
            result.messages.append(
                ParsedMessage(
                    sent_at=datetime(year, month, day, hour, minute).isoformat(
                        timespec="seconds"
                    ),
                    sender=sender,
                    body=body.strip(),
                    line_no=line_no,
                )
            )
            continue

        # ขึ้นต้นด้วยเวลาแต่ตัดชื่อผู้ส่งไม่ออก — รู้ว่าเป็นข้อความ แต่ไม่รู้ของใคร
        # ถ้าปล่อยให้ตกไปเป็น "บรรทัดต่อ" ข้างล่าง ข้อความจะไปแปะท้ายของคนอื่น
        if _MESSAGE_LOOSE_RE.match(line):
            result.skipped.append((line_no, line))
            continue

        # บรรทัดที่ขึ้นต้นด้วยเวลาแต่ไม่มีชื่อผู้ส่ง — รู้ว่าเป็นข้อความแต่ไม่รู้ของใคร
        if _TIME_ONLY_RE.match(line.strip()):
            result.skipped.append((line_no, line))
            continue

        # ไม่มีเวลานำหน้า และมีข้อความอยู่ก่อนแล้ว = บรรทัดต่อของข้อความเดิม
        # (ข้อความหลายบรรทัดใน LINE ถูก export แบบนี้)
        if result.messages:
            previous = result.messages[-1]
            previous.body = f"{previous.body}\n{line.strip()}".strip()
            continue

        result.skipped.append((line_no, line))

    if result.title is None:
        result.title = fallback_title

    return result


def guess_owner(export: ParsedExport) -> Optional[str]:
    """เดาว่าชื่อไหนคือเจ้าของเครื่อง

    ใช้ได้เฉพาะแชทเดี่ยวที่มีผู้ส่งสองคนพอดี — ชื่อในหัวไฟล์คืออีกฝ่าย ที่เหลือคือเรา
    แชทกลุ่มเดาไม่ได้ ต้องให้ผู้ใช้บอกมา
    """
    senders = export.senders
    if len(senders) != 2 or not export.title:
        return None
    others = [name for name in senders if name != export.title]
    return others[0] if len(others) == 1 else None


def thread_key(export: ParsedExport) -> str:
    """id ของห้องแชทที่นำเข้า — คงที่สำหรับ "ห้องเดิม" และไม่ชนกับห้องจาก webhook

    ห้องจริงจาก LINE ใช้ chat id ของ LINE ตรง ๆ การเติมคำนำหน้า "import:" ทำให้
    ประวัติที่นำเข้าไม่ไปทับห้องที่กำลังรับข้อความสดอยู่

    คีย์ผูกกับ "ห้อง" ไม่ใช่ "ไฟล์" โดยตั้งใจ คนเรา export ห้องเดิมซ้ำเมื่อมี
    ข้อความใหม่เพิ่ม ถ้าเอาจำนวนข้อความหรือเวลาข้อความแรกมาผสมในคีย์ ไฟล์ที่ยาว
    ขึ้นจะกลายเป็นห้องใหม่ทั้งห้อง แล้วประวัติเก่าทั้งกองจะถูกนำเข้าซ้ำอีกรอบ
    ไฟล์ที่ไม่มีชื่อห้องเลยใช้รายชื่อผู้ส่งแทน ซึ่งคงที่ข้ามการ export เช่นกัน
    """
    seed = (export.title or "").strip()
    if not seed:
        seed = "|".join(sorted({message.sender for message in export.messages}))
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return f"import:{digest}"


def _existing_keys(conn: sqlite3.Connection, thread_id: int) -> Counter:
    """นับข้อความที่มีอยู่แล้วในห้องนี้ ใช้กันการนำเข้าซ้ำ

    ต้อง "นับ" ไม่ใช่แค่ "มีหรือไม่มี" เพราะไฟล์ export บอกเวลาละเอียดแค่ระดับนาที
    คนคนเดียวส่งรูปสามรูปในนาทีเดียวจะได้คีย์เหมือนกันเป๊ะทั้งสามข้อความ ถ้าใช้ set
    จะเหลือรูปเดียว อีกสองรูปหายไปเงียบ ๆ โดยถูกนับเป็น "ซ้ำ" (ไฟล์จริงไฟล์แรก
    ที่เอามาทดสอบ หายไป 76 จาก 722 ข้อความด้วยเหตุนี้)
    """
    rows = conn.execute(
        "SELECT direction, sent_at, body FROM chat_messages WHERE thread_id = ?",
        (thread_id,),
    )
    return Counter((row["direction"], row["sent_at"], row["body"]) for row in rows)


def _upsert_import_contact(
    conn: sqlite3.Connection, display_name: str
) -> Optional[int]:
    """ผู้ส่งจากไฟล์ export

    ไฟล์ export ไม่มี LINE user id มีแค่ชื่อที่แสดง จึงสังเคราะห์ id จากชื่อแทน
    ผลคือคนคนเดียวกันจะได้ contact คนละแถวกับที่ webhook สร้างไว้ ซึ่งตั้งใจ — เรา
    พิสูจน์ไม่ได้ว่าเป็นคนเดียวกัน และการเดาผิดแปลว่าเอาประวัติไปแปะให้ผิดคน
    """
    if not display_name:
        return None
    synthetic_id = f"import:{display_name}"
    return line_webhook.upsert_contact(conn, synthetic_id, display_name)


@dataclass
class ImportResult:
    thread_id: int
    title: Optional[str]
    imported: int = 0
    duplicates: int = 0
    skipped_lines: int = 0
    intents: Dict[str, int] = field(default_factory=dict)


def import_export(
    conn: sqlite3.Connection,
    export: ParsedExport,
    *,
    owner_name: Optional[str] = None,
    is_group: bool = False,
) -> ImportResult:
    """เขียนข้อความที่ parse ได้ลงตารางกลาง — นำเข้าซ้ำไม่เพิ่มแถว

    owner_name คือชื่อของคุณในไฟล์นั้น ข้อความจากชื่อนี้จะถูกบันทึกเป็น direction
    'out' ที่เหลือเป็น 'in' ถ้าไม่ระบุและเดาไม่ได้ ทุกข้อความจะถูกบันทึกเป็น 'in'
    ซึ่งทำให้ตัวเลข "รอเราตอบ" ผิด — ผู้เรียกควรบอกผู้ใช้เมื่อเกิดกรณีนี้
    """
    if not export.messages:
        raise ValueError("ไม่พบข้อความในไฟล์")

    result = ImportResult(thread_id=0, title=export.title,
                          skipped_lines=len(export.skipped))
    last_sent_at = max(message.sent_at for message in export.messages)

    with conn:
        thread = line_webhook.upsert_thread(
            conn,
            thread_key(export),
            is_group=is_group,
            sent_at=last_sent_at,
            title=export.title,
        )
        result.thread_id = int(thread["id"])
        seen = _existing_keys(conn, result.thread_id)
        contacts: Dict[str, Optional[int]] = {}

        for message in export.messages:
            is_owner = owner_name is not None and message.sender == owner_name
            direction = "out" if is_owner else "in"
            # หักออกจากจำนวนที่มีอยู่ก่อนทีละใบ ข้อความที่เกินจำนวนเดิมคือของใหม่
            # ผลคือ นำเข้าไฟล์เดิมซ้ำได้ศูนย์แถวเหมือนเดิม แต่ข้อความที่ซ้ำกันเอง
            # "ภายในไฟล์" ยังเข้าครบทุกใบ
            key = (direction, message.sent_at, message.body)
            if seen[key] > 0:
                seen[key] -= 1
                result.duplicates += 1
                continue

            if is_owner:
                contact_id = None       # ข้อความของเราเอง contact_id เป็น NULL
            else:
                if message.sender not in contacts:
                    contacts[message.sender] = _upsert_import_contact(
                        conn, message.sender
                    )
                contact_id = contacts[message.sender]

            cursor = conn.execute(
                """
                INSERT INTO chat_messages
                    (thread_id, contact_id, direction, body, sent_at, project_id, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.thread_id,
                    contact_id,
                    direction,
                    message.body,
                    message.sent_at,
                    thread["project_id"],
                    json.dumps(
                        {
                            "source": "line_export",
                            "sender": message.sender,
                            "line_no": message.line_no,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            message_id = int(cursor.lastrowid)
            result.imported += 1

            # คัดแยกเฉพาะข้อความที่เข้ามา — ข้อความของเราเองไม่ใช่สิ่งที่ต้องตอบ
            if direction == "in":
                classification = line_webhook.classify_message(message.body)
                line_webhook.apply_classification(conn, message_id, classification)
                intent = classification.get("intent")
                if intent:
                    result.intents[intent] = result.intents.get(intent, 0) + 1

    return result


def import_from_text(
    db_path: str,
    raw_text: str,
    *,
    owner_name: Optional[str] = None,
    file_name: Optional[str] = None,
) -> Tuple[ParsedExport, "ImportResult"]:
    """อ่านและนำเข้าในครั้งเดียว โดยเปิดและปิด connection ภายในฟังก์ชันนี้เอง

    ตัวเชื่อมของ sqlite3 ผูกกับเธรดที่สร้างมัน ถ้าผู้เรียกเปิด connection บนเธรด
    ของ event loop แล้วส่งเข้า asyncio.to_thread จะได้ ProgrammingError ทันที
    ("SQLite objects created in a thread can only be used in that same thread")
    ให้ทั้งวงจรอยู่ในฟังก์ชันเดียวจึงส่งฟังก์ชันนี้เข้า to_thread ได้ตรง ๆ
    """
    export = parse_export(raw_text, fallback_title=title_from_filename(file_name))
    if not export.messages:
        return export, ImportResult(thread_id=0, title=export.title,
                                    skipped_lines=len(export.skipped))

    owner = owner_name if owner_name is not None else guess_owner(export)
    conn = line_webhook.connect(db_path)
    try:
        result = import_export(
            conn,
            export,
            owner_name=owner,
            is_group=len(export.senders) > 2,
        )
    finally:
        conn.close()
    return export, result
