"""
Assistant_everyTask_Bot - Enhanced Version
Features: Tasks, Reminders, Notes, Storage Settings, Translation, Voice Transcription
"""

import os
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

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATA_DIR = os.getenv("DATA_DIR", "data")
DB_PATH = f"{DATA_DIR}/assistant.db"

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

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
# DATABASE SETUP
# ═══════════════════════════════════════════════════════════════════════════════

def init_db():
    """Initialize the SQLite database with all tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Tasks table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            priority TEXT DEFAULT 'medium',
            status TEXT DEFAULT 'todo',
            due_date TEXT,
            project TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
    """)
    
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
                   airtable_table_name, google_sheet_id, preferred_language
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
                "preferred_language": row[5] or "en"
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
            return {"success": False, "message": f"❌ Error: {str(e)}"}


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
            return {"success": False, "message": f"❌ Error: {str(e)}"}


# ═══════════════════════════════════════════════════════════════════════════════
# STORAGE MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class Storage:
    """Routes storage operations to the correct backend"""
    
    @staticmethod
    async def add_task(user_id: int, title: str, priority: str = "medium",
                       due_date: str = None, project: str = None) -> int:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO tasks (user_id, title, priority, due_date, project)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, title, priority, due_date, project))
        task_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return task_id
    
    @staticmethod
    async def get_tasks(user_id: int, status: str = None) -> List[Dict]:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        if status:
            cursor.execute("""
                SELECT id, title, priority, status, due_date, project, created_at
                FROM tasks WHERE user_id = ? AND status = ?
                ORDER BY created_at DESC
            """, (user_id, status))
        else:
            cursor.execute("""
                SELECT id, title, priority, status, due_date, project, created_at
                FROM tasks WHERE user_id = ?
                ORDER BY created_at DESC
            """, (user_id,))
        
        rows = cursor.fetchall()
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
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE tasks SET status = 'done', completed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ?
        """, (task_id, user_id))
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return affected > 0
    
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
        logger.error(f"Translation error: {e}")
        return f"❌ Translation error: {str(e)}"


async def transcribe_voice(file_path: str) -> str:
    """Transcribe voice message using OpenAI Whisper"""
    if not client:
        return "❌ OpenAI API not configured"
    
    import asyncio
    
    try:
        # Run synchronous OpenAI call in thread pool
        def do_transcribe():
            with open(file_path, "rb") as audio_file:
                return client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    response_format="text"
                )
        
        response = await asyncio.to_thread(do_transcribe)
        return response.strip()
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        return f"❌ Transcription error: {str(e)}"


# ═══════════════════════════════════════════════════════════════════════════════
# SETUP STATE TRACKING
# ═══════════════════════════════════════════════════════════════════════════════

user_setup_state: Dict[int, Dict[str, Any]] = {}


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    
    text = f"""
👋 **สวัสดี {user.first_name}!** Welcome!

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
    
    # Detect priority
    priority = "medium"
    title_lower = title.lower()
    if any(w in title_lower for w in ["urgent", "ด่วน", "asap", "!"]):
        priority = "urgent"
    elif any(w in title_lower for w in ["important", "สำคัญ", "high"]):
        priority = "high"
    elif any(w in title_lower for w in ["low", "ต่ำ", "later"]):
        priority = "low"
    
    task_id = await Storage.add_task(user_id, title, priority)
    
    priority_emoji = {"urgent": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
    
    await update.message.reply_text(
        f"✅ **Task Added!**\n\n"
        f"📋 {title}\n"
        f"{priority_emoji.get(priority, '⚪')} Priority: {priority}\n\n"
        f"Complete with `/done {task_id}`",
        parse_mode="Markdown"
    )


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
    
    todo = [t for t in tasks if t["status"] == "todo"]
    doing = [t for t in tasks if t["status"] == "doing"]
    done = [t for t in tasks if t["status"] == "done"]
    
    priority_emoji = {"urgent": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
    
    text = "📋 **Your Tasks**\n\n"
    
    if todo:
        text += "**📌 To Do:**\n"
        for t in todo[:10]:
            emoji = priority_emoji.get(t["priority"], "⚪")
            text += f"{emoji} `{t['id']}` {t['title']}\n"
        text += "\n"
    
    if doing:
        text += "**⚡ In Progress:**\n"
        for t in doing[:5]:
            text += f"🔵 `{t['id']}` {t['title']}\n"
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
    
    time_str = context.args[0].lower()
    text = " ".join(context.args[1:])
    
    now = datetime.now()
    remind_at = now
    
    try:
        if time_str.endswith("m"):
            remind_at = now + timedelta(minutes=int(time_str[:-1]))
        elif time_str.endswith("h"):
            remind_at = now + timedelta(hours=int(time_str[:-1]))
        elif time_str.endswith("d"):
            remind_at = now + timedelta(days=int(time_str[:-1]))
        else:
            raise ValueError("Invalid format")
    except:
        await update.message.reply_text("❌ Use: `30m`, `2h`, `1d`", parse_mode="Markdown")
        return
    
    await Storage.add_reminder(user_id, text, remind_at)
    
    await update.message.reply_text(
        f"⏰ **Reminder Set!**\n\n"
        f"📝 {text}\n"
        f"🕐 {remind_at.strftime('%Y-%m-%d %H:%M')}",
        parse_mode="Markdown"
    )


async def reminders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List reminders"""
    user_id = update.effective_user.id
    reminders = await Storage.get_reminders(user_id)
    
    if not reminders:
        await update.message.reply_text("🔔 No active reminders")
        return
    
    text = "⏰ **Your Reminders**\n\n"
    for r in reminders:
        text += f"🔔 `{r['id']}` {r['text']}\n   📅 {r['remind_at']}\n\n"
    
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
        text += f"`{n['id']}` {preview}\n"
    
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
            f"❌ Unknown language: `{target_lang}`\n\n"
            f"Available: en, th, zh, ja, ko, vi, id, es, fr, de...",
            parse_mode="Markdown"
        )
        return
    
    await update.message.reply_text("🔄 Translating...")
    
    translated = await translate_text(text, target_lang)
    
    await update.message.reply_text(
        f"🌐 **Translation**\n\n"
        f"📝 Original: {text}\n\n"
        f"🎯 {LANGUAGES[target_lang]}: {translated}",
        parse_mode="Markdown"
    )


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
    
    try:
        # Download the voice file
        file: File = await context.bot.get_file(voice.file_id)
        
        # Create temp file
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as temp_file:
            temp_path = temp_file.name
            await file.download_to_drive(temp_path)
        
        # Transcribe
        transcription = await transcribe_voice(temp_path)
        
        # Clean up
        os.unlink(temp_path)
        
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
            f"📝 {transcription}\n\n"
            f"⏱️ Duration: {voice.duration}s",
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"Voice handling error: {e}")
        await update.message.reply_text(f"❌ Could not transcribe: {str(e)}")


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


async def mystorage_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current storage configuration"""
    user_id = update.effective_user.id
    settings = StorageSettings.get_settings(user_id)
    storage_type = settings.get("storage_type", "local")
    
    icons = {"local": "📱", "airtable": "📊", "sheets": "📄"}
    
    text = f"{icons.get(storage_type, '📱')} **Your Storage: {storage_type.title()}**\n\n"
    
    if storage_type == "airtable":
        text += f"Base: `{settings.get('airtable_base_id', 'N/A')}`"
    elif storage_type == "sheets":
        text += f"Sheet: `{settings.get('google_sheet_id', 'N/A')[:20]}...`"
    
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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages"""
    user_id = update.effective_user.id
    text = update.message.text
    
    # Check if in setup mode
    if user_id in user_setup_state:
        state = user_setup_state[user_id]
        
        if text.lower() == "/cancel":
            user_setup_state.pop(user_id, None)
            await update.message.reply_text("Setup cancelled ❌")
            return
        
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
                    await update.message.reply_text(f"✅ **Airtable Connected!**\n\n{result['message']}", parse_mode="Markdown")
                else:
                    await update.message.reply_text(f"❌ **Failed**\n\n{result['message']}", parse_mode="Markdown")
                
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
                await update.message.reply_text(f"❌ **Failed**\n\n{result['message']}", parse_mode="Markdown")
            
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

def main():
    """Start the bot"""
    init_db()
    
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
    
    # Callback handlers
    app.add_handler(CallbackQueryHandler(storage_callback, pattern="^storage:"))
    app.add_handler(CallbackQueryHandler(language_callback, pattern="^lang:"))
    
    # Voice handler
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    
    # Text handler (last)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
