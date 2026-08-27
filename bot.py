"""
Assistant_everyTask_Bot - Enhanced Version
Features: Tasks, Reminders, Notes, Storage Settings, Translation, Voice Transcription
"""

import os
import re
import sqlite3
import logging
import json
import tempfile
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Voice, File
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)
from openai import OpenAI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import aiohttp

import line_webhook
import line_export

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATA_DIR = os.getenv("DATA_DIR", "data")
# ต้องเป็นไฟล์เดียวกับที่ line_webhook.py ใช้ ไม่งั้นสองส่วนเขียนคนละฐาน
DB_PATH = os.getenv("DATABASE_PATH", f"{DATA_DIR}/assistant.db")

# ใช้ connection ตัวเดียวกับฝั่งเว็บ — ทั้งสองเส้นทางเขียนไฟล์เดียวกัน ถ้าตั้งค่า
# ไม่ตรงกันจะได้พฤติกรรมคนละอย่างบนตารางเดียวกัน sqlite3.connect เปล่า ๆ ปิด
# foreign_keys ไว้ (ตารางกลางพึ่ง FK และ ON DELETE CASCADE) และไม่มี busy timeout
# ให้รอเมื่ออีกฝั่งกำลังเขียนอยู่
connect = line_webhook.connect

# Google Drive ใช้ OAuth ของ "เจ้าของบอท" ไม่ใช่ของผู้ใช้แต่ละคน ผู้ใช้แค่กด
# ยินยอมผ่านลิงก์ แล้ว refresh token กลับมาที่เซิร์ฟเวอร์เอง — ไม่มี credential
# ตัวไหนวิ่งผ่านแชต Telegram เลย ซึ่งเป็นเหตุผลเดียวที่ทำแบบนี้แทนวิธีที่ให้
# ผู้ใช้พิมพ์ token ใส่แชต (แบบนั้น token จะค้างในประวัติแชตถาวร)
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
# URL สาธารณะของบริการนี้ ใช้ประกอบ redirect_uri ที่ต้องตรงกับที่ลงทะเบียนไว้
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

logger = logging.getLogger(__name__)
# Silence httpx/httpcore request logs so the bot token never lands in logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# OpenAI client
client = None
if OPENAI_API_KEY:
    client = OpenAI(api_key=OPENAI_API_KEY)

# Supported languages for translation
LANGUAGES = {
    "en": "English", "th": "ไทย", "zh": "中文", "ja": "日本語", "ko": "한국어",
    "vi": "Tiếng Việt", "id": "Bahasa Indonesia", "ms": "Bahasa Melayu",
    "es": "Español", "fr": "Français", "de": "Deutsch", "it": "Italiano",
    "pt": "Português", "ru": "Русский", "uk": "Українська", "ar": "العربية", 
    "hi": "हिंदी", "tl": "Tagalog", "my": "မြန်မာ", "km": "ខ្មែរ", "lo": "ລາວ"
}


# ═══════════════════════════════════════════════════════════════════════════════
# TASK PRIORITY
# ═══════════════════════════════════════════════════════════════════════════════

# คำอังกฤษจับที่ขอบคำ ไม่งั้น "highlight" กลายเป็น high, "flowchart" กับ "pillow"
# กลายเป็น low ส่วนภาษาไทยเขียนติดกันไม่มีช่องว่าง จึงยังต้องจับแบบ substring
_PRIORITY_RULES = (
    ("urgent", (r"\burgent\b", r"\basap\b"), ("ด่วน",)),
    ("high", (r"\bimportant\b", r"\bhigh\b"), ("สำคัญ",)),
    ("low", (r"\blow\b", r"\blater\b"), ("ต่ำ",)),
)


def detect_priority(title: Any) -> str:
    """เดาความสำคัญจากชื่องาน ค่าเริ่มต้นคือ medium"""
    lowered = str(title or "").lower()

    if "!" in lowered:
        return "urgent"

    for priority, patterns, substrings in _PRIORITY_RULES:
        if any(re.search(pattern, lowered) for pattern in patterns):
            return priority
        if any(word in lowered for word in substrings):
            return priority

    return "medium"


# ═══════════════════════════════════════════════════════════════════════════════
# MARKDOWN ESCAPING
# ═══════════════════════════════════════════════════════════════════════════════

# อักขระที่เปิด entity ใน parse_mode="Markdown" (โหมดเก่าของ Telegram)
_MD_SPECIALS = ("_", "*", "`", "[")


def escape_md(text: Any) -> str:
    """ทำให้ข้อความของผู้ใช้ปลอดภัยสำหรับ parse_mode="Markdown"

    Telegram ปฏิเสธ "ทั้งข้อความ" ด้วย 400 Can't parse entities ถ้ามี _ * ` [
    ที่ไม่จับคู่ — งานชื่อ "fix user_id" จึงทำให้ไม่มีคำยืนยันกลับมาเลย และ
    /tasks ที่มีงานแบบนั้นอยู่แถวเดียว จะพังทั้งรายการ ไม่ใช่แค่แถวนั้น

    escape แล้วโครง Markdown ของเราเอง (**หัวข้อ**, `โค้ด`) ยังทำงานปกติ
    ส่วนข้อความของผู้ใช้แสดงตามที่พิมพ์มาจริง
    """
    if text is None:
        return ""
    out = str(text)
    for ch in _MD_SPECIALS:
        out = out.replace(ch, "\\" + ch)
    return out


def escape_code(text: Any) -> str:
    """ทำให้ข้อความปลอดภัยสำหรับใช้ "ใน" code span

    ข้างใน code span มีแค่ backtick เท่านั้นที่ปิด span ก่อนเวลา และ Markdown
    โหมดเก่าไม่มีวิธี escape backtick ข้างในได้ จึงต้องแทนด้วยอย่างอื่น
    """
    if text is None:
        return ""
    return str(text).replace("`", "'")


# ═══════════════════════════════════════════════════════════════════════════════
# ข้อความ error ที่ส่งให้ผู้ใช้
# ═══════════════════════════════════════════════════════════════════════════════

# ข้อความที่ผู้ให้บริการภายนอกใช้บอกว่า credential ไม่ผ่าน
_AUTH_ERROR_MARKERS = (
    "invalid_api_key",
    "incorrect api key",
    "invalid authentication",
    "unauthorized",
)


def _looks_like_auth_error(exc: BaseException) -> bool:
    """แยกกรณี "คีย์ผิด" ออกจาก error อื่น เพื่อบอกทางแก้ให้ตรงจุด

    ดู status code ก่อน เพราะเชื่อถือได้กว่าการอ่านข้อความ ส่วนการค้นข้อความใช้
    เป็นทางสำรองสำหรับไลบรารีที่ไม่ได้แนบ status มาให้ — ข้อความจาก exception ถูก
    "อ่าน" ตรงนี้เท่านั้น ไม่เคยถูกส่งต่อให้ผู้ใช้
    """
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status == 401:
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _AUTH_ERROR_MARKERS)


def api_failure_message(exc: BaseException, action: str, key_name: str = "") -> str:
    """คืนข้อความที่ปลอดภัยสำหรับผู้ใช้ และเก็บรายละเอียดจริงไว้ใน log

    เดิมโค้ดส่ง str(exc) กลับไปให้ผู้ใช้ตรง ๆ ซึ่งพาของที่ไม่ควรออกไปด้วย —
    ตอน OPENAI_API_KEY ผิด OpenAI ตอบกลับมาพร้อมคีย์ที่มาสก์ไว้บางส่วน แล้วบอท
    ก็เอาไปโพสต์ลงแชต Telegram ให้ค้างอยู่ในประวัติ ข้อความจากผู้ให้บริการภายนอก
    ไม่ควรถูกส่งต่อดิบ ๆ ไม่ว่ากรณีใด
    """
    # ส่ง exc เข้า exc_info ตรง ๆ ไม่ใช่ logger.exception() ที่อ่านจาก sys.exc_info()
    # — แบบนั้นได้รายละเอียดเฉพาะตอนถูกเรียกจากใน except block ถ้าวันหนึ่งมีใคร
    # เรียกจากที่อื่น รายละเอียดจะหายเงียบ ๆ ซึ่งเป็นสิ่งเดียวกับที่ฟังก์ชันนี้
    # มีไว้เพื่อป้องกัน
    logger.error("%s ไม่สำเร็จ: %s", action, exc, exc_info=exc)
    if _looks_like_auth_error(exc):
        if key_name:
            return f"❌ {action}ไม่สำเร็จ — {key_name} ใช้ไม่ได้ ผู้ดูแลบอทต้องตรวจสอบ"
        return f"❌ {action}ไม่สำเร็จ — credential ใช้ไม่ได้"
    return f"❌ {action}ไม่สำเร็จ ลองใหม่อีกครั้ง (รายละเอียดอยู่ใน log ของเซิร์ฟเวอร์)"


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE SETUP
# ═══════════════════════════════════════════════════════════════════════════════

def _add_missing_columns(cursor):
    """เติมคอลัมน์ที่เพิ่มเข้ามาทีหลังให้ฐานข้อมูลที่มีอยู่ก่อนแล้ว

    CREATE TABLE IF NOT EXISTS ไม่แตะตารางที่มีอยู่แล้วเลย พอมี volume ถาวร
    ฐานข้อมูลเดิมจะไม่มีคอลัมน์ใหม่ตลอดไปถ้าไม่ ALTER ให้
    """
    columns = {row[1] for row in cursor.execute("PRAGMA table_info(tasks)")}
    if "priority" not in columns:
        cursor.execute("ALTER TABLE tasks ADD COLUMN priority TEXT")

    # คอลัมน์ของ Google Drive เพิ่มทีหลัง ฐานข้อมูลบน volume ที่สร้างไว้ก่อนหน้า
    # จะไม่มีให้ ถ้าไม่ ALTER ตรงนี้ get_settings จะพังด้วย "no such column"
    #
    # PRAGMA ของตารางที่ยังไม่มีคืนค่าว่าง ไม่ใช่ error — ฐานข้อมูลใหม่เอี่ยมจึง
    # ตกมาที่ทางนี้ และต้องข้ามไป เพราะ CREATE TABLE ข้างล่างใน init_db() ประกาศ
    # คอลัมน์ครบอยู่แล้ว ส่วน ALTER TABLE บนตารางที่ยังไม่มีจะพัง
    settings_columns = {
        row[1] for row in cursor.execute("PRAGMA table_info(user_storage_settings)")
    }
    if settings_columns:
        for column in ("google_refresh_token", "google_drive_folder_id"):
            if column not in settings_columns:
                cursor.execute(
                    f"ALTER TABLE user_storage_settings ADD COLUMN {column} TEXT"
                )


def init_db():
    """สร้างตารางทั้งหมดที่บอทใช้

    ตาราง tasks เป็นของ sql/01_schema.sql ไม่ใช่ของไฟล์นี้ — งานจาก Telegram
    กับงานจาก LINE อยู่ตารางเดียวกัน แยกความเป็นเจ้าของด้วย source/source_ref

    เดิมไฟล์นี้ประกาศ tasks ของตัวเองที่มี user_id/priority/due_date/project
    ทั้งสองฝั่งใช้ CREATE TABLE IF NOT EXISTS ใครสร้างก่อนจึงได้ตารางนั้นไป และ
    app.py เรียก start_web() ก่อน start_telegram() เสมอ ผลคือ /task พังด้วย
    "table tasks has no column named user_id" มาตลอดโดยไม่มีใครเห็น
    """
    line_webhook.init_webhook_tables(DB_PATH)

    conn = connect(DB_PATH)
    cursor = conn.cursor()

    _add_missing_columns(cursor)

    # Reminders table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            remind_at TIMESTAMP NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Notes table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            tags TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # User storage settings table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_storage_settings (
            user_id INTEGER PRIMARY KEY,
            storage_type TEXT DEFAULT 'local',
            airtable_api_key TEXT,
            airtable_base_id TEXT,
            airtable_table_name TEXT DEFAULT 'Tasks',
            google_sheet_id TEXT,
            google_refresh_token TEXT,
            google_drive_folder_id TEXT,
            preferred_language TEXT DEFAULT 'en',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Voice transcriptions log
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transcriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            original_text TEXT,
            duration_seconds REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()
    logger.info("Database initialized successfully")


# ═══════════════════════════════════════════════════════════════════════════════
# STORAGE SETTINGS MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class StorageSettings:
    """Manage user storage preferences"""
    
    @staticmethod
    def get_settings(user_id: int) -> Dict[str, Any]:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT storage_type, airtable_api_key, airtable_base_id,
                   airtable_table_name, google_sheet_id, preferred_language,
                   google_refresh_token, google_drive_folder_id
            FROM user_storage_settings WHERE user_id = ?
        """, (user_id,))
        row = cursor.fetchone()
        conn.close()

        if row:
            return {
                "storage_type": row[0] or "local",
                "airtable_api_key": row[1],
                "airtable_base_id": row[2],
                "airtable_table_name": row[3] or "Tasks",
                "google_sheet_id": row[4],
                "preferred_language": row[5] or "en",
                "google_refresh_token": row[6],
                "google_drive_folder_id": row[7],
            }
        return {"storage_type": "local", "preferred_language": "en"}
    
    @staticmethod
    def set_storage_type(user_id: int, storage_type: str):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO user_storage_settings (user_id, storage_type, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET 
                storage_type = excluded.storage_type,
                updated_at = CURRENT_TIMESTAMP
        """, (user_id, storage_type))
        conn.commit()
        conn.close()
    
    @staticmethod
    def set_airtable(user_id: int, api_key: str, base_id: str, table_name: str = "Tasks"):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO user_storage_settings 
                (user_id, storage_type, airtable_api_key, airtable_base_id, airtable_table_name, updated_at)
            VALUES (?, 'airtable', ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET 
                storage_type = 'airtable',
                airtable_api_key = excluded.airtable_api_key,
                airtable_base_id = excluded.airtable_base_id,
                airtable_table_name = excluded.airtable_table_name,
                updated_at = CURRENT_TIMESTAMP
        """, (user_id, api_key, base_id, table_name))
        conn.commit()
        conn.close()
    
    @staticmethod
    def set_google_sheets(user_id: int, sheet_id: str):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO user_storage_settings 
                (user_id, storage_type, google_sheet_id, updated_at)
            VALUES (?, 'sheets', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET 
                storage_type = 'sheets',
                google_sheet_id = excluded.google_sheet_id,
                updated_at = CURRENT_TIMESTAMP
        """, (user_id, sheet_id))
        conn.commit()
        conn.close()
    
    @staticmethod
    def set_google_drive(user_id: int, refresh_token: str, folder_id: Optional[str] = None):
        """บันทึก refresh token ที่ได้จาก OAuth callback

        เก็บ refresh token ไม่ใช่ access token — access token ของ Google หมดอายุ
        ในหนึ่งชั่วโมง เก็บไว้ก็ใช้ไม่ได้ในรอบถัดไป ส่วน refresh token อยู่จนกว่า
        ผู้ใช้จะถอนสิทธิ์เอง
        """
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO user_storage_settings
                (user_id, storage_type, google_refresh_token, google_drive_folder_id, updated_at)
            VALUES (?, 'drive', ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                storage_type = 'drive',
                google_refresh_token = excluded.google_refresh_token,
                google_drive_folder_id = excluded.google_drive_folder_id,
                updated_at = CURRENT_TIMESTAMP
        """, (user_id, refresh_token, folder_id))
        conn.commit()
        conn.close()

    @staticmethod
    def set_drive_folder(user_id: int, folder_id: str):
        """จำโฟลเดอร์ที่สร้างไว้ครั้งแรก จะได้ไม่สร้างซ้ำทุกครั้งที่เขียน"""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE user_storage_settings
            SET google_drive_folder_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (folder_id, user_id))
        conn.commit()
        conn.close()

    @staticmethod
    def set_language(user_id: int, lang_code: str):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO user_storage_settings (user_id, preferred_language, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET 
                preferred_language = excluded.preferred_language,
                updated_at = CURRENT_TIMESTAMP
        """, (user_id, lang_code))
        conn.commit()
        conn.close()
    
    @staticmethod
    def reset_to_local(user_id: int):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE user_storage_settings 
            SET storage_type = 'local',
                airtable_api_key = NULL,
                airtable_base_id = NULL,
                google_sheet_id = NULL,
                google_refresh_token = NULL,
                google_drive_folder_id = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (user_id,))
        conn.commit()
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# AIRTABLE INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════════

class AirtableClient:
    """Airtable API client for user's personal base"""
    
    BASE_URL = "https://api.airtable.com/v0"
    
    def __init__(self, api_key: str, base_id: str, table_name: str = "Tasks"):
        self.api_key = api_key
        self.base_id = base_id
        self.table_name = table_name
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
    
    @property
    def url(self) -> str:
        return f"{self.BASE_URL}/{self.base_id}/{self.table_name}"
    
    async def test_connection(self) -> Dict[str, Any]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.url, headers=self.headers, params={"maxRecords": 1}
                ) as response:
                    if response.status == 200:
                        return {"success": True, "message": "✅ Connected to Airtable!"}
                    elif response.status == 401:
                        return {"success": False, "message": "❌ Invalid API Key"}
                    elif response.status == 404:
                        return {"success": False, "message": "❌ Base or Table not found"}
                    else:
                        return {"success": False, "message": f"❌ Error: {response.status}"}
        except Exception as e:
            return {"success": False,
                    "message": api_failure_message(e, "เชื่อมต่อ Airtable", "Airtable API Key")}


# ═══════════════════════════════════════════════════════════════════════════════
# GOOGLE SHEETS INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════════

class GoogleSheetsClient:
    """Simple Google Sheets client (public sheets only)"""
    
    def __init__(self, sheet_id: str):
        self.sheet_id = sheet_id
    
    async def test_connection(self) -> Dict[str, Any]:
        try:
            url = f"https://docs.google.com/spreadsheets/d/{self.sheet_id}/export?format=csv&range=A1"
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        return {"success": True, "message": "✅ Connected to Google Sheets!"}
                    else:
                        return {"success": False, "message": "❌ Sheet not accessible. Make sure it's shared publicly."}
        except Exception as e:
            return {"success": False, "message": api_failure_message(e, "เชื่อมต่อ Google Sheets")}


# ═══════════════════════════════════════════════════════════════════════════════
# GOOGLE DRIVE INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════════

DRIVE_FOLDER_NAME = "Assistant everyTask Bot"
DRIVE_MIME_FOLDER = "application/vnd.google-apps.folder"


def _drive_data_filename(user_id: int) -> str:
    return f"assistant_data_{user_id}.json"


class GoogleDriveClient:
    """Google Drive client ที่ต่ออายุ access token ให้เอง

    ต่างจากโมดูลต้นทางตรงที่รับ refresh token ไม่ใช่ access token — access token
    ของ Google อายุหนึ่งชั่วโมง ถ้าเก็บตัวนั้นไว้ในฐานข้อมูล ผู้ใช้จะต่อได้แค่
    ชั่วโมงแรกแล้วเงียบไปเฉย ๆ โดยไม่มีอะไรฟ้อง
    """

    DRIVE_API_URL = "https://www.googleapis.com/drive/v3"
    UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3"
    TOKEN_URL = "https://oauth2.googleapis.com/token"

    def __init__(self, refresh_token: str, folder_id: Optional[str] = None,
                 client_id: Optional[str] = None, client_secret: Optional[str] = None):
        self.refresh_token = refresh_token
        self.folder_id = folder_id
        self.client_id = client_id or GOOGLE_CLIENT_ID
        self.client_secret = client_secret or GOOGLE_CLIENT_SECRET
        self._access_token: Optional[str] = None
        self._expires_at = datetime.min

    async def _token(self) -> Optional[str]:
        """คืน access token ที่ยังไม่หมดอายุ ขอใหม่เมื่อจำเป็น"""
        if self._access_token and datetime.utcnow() < self._expires_at:
            return self._access_token

        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(self.TOKEN_URL, data=payload) as response:
                if response.status != 200:
                    body = await response.text()
                    logger.warning(
                        "ขอ access token ของ Google ไม่สำเร็จ (%s): %s",
                        response.status, body[:200],
                    )
                    return None
                data = await response.json()

        self._access_token = data.get("access_token")
        # หักออกหนึ่งนาทีกันเส้นตาย — ขอใหม่ก่อนหมดอายุจริงดีกว่าโดนปฏิเสธกลางคัน
        self._expires_at = datetime.utcnow() + timedelta(
            seconds=max(int(data.get("expires_in", 3600)) - 60, 0)
        )
        return self._access_token

    async def _headers(self) -> Optional[Dict[str, str]]:
        token = await self._token()
        if not token:
            return None
        return {"Authorization": f"Bearer {token}",
                "Content-Type": "application/json"}

    async def test_connection(self) -> Dict[str, Any]:
        """เช็คว่าต่อ Drive ได้จริง และบอกว่าเป็นบัญชีไหน"""
        if not self.client_id or not self.client_secret:
            return {"success": False,
                    "message": "❌ ยังไม่ได้ตั้ง GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET"}
        try:
            headers = await self._headers()
            if not headers:
                return {"success": False, "message": "❌ Token หมดอายุหรือถูกถอนสิทธิ์"}
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.DRIVE_API_URL}/about",
                    headers=headers,
                    params={"fields": "user"},
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        email = data.get("user", {}).get("emailAddress", "unknown")
                        return {"success": True, "message": f"✅ ต่อ Drive แล้ว: {email}"}
                    if response.status == 401:
                        return {"success": False, "message": "❌ Token ใช้ไม่ได้แล้ว"}
                    return {"success": False, "message": f"❌ Error: {response.status}"}
        except Exception as e:
            return {"success": False, "message": api_failure_message(e, "เชื่อมต่อ Google Drive")}

    async def ensure_folder(self) -> Optional[str]:
        """หาโฟลเดอร์ของบอท ถ้ายังไม่มีก็สร้าง แล้วคืน id"""
        if self.folder_id:
            return self.folder_id

        headers = await self._headers()
        if not headers:
            return None

        query = (
            f"name = '{DRIVE_FOLDER_NAME}' and mimeType = '{DRIVE_MIME_FOLDER}' "
            "and trashed = false"
        )
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.DRIVE_API_URL}/files",
                headers=headers,
                params={"q": query, "fields": "files(id)"},
            ) as response:
                if response.status == 200:
                    files = (await response.json()).get("files", [])
                    if files:
                        self.folder_id = files[0]["id"]
                        return self.folder_id

            async with session.post(
                f"{self.DRIVE_API_URL}/files",
                headers=headers,
                json={"name": DRIVE_FOLDER_NAME, "mimeType": DRIVE_MIME_FOLDER},
            ) as response:
                if response.status in (200, 201):
                    self.folder_id = (await response.json()).get("id")
                    return self.folder_id
                logger.warning("สร้างโฟลเดอร์บน Drive ไม่สำเร็จ (%s)", response.status)
        return None

    async def find_file(self, name: str) -> Optional[str]:
        """หาไฟล์ตามชื่อในโฟลเดอร์ของบอท"""
        headers = await self._headers()
        if not headers:
            return None
        folder = await self.ensure_folder()

        # ชื่อไฟล์ประกอบจาก user id ล้วน ๆ แต่ escape ไว้ก็ไม่เสียหาย เพราะ
        # single quote ที่หลุดเข้าไปทำให้ query ของ Drive พังทั้งอัน
        safe = name.replace("\\", "\\\\").replace("'", "\\'")
        query = f"name = '{safe}' and trashed = false"
        if folder:
            query += f" and '{folder}' in parents"

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.DRIVE_API_URL}/files",
                headers=headers,
                params={"q": query, "fields": "files(id)"},
            ) as response:
                if response.status != 200:
                    return None
                files = (await response.json()).get("files", [])
                return files[0]["id"] if files else None

    async def write_json(self, name: str, payload: Dict[str, Any]) -> Optional[str]:
        """เขียนไฟล์ JSON — สร้างใหม่ถ้ายังไม่มี ทับของเดิมถ้ามีแล้ว"""
        token = await self._token()
        if not token:
            return None

        body = json.dumps(payload, ensure_ascii=False, indent=2)
        file_id = await self.find_file(name)

        if file_id:
            headers = {"Authorization": f"Bearer {token}",
                       "Content-Type": "application/json; charset=UTF-8"}
            async with aiohttp.ClientSession() as session:
                async with session.patch(
                    f"{self.UPLOAD_URL}/files/{file_id}",
                    headers=headers,
                    params={"uploadType": "media"},
                    data=body.encode("utf-8"),
                ) as response:
                    if response.status == 200:
                        return file_id
                    logger.warning("อัปเดตไฟล์บน Drive ไม่สำเร็จ (%s)", response.status)
                    return None

        metadata: Dict[str, Any] = {"name": name}
        folder = await self.ensure_folder()
        if folder:
            metadata["parents"] = [folder]

        # multipart/related ประกอบเองด้วย writer ของ aiohttp แทนการต่อสตริง
        # boundary ด้วยมือ — ข้อความของผู้ใช้ที่บังเอิญมี boundary อยู่ข้างในจะทำให้
        # เนื้อไฟล์ที่ต่อเองขาดกลาง
        with aiohttp.MultipartWriter("related") as mpwriter:
            part = mpwriter.append_json(metadata)
            part.set_content_disposition("form-data", name="metadata")
            part = mpwriter.append(
                body.encode("utf-8"),
                {"Content-Type": "application/json; charset=UTF-8"},
            )
            part.set_content_disposition("form-data", name="file")

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.UPLOAD_URL}/files",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"uploadType": "multipart"},
                    data=mpwriter,
                ) as response:
                    if response.status in (200, 201):
                        return (await response.json()).get("id")
                    logger.warning("อัปโหลดไฟล์ขึ้น Drive ไม่สำเร็จ (%s)", response.status)
                    return None

    async def read_json(self, name: str) -> Optional[Dict[str, Any]]:
        """อ่านไฟล์ JSON กลับมา ใช้ยืนยันว่าที่เขียนไปถึงจริง"""
        headers = await self._headers()
        if not headers:
            return None
        file_id = await self.find_file(name)
        if not file_id:
            return None
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.DRIVE_API_URL}/files/{file_id}",
                headers=headers,
                params={"alt": "media"},
            ) as response:
                if response.status != 200:
                    return None
                try:
                    return json.loads(await response.text())
                except json.JSONDecodeError:
                    logger.warning("ไฟล์ %s บน Drive ไม่ใช่ JSON ที่อ่านได้", name)
                    return None


async def mirror_and_warn(update: Update, user_id: int) -> None:
    """ส่งสำเนาขึ้น Drive หลังเขียนสำเร็จ และบอกผู้ใช้เมื่อส่งไม่ขึ้น

    เรียกหลังตอบผู้ใช้แล้วเสมอ — งานถูกบันทึกลง SQLite ไปแล้วตั้งแต่ก่อนถึงตรงนี้
    Drive ล่มจึงไม่ควรทำให้คำสั่งล้มเหลว แต่ก็ต้องไม่เงียบ ไม่งั้นผู้ใช้จะเชื่อว่า
    ข้อมูลขึ้น Drive แล้วทั้งที่ไม่ได้ขึ้น
    """
    if StorageSettings.get_settings(user_id).get("storage_type") != "drive":
        return
    if await mirror_to_drive(user_id):
        return
    await update.message.reply_text(
        "⚠️ บันทึกลงบอทแล้ว แต่ส่งขึ้น Google Drive ไม่สำเร็จ\n"
        "ลอง /mystorage เพื่อดูสถานะการเชื่อมต่อ"
    )


def drive_client_for(user_id: int) -> Optional[GoogleDriveClient]:
    """สร้าง client จากค่าที่ผู้ใช้ตั้งไว้ คืน None ถ้ายังไม่ได้ต่อ Drive"""
    settings = StorageSettings.get_settings(user_id)
    if settings.get("storage_type") != "drive":
        return None
    refresh_token = settings.get("google_refresh_token")
    if not refresh_token:
        return None
    return GoogleDriveClient(refresh_token, settings.get("google_drive_folder_id"))


async def mirror_to_drive(user_id: int) -> bool:
    """ส่งข้อมูลของผู้ใช้ขึ้น Drive เป็นไฟล์ JSON ไฟล์เดียว

    SQLite ยังเป็นต้นฉบับเสมอ Drive เป็นสำเนา — ตัวส่งการเตือนกับ /done อ้าง
    rowid ของ SQLite ถ้าย้ายต้นฉบับไป Drive สองอย่างนั้นพังทันที และการเขียน
    ที่ล้มเหลวกลางทางจะกลายเป็นข้อมูลหาย ไม่ใช่แค่สำเนาไม่ตรง
    """
    client_drive = drive_client_for(user_id)
    if client_drive is None:
        return False

    payload = {
        "user_id": user_id,
        "exported_at": line_webhook.utc_now(),
        "tasks": await Storage.get_tasks(user_id),
        "reminders": await Storage.get_reminders(user_id),
        "notes": await Storage.get_notes(user_id),
    }

    try:
        file_id = await client_drive.write_json(_drive_data_filename(user_id), payload)
    except Exception:
        logger.exception("ส่งข้อมูลขึ้น Drive ไม่สำเร็จ (user %s)", user_id)
        return False

    if not file_id:
        return False

    # โฟลเดอร์เพิ่งถูกสร้างในรอบนี้ จำไว้จะได้ไม่ต้องค้นหาใหม่ทุกครั้ง
    if client_drive.folder_id:
        settings = StorageSettings.get_settings(user_id)
        if settings.get("google_drive_folder_id") != client_drive.folder_id:
            StorageSettings.set_drive_folder(user_id, client_drive.folder_id)
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# STORAGE MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

# งานทุกแถวในตารางกลางบอกที่มาของตัวเองไว้ ค่านี้คือของฝั่ง Telegram
TELEGRAM_SOURCE = "telegram"


def _project_id(conn, name):
    """หา projects.id จากชื่อ — ไม่สร้างแถวใหม่

    projects.type มี CHECK จำกัดไว้ห้าค่า การเดา type จากข้อความที่พิมพ์ใน
    Telegram มีแต่จะได้แถวผิดหรือ IntegrityError งานที่อ้างชื่อโปรเจกต์ที่ยัง
    ไม่มีจึงปล่อย project_id ว่างไว้ ดีกว่าเดา
    """
    if not name:
        return None
    row = conn.execute(
        "SELECT id FROM projects WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    return row[0] if row else None


class Storage:
    """Routes storage operations to the correct backend"""
    
    @staticmethod
    async def add_task(user_id: int, title: str, priority: str = "medium",
                       due_date: str = None, project: str = None) -> int:
        """สร้างงานลงตารางกลาง — source='telegram', source_ref = Telegram user id

        source_ref คือสิ่งที่บอกว่างานเป็นของใคร แทนคอลัมน์ user_id เดิม รูปแบบ
        เดียวกับที่ฝั่ง LINE ใช้ (source='line', source_ref = chat_messages.id)
        """
        conn = connect(DB_PATH)
        try:
            at = line_webhook.utc_now()
            with conn:
                cursor = conn.execute("""
                    INSERT INTO tasks
                        (title, status, created_at, due_at, priority, project_id,
                         source, source_ref)
                    VALUES (?, 'inbox', ?, ?, ?, ?, ?, ?)
                """, (title, at, due_date, priority, _project_id(conn, project),
                      TELEGRAM_SOURCE, str(user_id)))
                task_id = int(cursor.lastrowid)
                # task_events เป็นบันทึกการเปลี่ยนสถานะที่รายงานทุกตัวอ่าน
                # ฝั่ง LINE เขียนทุกครั้งที่สร้างงาน ฝั่งนี้จึงต้องเขียนด้วย
                conn.execute("""
                    INSERT INTO task_events (task_id, from_status, to_status, at)
                    VALUES (?, NULL, 'inbox', ?)
                """, (task_id, at))
            return task_id
        finally:
            conn.close()
    
    @staticmethod
    async def get_tasks(user_id: int, status: str = None) -> List[Dict]:
        """งานของผู้ใช้คนนี้เท่านั้น — งานที่มาจาก LINE จะไม่ติดมาด้วย"""
        conn = connect(DB_PATH)
        try:
            sql = """
                SELECT t.id, t.title, t.priority, t.status, t.due_at, p.name
                FROM tasks t
                LEFT JOIN projects p ON p.id = t.project_id
                WHERE t.source = ? AND t.source_ref = ?
            """
            params = [TELEGRAM_SOURCE, str(user_id)]
            if status:
                sql += " AND t.status = ?"
                params.append(status)
            # created_at ละเอียดระดับวินาที งานที่สร้างวินาทีเดียวกันจึงต้องมี
            # ตัวตัดสินที่แน่นอน ไม่งั้นลำดับสลับไปมาระหว่างการรัน
            sql += " ORDER BY t.created_at DESC, t.id DESC"
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        
        return [
            {
                "id": row[0], "title": row[1], "priority": row[2],
                "status": row[3], "due_date": row[4], "project": row[5]
            }
            for row in rows
        ]
    
    @staticmethod
    async def complete_task(user_id: int, task_id: int) -> bool:
        conn = connect(DB_PATH)
        try:
            at = line_webhook.utc_now()
            with conn:
                row = conn.execute("""
                    SELECT status FROM tasks
                    WHERE id = ? AND source = ? AND source_ref = ?
                """, (task_id, TELEGRAM_SOURCE, str(user_id))).fetchone()
                if row is None:
                    return False
                if row[0] != "done":
                    conn.execute("""
                        UPDATE tasks SET status = 'done', completed_at = ? WHERE id = ?
                    """, (at, task_id))
                    conn.execute("""
                        INSERT INTO task_events (task_id, from_status, to_status, at)
                        VALUES (?, ?, 'done', ?)
                    """, (task_id, row[0], at))
            return True
        finally:
            conn.close()
    
    @staticmethod
    async def add_reminder(user_id: int, text: str, remind_at: datetime) -> int:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO reminders (user_id, text, remind_at)
            VALUES (?, ?, ?)
        """, (user_id, text, remind_at.isoformat()))
        reminder_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return reminder_id
    
    @staticmethod
    async def get_reminders(user_id: int) -> List[Dict]:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, text, remind_at, status
            FROM reminders WHERE user_id = ? AND status = 'pending'
            ORDER BY remind_at ASC
        """, (user_id,))
        rows = cursor.fetchall()
        conn.close()
        return [{"id": r[0], "text": r[1], "remind_at": r[2], "status": r[3]} for r in rows]
    
    @staticmethod
    async def get_due_reminders(now: Optional[datetime] = None) -> List[Dict]:
        """การเตือนที่ถึงเวลาแล้วและยังไม่ถูกส่ง

        remind_at เก็บเป็น datetime.now().isoformat() คือเวลาท้องถิ่นแบบไม่มี
        timezone การเทียบจึงต้องใช้ datetime.now() เหมือนกัน ไม่ใช่ utcnow()

        รวมรายการที่เลยกำหนดไปแล้วด้วย ไม่ใช่เฉพาะที่ถึงพอดีในรอบนี้ — ถ้าบอท
        ดับไปสองชั่วโมง ของที่ครบกำหนดระหว่างนั้นต้องได้ออกตอนกลับมา สายดีกว่าหาย
        """
        cutoff = (now or datetime.now()).isoformat()
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, user_id, text, remind_at
            FROM reminders WHERE status = 'pending' AND remind_at <= ?
            ORDER BY remind_at ASC
        """, (cutoff,))
        rows = cursor.fetchall()
        conn.close()
        return [{"id": r[0], "user_id": r[1], "text": r[2], "remind_at": r[3]} for r in rows]

    @staticmethod
    async def mark_reminder(reminder_id: int, status: str) -> None:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE reminders SET status = ? WHERE id = ?", (status, reminder_id))
        conn.commit()
        conn.close()

    @staticmethod
    async def add_note(user_id: int, content: str, tags: str = None) -> int:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO notes (user_id, content, tags)
            VALUES (?, ?, ?)
        """, (user_id, content, tags))
        note_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return note_id
    
    @staticmethod
    async def get_notes(user_id: int) -> List[Dict]:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, content, tags, created_at
            FROM notes WHERE user_id = ?
            ORDER BY created_at DESC
        """, (user_id,))
        rows = cursor.fetchall()
        conn.close()
        return [{"id": r[0], "content": r[1], "tags": r[2], "created_at": r[3]} for r in rows]


# ═══════════════════════════════════════════════════════════════════════════════
# AI SERVICES (Translation & Transcription)
# ═══════════════════════════════════════════════════════════════════════════════

async def translate_text(text: str, target_lang: str, source_lang: str = "auto") -> str:
    """Translate text using OpenAI GPT"""
    if not client:
        return "❌ OpenAI API not configured"
    
    import asyncio
    
    try:
        lang_name = LANGUAGES.get(target_lang, target_lang)
        
        # Run synchronous OpenAI call in thread pool
        def do_translate():
            return client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                messages=[
                    {
                        "role": "system",
                        "content": f"You are a translator. Translate the following text to {lang_name}. Only output the translation, nothing else."
                    },
                    {"role": "user", "content": text}
                ],
                max_tokens=1000
            )
        
        response = await asyncio.to_thread(do_translate)
        return response.choices[0].message.content.strip()
    except Exception as e:
        return api_failure_message(e, "แปลภาษา", "OPENAI_API_KEY")


async def transcribe_voice(file_path: str) -> str:
    """Transcribe voice message using OpenAI Whisper"""
    if not client:
        return "❌ OpenAI API not configured"
    
    import asyncio

    # ไม่จับ exception ตรงนี้ ปล่อยให้ทะลุไปถึง handle_voice
    #
    # เดิมจับไว้แล้วคืนข้อความ error เป็น "ผลการถอดเสียง" ผลคือความล้มเหลวถูกบันทึก
    # ลงตาราง transcriptions เป็น original_text เหมือนเป็นข้อความจริง แล้วถูกแสดง
    # ใต้หัวข้อ "Voice Transcription" ราวกับถอดเสียงสำเร็จ ผู้เรียกต้องแยกให้ออกว่า
    # สำเร็จหรือล้มเหลว ซึ่งทำไม่ได้ถ้าทั้งสองกรณีคืน str เหมือนกัน
    def do_transcribe():
        with open(file_path, "rb") as audio_file:
            return client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="text"
            )

    response = await asyncio.to_thread(do_transcribe)
    return response.strip()


# ═══════════════════════════════════════════════════════════════════════════════
# SETUP STATE TRACKING
# ═══════════════════════════════════════════════════════════════════════════════

user_setup_state: Dict[int, Dict[str, Any]] = {}


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

DURATION_RE = re.compile(r"^(\d+)\s*([mhd])$", re.IGNORECASE)
DURATION_UNITS = {"m": "minutes", "h": "hours", "d": "days"}


def parse_duration(text: str) -> Optional[timedelta]:
    """Turn '30m', '2h', '1d' into a timedelta. None when it isn't one."""
    match = DURATION_RE.match((text or "").strip())
    if not match:
        return None
    amount = int(match.group(1))
    if amount <= 0:
        return None
    return timedelta(**{DURATION_UNITS[match.group(2).lower()]: amount})


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    
    text = f"""
👋 **สวัสดี {escape_md(user.first_name)}!** Welcome!

🤖 I'm **Assistant EveryTask Bot** - Your personal productivity assistant!

━━━━━━━━━━━━━━━━━━━━
📋 **Task Management**
━━━━━━━━━━━━━━━━━━━━
• `/task <title>` - Add a task
• `/tasks` - View all tasks
• `/done <id>` - Complete task

━━━━━━━━━━━━━━━━━━━━
⏰ **Reminders**
━━━━━━━━━━━━━━━━━━━━
• `/remind 30m Call mom`
• `/reminders` - View all

━━━━━━━━━━━━━━━━━━━━
📝 **Notes**
━━━━━━━━━━━━━━━━━━━━
• `/note <content>` - Save note
• `/notes` - View all notes

━━━━━━━━━━━━━━━━━━━━
🌐 **Translation**
━━━━━━━━━━━━━━━━━━━━
• `/tr <lang> <text>` - Translate
• Example: `/tr th Hello world`
• Supports 20+ languages!

━━━━━━━━━━━━━━━━━━━━
🎤 **Voice Messages**
━━━━━━━━━━━━━━━━━━━━
• Just send a voice message!
• I'll transcribe it automatically 🎙️

━━━━━━━━━━━━━━━━━━━━
⚙️ **Settings**
━━━━━━━━━━━━━━━━━━━━
• `/settings` - Storage options
• `/language` - Set language

Use `/help` for full guide! 📖
"""
    
    await update.message.reply_text(text, parse_mode="Markdown")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    text = """
📖 **Full Command Guide**

**Tasks:**
`/task Buy groceries` - Add task
`/tasks` - List all tasks
`/done 1` - Complete task #1

**Reminders:**
`/remind 30m Call client`
`/remind 2h Meeting`
`/remind 1d Birthday`
`/reminders` - View active

**Notes:**
`/note Meeting notes here`
`/notes` - View all notes

**Translation:**
`/tr th Hello` → สวัสดี
`/tr en สวัสดี` → Hello
`/tr ja Good morning` → おはよう

**Languages:** en, th, zh, ja, ko, vi, id, ms, es, fr, de, it, pt, ru, ar, hi, tl, my, km, lo

**Voice:**
Send any voice message → Auto transcription!

**Settings:**
`/settings` - Connect Airtable/Sheets
`/mystorage` - View current storage
`/language` - Set preferred language
"""
    await update.message.reply_text(text, parse_mode="Markdown")


# ═══════════════════════════════════════════════════════════════════════════════
# TASK COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

async def task_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a new task"""
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text(
            "📝 Please provide a task title:\n`/task Buy groceries`",
            parse_mode="Markdown"
        )
        return
    
    title = " ".join(context.args)
    
    priority = detect_priority(title)
    
    task_id = await Storage.add_task(user_id, title, priority)
    
    priority_emoji = {"urgent": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
    
    await update.message.reply_text(
        f"✅ **Task Added!**\n\n"
        f"📋 {escape_md(title)}\n"
        f"{priority_emoji.get(priority, '⚪')} Priority: {priority}\n\n"
        f"Complete with `/done {task_id}`",
        parse_mode="Markdown"
    )

    await mirror_and_warn(update, user_id)


async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all tasks"""
    user_id = update.effective_user.id
    tasks = await Storage.get_tasks(user_id)
    
    if not tasks:
        await update.message.reply_text(
            "📭 No tasks yet!\nAdd one with `/task Buy groceries`",
            parse_mode="Markdown"
        )
        return
    
    todo = [t for t in tasks if t["status"] == "inbox"]
    doing = [t for t in tasks if t["status"] == "doing"]
    done = [t for t in tasks if t["status"] == "done"]
    
    priority_emoji = {"urgent": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
    
    text = "📋 **Your Tasks**\n\n"
    
    if todo:
        text += "**📌 To Do:**\n"
        for t in todo[:10]:
            emoji = priority_emoji.get(t["priority"], "⚪")
            text += f"{emoji} `{t['id']}` {escape_md(t['title'])}\n"
        text += "\n"
    
    if doing:
        text += "**⚡ In Progress:**\n"
        for t in doing[:5]:
            text += f"🔵 `{t['id']}` {escape_md(t['title'])}\n"
        text += "\n"
    
    if done:
        text += f"**✅ Done:** {len(done)} tasks\n"
    
    await update.message.reply_text(text, parse_mode="Markdown")


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mark a task as done"""
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text("Usage: `/done 1`", parse_mode="Markdown")
        return
    
    try:
        task_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid task ID")
        return
    
    success = await Storage.complete_task(user_id, task_id)
    
    if success:
        await update.message.reply_text(f"✅ Task #{task_id} completed! 🎉")
        await mirror_and_warn(update, user_id)
    else:
        await update.message.reply_text(f"❌ Task #{task_id} not found")


# ═══════════════════════════════════════════════════════════════════════════════
# REMINDER COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set a reminder"""
    user_id = update.effective_user.id
    
    if len(context.args) < 2:
        await update.message.reply_text(
            "⏰ **Set a reminder:**\n\n"
            "`/remind 30m Call mom`\n"
            "`/remind 2h Meeting`\n"
            "`/remind 1d Birthday`",
            parse_mode="Markdown"
        )
        return
    
    text = " ".join(context.args[1:])
    
    delta = parse_duration(context.args[0])
    if delta is None:
        await update.message.reply_text("❌ Use: `30m`, `2h`, `1d`", parse_mode="Markdown")
        return
    
    remind_at = datetime.now() + delta
    
    await Storage.add_reminder(user_id, text, remind_at)
    
    await update.message.reply_text(
        f"⏰ **Reminder Set!**\n\n"
        f"📝 {escape_md(text)}\n"
        f"🕐 {remind_at.strftime('%Y-%m-%d %H:%M')}",
        parse_mode="Markdown"
    )

    await mirror_and_warn(update, user_id)


async def reminders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List reminders"""
    user_id = update.effective_user.id
    reminders = await Storage.get_reminders(user_id)
    
    if not reminders:
        await update.message.reply_text("🔔 No active reminders")
        return
    
    text = "⏰ **Your Reminders**\n\n"
    for r in reminders:
        text += f"🔔 `{r['id']}` {escape_md(r['text'])}\n   📅 {r['remind_at']}\n\n"
    
    await update.message.reply_text(text, parse_mode="Markdown")


# ═══════════════════════════════════════════════════════════════════════════════
# NOTE COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

async def note_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save a note"""
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text("📝 Usage: `/note Your note here`", parse_mode="Markdown")
        return
    
    content = " ".join(context.args)
    note_id = await Storage.add_note(user_id, content)
    
    await update.message.reply_text(
        f"📝 **Note Saved!**\n\nID: `{note_id}`",
        parse_mode="Markdown"
    )

    await mirror_and_warn(update, user_id)


async def notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List notes"""
    user_id = update.effective_user.id
    notes = await Storage.get_notes(user_id)
    
    if not notes:
        await update.message.reply_text("📝 No notes yet!")
        return
    
    text = "📝 **Your Notes**\n\n"
    for n in notes[:10]:
        preview = n["content"][:50] + "..." if len(n["content"]) > 50 else n["content"]
        text += f"`{n['id']}` {escape_md(preview)}\n"
    
    await update.message.reply_text(text, parse_mode="Markdown")


# ═══════════════════════════════════════════════════════════════════════════════
# TRANSLATION COMMAND
# ═══════════════════════════════════════════════════════════════════════════════

async def translate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Translate text"""
    if len(context.args) < 2:
        lang_list = ", ".join([f"`{k}` ({v})" for k, v in list(LANGUAGES.items())[:10]])
        await update.message.reply_text(
            f"🌐 **Translation**\n\n"
            f"Usage: `/tr <lang> <text>`\n\n"
            f"Example:\n"
            f"• `/tr th Hello world`\n"
            f"• `/tr en สวัสดี`\n"
            f"• `/tr ja Good morning`\n\n"
            f"**Languages:**\n{lang_list}...",
            parse_mode="Markdown"
        )
        return
    
    target_lang = context.args[0].lower()
    text = " ".join(context.args[1:])
    
    if target_lang not in LANGUAGES:
        await update.message.reply_text(
            f"❌ Unknown language: `{escape_code(target_lang)}`\n\n"
            f"Available: en, th, zh, ja, ko, vi, id, es, fr, de...",
            parse_mode="Markdown"
        )
        return
    
    await update.message.reply_text("🔄 Translating...")
    
    translated = await translate_text(text, target_lang)
    
    await update.message.reply_text(
        f"🌐 **Translation**\n\n"
        f"📝 Original: {escape_md(text)}\n\n"
        f"🎯 {LANGUAGES[target_lang]}: {escape_md(translated)}",
        parse_mode="Markdown"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# นำเข้าประวัติแชทจากไฟล์ export
# ═══════════════════════════════════════════════════════════════════════════════

# ไฟล์ export ของแชทที่คุยกันมานานเป็นปีก็ยังไม่กี่ MB ถ้าใหญ่กว่านี้มักไม่ใช่
# ไฟล์ export แต่เป็นอย่างอื่นที่ส่งผิด
MAX_EXPORT_BYTES = 8 * 1024 * 1024

# "ฉันคือ สมชาย" / "ผมคือ Farid" / "me: Somchai" — ใช้บอกว่าชื่อไหนคือเจ้าของ
_OWNER_HINT_RE = re.compile(
    r"^\s*(?:ฉันคือ|ผมคือ|เราคือ|me)\s*[:：]?\s*(?P<name>.+?)\s*$",
    re.IGNORECASE,
)


def _owner_from_caption(caption: Optional[str]) -> Optional[str]:
    if not caption:
        return None
    match = _OWNER_HINT_RE.match(caption)
    return match.group("name") if match else None


async def handle_chat_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """รับไฟล์ .txt ที่ export จาก LINE แล้วนำเข้าตารางกลาง

    ส่งไฟล์มาเฉย ๆ ก็พอ ถ้าเป็นแชทกลุ่มหรือบอทเดาชื่อเจ้าของไม่ได้ ให้ใส่ caption
    ว่า "ฉันคือ <ชื่อที่คุณใช้ในแชทนั้น>" มาด้วย ไม่งั้นทุกข้อความจะถูกนับเป็น
    ข้อความขาเข้า และตัวเลข "รอเราตอบ" จะผิด
    """
    document = update.message.document
    if not document:
        return

    name = (document.file_name or "").lower()
    if not name.endswith(".txt"):
        await update.message.reply_text(
            "📎 รับเฉพาะไฟล์ .txt ที่ export จาก LINE\n"
            "ในแอป LINE: เปิดห้องแชท → เมนู → การตั้งค่า → บันทึกประวัติแชท"
        )
        return

    if document.file_size and document.file_size > MAX_EXPORT_BYTES:
        await update.message.reply_text(
            f"📎 ไฟล์ใหญ่เกิน {MAX_EXPORT_BYTES // (1024 * 1024)} MB "
            "ลองแบ่งเป็นช่วงเวลาสั้นลงแล้วส่งใหม่"
        )
        return

    await update.message.reply_text("📥 กำลังอ่านไฟล์...")

    import asyncio   # โมดูลนี้ import asyncio ในฟังก์ชันเป็นแบบแผนอยู่แล้ว

    temp_path = None
    try:
        telegram_file = await context.bot.get_file(document.file_id)
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as handle:
            temp_path = handle.name
        await telegram_file.download_to_drive(temp_path)

        # ไฟล์จาก LINE เป็น UTF-8 แต่บางเครื่องบันทึกมาพร้อม BOM
        with open(temp_path, "r", encoding="utf-8-sig", errors="replace") as handle:
            raw_text = handle.read()

        owner = _owner_from_caption(update.message.caption)
        guessed = owner is None

        # ทั้งการเปิดและปิด connection อยู่ในเธรดเดียวกันภายใน import_from_text
        # ส่ง connection ข้ามเธรดเข้า to_thread ไม่ได้ — sqlite3 ผูกไว้กับเธรดที่สร้าง
        export, result = await asyncio.to_thread(
            line_export.import_from_text,
            DB_PATH,
            raw_text,
            owner_name=owner,
        )

        if not export.messages:
            await update.message.reply_text(
                "❌ อ่านไฟล์แล้วไม่พบข้อความเลย\n"
                f"บรรทัดที่อ่านไม่ออก: {len(export.skipped)}\n\n"
                "ไฟล์อาจเป็นรูปแบบที่ยังไม่รองรับ ส่งไฟล์มาให้ดูได้"
            )
            return

        if owner is None:
            owner = line_export.guess_owner(export)

        lines = [
            f"📥 **นำเข้าแล้ว**\n",
            f"ห้อง: {escape_md(result.title or 'ไม่ทราบชื่อ')}",
            f"เก็บใหม่: {result.imported} ข้อความ",
        ]
        if result.duplicates:
            lines.append(f"มีอยู่แล้ว (ข้าม): {result.duplicates}")
        if result.skipped_lines:
            lines.append(f"⚠️ อ่านไม่ออก: {result.skipped_lines} บรรทัด")
        if result.intents:
            summary = " · ".join(
                f"{intent} {count}"
                for intent, count in sorted(result.intents.items())
            )
            lines.append(f"\nคัดแยกได้: {summary}")

        if owner is None:
            lines.append(
                "\n⚠️ ไม่รู้ว่าชื่อไหนคือคุณ จึงนับทุกข้อความเป็นขาเข้า\n"
                "ส่งไฟล์ใหม่พร้อม caption ว่า `ฉันคือ <ชื่อของคุณในแชทนั้น>`\n"
                f"ชื่อที่พบ: {escape_md(', '.join(export.senders[:5]))}"
            )
        elif guessed:
            lines.append(f"\nเดาว่าคุณคือ {escape_md(owner)} — ถ้าผิดบอกได้")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(api_failure_message(e, "นำเข้าไฟล์แชท"))
    finally:
        if temp_path is not None and os.path.exists(temp_path):
            os.unlink(temp_path)


# ═══════════════════════════════════════════════════════════════════════════════
# VOICE MESSAGE HANDLER
# ═══════════════════════════════════════════════════════════════════════════════

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice messages - transcribe them"""
    user_id = update.effective_user.id
    voice = update.message.voice
    
    if not voice:
        return
    
    await update.message.reply_text("🎤 Transcribing your voice message...")
    
    temp_path = None
    try:
        # Download the voice file
        file: File = await context.bot.get_file(voice.file_id)
        
        # Create temp file
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as temp_file:
            temp_path = temp_file.name
            await file.download_to_drive(temp_path)
        
        # Transcribe
        transcription = await transcribe_voice(temp_path)
        
        # Save to database
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO transcriptions (user_id, original_text, duration_seconds)
            VALUES (?, ?, ?)
        """, (user_id, transcription, voice.duration))
        conn.commit()
        conn.close()
        
        await update.message.reply_text(
            f"🎤 **Voice Transcription**\n\n"
            f"📝 {escape_md(transcription)}\n\n"
            f"⏱️ Duration: {voice.duration}s",
            parse_mode="Markdown"
        )
        
    except Exception as e:
        await update.message.reply_text(api_failure_message(e, "ถอดเสียง", "OPENAI_API_KEY"))
    finally:
        # เดิมลบไฟล์หลังถอดเสียงเสร็จ ถ้าถอดเสียงพังไฟล์ .ogg จะค้างทุกครั้ง
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


# ═══════════════════════════════════════════════════════════════════════════════
# SETTINGS COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show storage settings menu"""
    user_id = update.effective_user.id
    settings = StorageSettings.get_settings(user_id)
    current = settings.get("storage_type", "local")
    
    keyboard = [
        [InlineKeyboardButton(
            f"{'✅ ' if current == 'local' else ''}📱 Bot Storage (Default)",
            callback_data="storage:local"
        )],
        [InlineKeyboardButton(
            f"{'✅ ' if current == 'airtable' else ''}📊 Airtable",
            callback_data="storage:airtable"
        )],
        [InlineKeyboardButton(
            f"{'✅ ' if current == 'sheets' else ''}📄 Google Sheets",
            callback_data="storage:sheets"
        )],
        [InlineKeyboardButton(
            f"{'✅ ' if current == 'drive' else ''}📁 Google Drive",
            callback_data="storage:drive"
        )],
        [InlineKeyboardButton("❌ Cancel", callback_data="storage:cancel")]
    ]
    
    await update.message.reply_text(
        f"⚙️ **Storage Settings**\n\n**Current:** {current.title()}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def storage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle storage selection"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    choice = query.data.split(":")[1]
    
    if choice == "cancel":
        await query.edit_message_text("Settings cancelled ❌")
        return
    
    if choice == "local":
        StorageSettings.reset_to_local(user_id)
        await query.edit_message_text("✅ **Storage set to Bot Storage**", parse_mode="Markdown")
        return
    
    if choice == "airtable":
        user_setup_state[user_id] = {"type": "airtable", "step": 1}
        await query.edit_message_text(
            "📊 **Airtable Setup**\n\n"
            "**Step 1/3:** Send me your **API Key**\n"
            "(starts with `pat` or `key`)\n\n"
            "Or /cancel to go back",
            parse_mode="Markdown"
        )
        return
    
    if choice == "sheets":
        user_setup_state[user_id] = {"type": "sheets", "step": 1}
        await query.edit_message_text(
            "📄 **Google Sheets Setup**\n\n"
            "Send me your **Sheet ID** from the URL\n\n"
            "Or /cancel to go back",
            parse_mode="Markdown"
        )
        return

    if choice == "drive":
        # Drive ไม่มีขั้นตอนถามค่าในแชตเลย ต่างจาก Airtable/Sheets — ผู้ใช้กดลิงก์
        # ยินยอมแล้วเซิร์ฟเวอร์รับ token เอง ไม่มี credential ผ่านแชต
        consent = line_webhook.google_consent_url(user_id)
        if not consent:
            await query.edit_message_text(
                "📁 **Google Drive**\n\n"
                "ยังใช้ไม่ได้ — เจ้าของบอทต้องตั้ง `GOOGLE_CLIENT_ID`, "
                "`GOOGLE_CLIENT_SECRET` และ `PUBLIC_BASE_URL` ก่อน",
                parse_mode="Markdown"
            )
            return
        # ส่ง URL เป็นปุ่ม ไม่ใช่ลิงก์ Markdown — state ที่เซ็นไว้เป็น base64url ซึ่งมี
        # "_" ได้ และ urlencode ไม่ escape "_" ให้ พอวางลงใน [ข้อความ](url) ของ
        # Markdown โหมดเก่า underscore ที่ไม่จับคู่จะทำให้ Telegram ปฏิเสธทั้งข้อความ
        await query.edit_message_text(
            "📁 **Google Drive**\n\n"
            "กดปุ่มข้างล่างเพื่ออนุญาต\n\n"
            "ลิงก์หมดอายุใน 15 นาที และบอทเห็นเฉพาะไฟล์ที่ตัวเองสร้างเท่านั้น\n"
            "อนุญาตเสร็จแล้วพิมพ์ /mystorage เพื่อตรวจสอบ",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔗 อนุญาต Google Drive", url=consent)]]
            ),
        )
        return


async def mystorage_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current storage configuration"""
    user_id = update.effective_user.id
    settings = StorageSettings.get_settings(user_id)
    storage_type = settings.get("storage_type", "local")
    
    icons = {"local": "📱", "airtable": "📊", "sheets": "📄", "drive": "📁"}
    
    text = f"{icons.get(storage_type, '📱')} **Your Storage: {storage_type.title()}**\n\n"
    
    # ค่าเริ่มต้นของ .get() ใช้ได้แค่ตอน "ไม่มีคีย์" แต่คอลัมน์ในฐานข้อมูลเป็น NULL
    # ได้ ซึ่งคืน None มา แล้ว None[:20] โยน TypeError — เกิดจริงเมื่อผู้ใช้เลือก
    # sheets แล้วยังไม่ได้ใส่ id ส่วน escape_code(None) ก็คืนช่องว่างเปล่า ๆ
    if storage_type == "airtable":
        base_id = settings.get("airtable_base_id") or "ยังไม่ได้ตั้ง"
        text += f"Base: `{escape_code(base_id)}`"
    elif storage_type == "sheets":
        sheet_id = settings.get("google_sheet_id")
        text += f"Sheet: `{escape_code(sheet_id[:20] + '...' if sheet_id else 'ยังไม่ได้ตั้ง')}`"
    elif storage_type == "drive":
        # ไม่แสดง refresh token ออกมาเด็ดขาด บอกแค่ว่ามีหรือไม่มี
        if settings.get("google_refresh_token"):
            text += f"โฟลเดอร์: `{escape_code(DRIVE_FOLDER_NAME)}`\nสถานะ: เชื่อมต่อแล้ว"
        else:
            text += "สถานะ: ยังไม่ได้อนุญาต — พิมพ์ /settings แล้วเลือก Google Drive"

    text += "\n\n💡 Use /settings to change"
    
    await update.message.reply_text(text, parse_mode="Markdown")


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set preferred language"""
    keyboard = []
    row = []
    for i, (code, name) in enumerate(list(LANGUAGES.items())[:12]):
        row.append(InlineKeyboardButton(f"{name}", callback_data=f"lang:{code}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    
    await update.message.reply_text(
        "🌐 **Select Your Language:**",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle language selection"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    lang_code = query.data.split(":")[1]
    
    StorageSettings.set_language(user_id, lang_code)
    
    await query.edit_message_text(
        f"✅ Language set to **{LANGUAGES.get(lang_code, lang_code)}**",
        parse_mode="Markdown"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# MESSAGE HANDLER
# ═══════════════════════════════════════════════════════════════════════════════

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ยกเลิกการตั้งค่า storage ที่ค้างอยู่

    ระหว่างตั้งค่า Airtable/Sheets ข้อความธรรมดาทุกข้อความจะถูกกลืนไปเป็นคำตอบ
    ของขั้นตอนนั้น เดิมไม่มีทางออกเลยนอกจากตั้งค่าให้จบหรือรอรีสตาร์ต
    """
    user_id = update.effective_user.id

    if user_setup_state.pop(user_id, None) is None:
        await update.message.reply_text("ไม่มีอะไรให้ยกเลิก 👍")
        return

    await update.message.reply_text("ยกเลิกการตั้งค่าแล้ว ❌")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages"""
    user_id = update.effective_user.id
    text = update.message.text
    
    # Check if in setup mode
    if user_id in user_setup_state:
        state = user_setup_state[user_id]
        
        # Airtable setup
        if state["type"] == "airtable":
            if state["step"] == 1:
                state["api_key"] = text
                state["step"] = 2
                await update.message.reply_text(
                    "✅ API Key received!\n\n**Step 2/3:** Send your **Base ID** (starts with `app`)",
                    parse_mode="Markdown"
                )
            elif state["step"] == 2:
                state["base_id"] = text
                state["step"] = 3
                await update.message.reply_text(
                    "✅ Base ID received!\n\n**Step 3/3:** Send your **Table Name** (default: Tasks)",
                    parse_mode="Markdown"
                )
            elif state["step"] == 3:
                table_name = text or "Tasks"
                await update.message.reply_text("🔄 Testing connection...")
                
                client_at = AirtableClient(state["api_key"], state["base_id"], table_name)
                result = await client_at.test_connection()
                
                if result["success"]:
                    StorageSettings.set_airtable(user_id, state["api_key"], state["base_id"], table_name)
                    await update.message.reply_text(f"✅ **Airtable Connected!**\n\n{escape_md(result['message'])}", parse_mode="Markdown")
                else:
                    await update.message.reply_text(f"❌ **Failed**\n\n{escape_md(result['message'])}", parse_mode="Markdown")
                
                user_setup_state.pop(user_id, None)
            return
        
        # Sheets setup
        if state["type"] == "sheets":
            import re
            if "docs.google.com/spreadsheets" in text:
                match = re.search(r'/d/([a-zA-Z0-9-_]+)', text)
                if match:
                    text = match.group(1)
            
            await update.message.reply_text("🔄 Testing connection...")
            
            sheets_client = GoogleSheetsClient(text)
            result = await sheets_client.test_connection()
            
            if result["success"]:
                StorageSettings.set_google_sheets(user_id, text)
                await update.message.reply_text(f"✅ **Google Sheets Connected!**", parse_mode="Markdown")
            else:
                await update.message.reply_text(f"❌ **Failed**\n\n{escape_md(result['message'])}", parse_mode="Markdown")
            
            user_setup_state.pop(user_id, None)
            return
    
    # Default: just acknowledge
    await update.message.reply_text(
        "💡 Try:\n"
        "• `/task <title>` - Add task\n"
        "• `/tr th Hello` - Translate\n"
        "• Send voice → Transcription",
        parse_mode="Markdown"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# REMINDER DELIVERY
# ═══════════════════════════════════════════════════════════════════════════════

# ถี่แค่ไหนถึงจะไปดูว่ามีอะไรครบกำหนด — /remind รับหน่วยเล็กสุดเป็นนาที
DEFAULT_REMINDER_POLL_SECONDS = 30


def _reminder_poll_seconds() -> int:
    """อ่าน REMINDER_POLL_SECONDS โดยไม่ยอมให้ค่าที่พิมพ์ผิดล้มทั้งบอท

    int() ตรง ๆ ตอน import แปลว่าพิมพ์ '30s' ไว้ในหน้า Variables แล้วบอททั้งตัว
    บูตไม่ขึ้น ทั้งที่เรื่องที่ผิดคือความถี่ของการตรวจการเตือนเท่านั้น

    0 หรือค่าติดลบทำให้ APScheduler ยิงรัวไม่หยุด จึงบังคับขั้นต่ำหนึ่งวินาที
    """
    raw = os.getenv("REMINDER_POLL_SECONDS")
    if raw is None:
        return DEFAULT_REMINDER_POLL_SECONDS
    try:
        seconds = int(raw)
    except ValueError:
        logger.warning(
            "REMINDER_POLL_SECONDS=%r ไม่ใช่ตัวเลข ใช้ %d วินาทีแทน",
            raw, DEFAULT_REMINDER_POLL_SECONDS,
        )
        return DEFAULT_REMINDER_POLL_SECONDS
    if seconds < 1:
        logger.warning("REMINDER_POLL_SECONDS=%d น้อยเกินไป ใช้ 1 วินาทีแทน", seconds)
        return 1
    return seconds


REMINDER_POLL_SECONDS = _reminder_poll_seconds()

_reminder_scheduler: Optional[AsyncIOScheduler] = None


async def deliver_due_reminders(telegram_bot) -> int:
    """ส่งการเตือนที่ถึงเวลาแล้ว คืนจำนวนที่ส่งสำเร็จ

    ส่งเป็นข้อความธรรมดา ไม่ใช้ parse_mode โดยตั้งใจ — เนื้อความมาจากผู้ใช้
    ถ้าใช้ Markdown แล้วมี * _ ` [ ที่ไม่จับคู่ Telegram จะตอบ 400 แล้วการเตือน
    นั้นก็หายไปเลย ซึ่งแย่กว่าการที่ตัวหนาไม่ขึ้นมาก

    ทำเครื่องหมายว่าส่งแล้ว "หลัง" ส่งสำเร็จเท่านั้น ถ้าโปรเซสตายคาระหว่างนั้น
    ผู้ใช้จะได้ซ้ำหนึ่งครั้งตอนบูตใหม่ — ยอมให้ซ้ำ ดีกว่ายอมให้หาย
    """
    from telegram.error import Forbidden, TelegramError

    delivered = 0
    for reminder in await Storage.get_due_reminders():
        try:
            await telegram_bot.send_message(
                chat_id=reminder["user_id"],
                text=f"⏰ เตือนความจำ\n\n{reminder['text']}",
            )
        except Forbidden:
            # ผู้ใช้บล็อกบอทหรือลบแชททิ้ง ลองใหม่กี่รอบก็ไม่มีทางผ่าน
            logger.warning("ส่งการเตือน %s ไม่ได้ ผู้ใช้บล็อกบอท", reminder["id"])
            await Storage.mark_reminder(reminder["id"], "failed")
        except TelegramError as exc:
            # ขัดข้องชั่วคราว — ปล่อยไว้เป็น pending ให้รอบหน้าลองใหม่
            logger.error("ส่งการเตือน %s ไม่สำเร็จ: %s", reminder["id"], exc)
        else:
            await Storage.mark_reminder(reminder["id"], "sent")
            delivered += 1
    return delivered


def start_reminder_scheduler(application: Application) -> AsyncIOScheduler:
    """เริ่มตัวเดินเวลาที่คอยส่งการเตือน

    ต้องเรียกให้ชัดเจนจากคนที่บูตบอท ไม่ใช่ผ่าน post_init ของ PTB เพราะ app.py
    ใช้ initialize() + start_polling() ซึ่ง "ไม่" เรียก post_init (มีแต่
    run_polling/run_webhook ที่เรียก) วางไว้ตรงนั้นแล้วมันจะเงียบไปเฉย ๆ

    coalesce กับ max_instances=1 กันไม่ให้รอบที่ค้างสะสมแล้วยิงรัวพร้อมกัน
    """
    global _reminder_scheduler

    # เรียกซ้ำโดยไม่ปิดตัวเก่า = มีสองตัววิ่งพร้อมกัน ผู้ใช้ได้การเตือนซ้ำ และ
    # ตัวเก่าก็ไม่มีใครอ้างถึงอีกจึงปิดไม่ได้ด้วย
    if _reminder_scheduler is not None:
        logger.warning("ตัวส่งการเตือนเริ่มอยู่แล้ว — ปิดตัวเดิมก่อนเริ่มใหม่")
        stop_reminder_scheduler()

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        deliver_due_reminders,
        "interval",
        seconds=REMINDER_POLL_SECONDS,
        args=[application.bot],
        coalesce=True,
        max_instances=1,
        id="deliver_due_reminders",
    )
    scheduler.start()
    _reminder_scheduler = scheduler
    logger.info("ตัวส่งการเตือนเริ่มแล้ว ตรวจทุก %d วินาที", REMINDER_POLL_SECONDS)
    return scheduler


def stop_reminder_scheduler() -> None:
    global _reminder_scheduler

    if _reminder_scheduler is None:
        return
    _reminder_scheduler.remove_all_jobs()
    _reminder_scheduler.shutdown(wait=False)
    _reminder_scheduler = None
    logger.info("ตัวส่งการเตือนหยุดแล้ว")


def build_application() -> Application:
    """Build the Telegram application with every handler registered.

    Separate from main() so app.py can run it inside its own event loop
    alongside the LINE webhook.
    """
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("task", task_command))
    app.add_handler(CommandHandler("tasks", tasks_command))
    app.add_handler(CommandHandler("done", done_command))
    app.add_handler(CommandHandler("remind", remind_command))
    app.add_handler(CommandHandler("reminders", reminders_command))
    app.add_handler(CommandHandler("note", note_command))
    app.add_handler(CommandHandler("notes", notes_command))
    app.add_handler(CommandHandler("tr", translate_command))
    app.add_handler(CommandHandler("translate", translate_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("mystorage", mystorage_command))
    app.add_handler(CommandHandler("language", language_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    
    # Callback handlers
    app.add_handler(CallbackQueryHandler(storage_callback, pattern="^storage:"))
    app.add_handler(CallbackQueryHandler(language_callback, pattern="^lang:"))
    
    # Voice handler
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    # ไฟล์ประวัติแชทที่ export มา
    app.add_handler(MessageHandler(filters.Document.ALL, handle_chat_export))
    
    # Text handler (last)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    return app


async def _post_init(application: Application) -> None:
    start_reminder_scheduler(application)


async def _post_shutdown(application: Application) -> None:
    stop_reminder_scheduler()


def main():
    """Start the bot on its own (app.py runs it together with the webhook)"""
    init_db()
    app = build_application()
    # run_polling เรียก post_init/post_shutdown ให้ ต่างจากเส้นทางของ app.py
    app.post_init = _post_init
    app.post_shutdown = _post_shutdown

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
