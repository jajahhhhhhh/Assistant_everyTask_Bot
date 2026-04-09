"""
Assistant_everyTask_Bot - With User Storage Settings
Users can connect their own Airtable, Google Sheets, or Google Drive!
"""

import os
import sqlite3
import logging
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, ContextTypes
)
import openai
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

# OpenAI setup
if OPENAI_API_KEY:
    openai.api_key = OPENAI_API_KEY

# Conversation states for settings
(AWAITING_STORAGE_CHOICE, AWAITING_AIRTABLE_KEY, AWAITING_AIRTABLE_BASE, 
 AWAITING_AIRTABLE_TABLE, AWAITING_SHEETS_ID) = range(5)


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
    
    # Calendar events table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            start_time TIMESTAMP NOT NULL,
            end_time TIMESTAMP,
            location TEXT,
            description TEXT,
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
            google_drive_folder_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                   airtable_table_name, google_sheet_id
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
                "google_sheet_id": row[4]
            }
        return {"storage_type": "local"}
    
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
        """Test if connection works"""
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
    
    async def add_task(self, user_id: int, title: str, priority: str = "medium",
                       due_date: str = None) -> bool:
        """Add a task to Airtable"""
        fields = {
            "Title": title,
            "User ID": str(user_id),
            "Priority": priority,
            "Status": "todo",
            "Created": datetime.now().isoformat()
        }
        if due_date:
            fields["Due Date"] = due_date
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.url, headers=self.headers, json={"fields": fields}
                ) as response:
                    return response.status == 200
        except:
            return False
    
    async def get_tasks(self, user_id: int) -> List[Dict]:
        """Get tasks from Airtable"""
        try:
            params = {"filterByFormula": f"{{User ID}} = '{user_id}'"}
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.url, headers=self.headers, params=params
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return [
                            {
                                "id": r["id"],
                                "title": r["fields"].get("Title", ""),
                                "priority": r["fields"].get("Priority", "medium"),
                                "status": r["fields"].get("Status", "todo"),
                                "due_date": r["fields"].get("Due Date")
                            }
                            for r in data.get("records", [])
                        ]
            return []
        except:
            return []


# ═══════════════════════════════════════════════════════════════════════════════
# GOOGLE SHEETS INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════════

class GoogleSheetsClient:
    """Simple Google Sheets client (public sheets only)"""
    
    API_URL = "https://sheets.googleapis.com/v4/spreadsheets"
    
    def __init__(self, sheet_id: str):
        self.sheet_id = sheet_id
    
    async def test_connection(self) -> Dict[str, Any]:
        """Test if the sheet is accessible"""
        try:
            # For public sheets, we can test with export URL
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
# UNIFIED STORAGE MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class Storage:
    """Routes storage operations to the correct backend"""
    
    @staticmethod
    def _get_backend(user_id: int):
        settings = StorageSettings.get_settings(user_id)
        storage_type = settings.get("storage_type", "local")
        
        if storage_type == "airtable" and settings.get("airtable_api_key"):
            return AirtableClient(
                settings["airtable_api_key"],
                settings["airtable_base_id"],
                settings.get("airtable_table_name", "Tasks")
            )
        elif storage_type == "sheets" and settings.get("google_sheet_id"):
            return GoogleSheetsClient(settings["google_sheet_id"])
        
        return None  # Use local SQLite
    
    # ─── Task Operations ─────────────────────────────────────────────────────
    
    @staticmethod
    async def add_task(user_id: int, title: str, priority: str = "medium",
                       due_date: str = None, project: str = None) -> int:
        """Add a task - routes to correct backend"""
        backend = Storage._get_backend(user_id)
        
        if isinstance(backend, AirtableClient):
            success = await backend.add_task(user_id, title, priority, due_date)
            if success:
                return 1  # Return dummy ID for external storage
        
        # Default: local SQLite
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
        """Get tasks - routes to correct backend"""
        backend = Storage._get_backend(user_id)
        
        if isinstance(backend, AirtableClient):
            return await backend.get_tasks(user_id)
        
        # Default: local SQLite
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
                "id": row[0],
                "title": row[1],
                "priority": row[2],
                "status": row[3],
                "due_date": row[4],
                "project": row[5],
                "created_at": row[6]
            }
            for row in rows
        ]
    
    @staticmethod
    async def complete_task(user_id: int, task_id: int) -> bool:
        """Mark task as complete"""
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
    
    # ─── Reminder Operations ─────────────────────────────────────────────────
    
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
    
    # ─── Note Operations ─────────────────────────────────────────────────────
    
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
    async def get_notes(user_id: int, search: str = None) -> List[Dict]:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        if search:
            cursor.execute("""
                SELECT id, content, tags, created_at
                FROM notes WHERE user_id = ? AND content LIKE ?
                ORDER BY created_at DESC
            """, (user_id, f"%{search}%"))
        else:
            cursor.execute("""
                SELECT id, content, tags, created_at
                FROM notes WHERE user_id = ?
                ORDER BY created_at DESC
            """, (user_id,))
        rows = cursor.fetchall()
        conn.close()
        return [{"id": r[0], "content": r[1], "tags": r[2], "created_at": r[3]} for r in rows]


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
👋 **สวัสดี {user.first_name}!** / Hello!

ฉันคือ **Assistant Bot** ผู้ช่วยจัดการงานส่วนตัวของคุณ!

🇹🇭 **คำสั่งภาษาไทย:**
• `/task ซื้อของ` - เพิ่มงาน
• `/remind 30m โทรหาลูกค้า` - ตั้งเตือน
• `/note บันทึกสำคัญ` - จดโน้ต

🇬🇧 **English Commands:**
• `/task Buy groceries` - Add task
• `/remind 1h Call client` - Set reminder  
• `/note Important meeting points` - Save note

⚙️ **Storage Settings:**
• `/settings` - Connect your own Airtable, Google Sheets, or Drive!

📋 **More Commands:**
• `/tasks` - View all tasks
• `/done 1` - Complete task #1
• `/reminders` - View reminders
• `/notes` - View notes
• `/help` - Full help

💡 Or just type naturally: "remind me to call mom in 2 hours"
"""
    
    await update.message.reply_text(text, parse_mode="Markdown")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    text = """
📖 **คู่มือการใช้งาน / User Guide**

━━━━━━━━━━━━━━━━━━━━
✅ **Tasks / งาน**
━━━━━━━━━━━━━━━━━━━━
`/task <title>` - เพิ่มงาน / Add task
`/tasks` - ดูรายการงาน / List tasks
`/done <id>` - เสร็จงาน / Complete task
`/delete <id>` - ลบงาน / Delete task

━━━━━━━━━━━━━━━━━━━━
⏰ **Reminders / เตือนความจำ**
━━━━━━━━━━━━━━━━━━━━
`/remind <time> <text>` - ตั้งเตือน
Examples:
• `/remind 30m call mom`
• `/remind 2h meeting`
• `/remind 1d birthday`

━━━━━━━━━━━━━━━━━━━━
📝 **Notes / บันทึก**
━━━━━━━━━━━━━━━━━━━━
`/note <content>` - บันทึกโน้ต
`/notes` - ดูโน้ตทั้งหมด
`/search <keyword>` - ค้นหาโน้ต

━━━━━━━━━━━━━━━━━━━━
⚙️ **Settings / ตั้งค่า**
━━━━━━━━━━━━━━━━━━━━
`/settings` - เชื่อมต่อ Airtable/Sheets
`/mystorage` - ดูการตั้งค่าปัจจุบัน

━━━━━━━━━━━━━━━━━━━━
🤖 **AI / ฉลาด**
━━━━━━━━━━━━━━━━━━━━
Just type naturally!
• "remind me to buy milk tomorrow"
• "add task call client urgent"
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
            "📝 Please provide a task title:\n"
            "`/task Buy groceries`",
            parse_mode="Markdown"
        )
        return
    
    title = " ".join(context.args)
    
    # Detect priority from text
    priority = "medium"
    title_lower = title.lower()
    if any(w in title_lower for w in ["urgent", "ด่วน", "asap"]):
        priority = "urgent"
    elif any(w in title_lower for w in ["important", "สำคัญ", "high"]):
        priority = "high"
    elif any(w in title_lower for w in ["low", "ต่ำ", "later"]):
        priority = "low"
    
    task_id = await Storage.add_task(user_id, title, priority)
    
    settings = StorageSettings.get_settings(user_id)
    storage_icon = {
        "local": "📱",
        "airtable": "📊",
        "sheets": "📄",
        "drive": "📁"
    }.get(settings.get("storage_type", "local"), "📱")
    
    await update.message.reply_text(
        f"✅ **Task Added!**\n\n"
        f"📋 {title}\n"
        f"🔴 Priority: {priority}\n"
        f"{storage_icon} Storage: {settings.get('storage_type', 'local').title()}\n\n"
        f"Use `/done {task_id}` to complete",
        parse_mode="Markdown"
    )


async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all tasks"""
    user_id = update.effective_user.id
    tasks = await Storage.get_tasks(user_id)
    
    if not tasks:
        await update.message.reply_text(
            "📭 No tasks yet!\n\n"
            "Add one with `/task Buy groceries`",
            parse_mode="Markdown"
        )
        return
    
    # Group by status
    todo = [t for t in tasks if t["status"] == "todo"]
    doing = [t for t in tasks if t["status"] == "doing"]
    done = [t for t in tasks if t["status"] == "done"]
    
    priority_emoji = {
        "urgent": "🔴",
        "high": "🟠",
        "medium": "🟡",
        "low": "🟢"
    }
    
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
        text += f"**✅ Done:** ({len(done)} tasks)\n"
    
    text += f"\n📊 Total: {len(tasks)} tasks"
    
    await update.message.reply_text(text, parse_mode="Markdown")


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mark a task as done"""
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text(
            "Please provide task ID:\n"
            "`/done 1`",
            parse_mode="Markdown"
        )
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
            "⏰ **How to set reminders:**\n\n"
            "`/remind 30m Call mom`\n"
            "`/remind 2h Meeting`\n"
            "`/remind 1d Birthday`\n\n"
            "Time formats: `m` (minutes), `h` (hours), `d` (days)",
            parse_mode="Markdown"
        )
        return
    
    time_str = context.args[0].lower()
    text = " ".join(context.args[1:])
    
    # Parse time
    now = datetime.now()
    remind_at = now
    
    try:
        if time_str.endswith("m"):
            minutes = int(time_str[:-1])
            remind_at = now + timedelta(minutes=minutes)
        elif time_str.endswith("h"):
            hours = int(time_str[:-1])
            remind_at = now + timedelta(hours=hours)
        elif time_str.endswith("d"):
            days = int(time_str[:-1])
            remind_at = now + timedelta(days=days)
        else:
            raise ValueError("Invalid format")
    except:
        await update.message.reply_text(
            "❌ Invalid time format\n\n"
            "Use: `30m`, `2h`, `1d`",
            parse_mode="Markdown"
        )
        return
    
    reminder_id = await Storage.add_reminder(user_id, text, remind_at)
    
    await update.message.reply_text(
        f"⏰ **Reminder Set!**\n\n"
        f"📝 {text}\n"
        f"🕐 {remind_at.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"I'll remind you! 🔔",
        parse_mode="Markdown"
    )


async def reminders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all reminders"""
    user_id = update.effective_user.id
    reminders = await Storage.get_reminders(user_id)
    
    if not reminders:
        await update.message.reply_text(
            "🔔 No active reminders\n\n"
            "Set one with `/remind 30m Call mom`",
            parse_mode="Markdown"
        )
        return
    
    text = "⏰ **Your Reminders**\n\n"
    for r in reminders:
        text += f"🔔 `{r['id']}` {r['text']}\n"
        text += f"   📅 {r['remind_at']}\n\n"
    
    await update.message.reply_text(text, parse_mode="Markdown")


# ═══════════════════════════════════════════════════════════════════════════════
# NOTE COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

async def note_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save a note"""
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text(
            "📝 Please provide note content:\n"
            "`/note Meeting notes here`",
            parse_mode="Markdown"
        )
        return
    
    content = " ".join(context.args)
    note_id = await Storage.add_note(user_id, content)
    
    await update.message.reply_text(
        f"📝 **Note Saved!**\n\n"
        f"ID: `{note_id}`\n"
        f"Content: {content[:100]}{'...' if len(content) > 100 else ''}",
        parse_mode="Markdown"
    )


async def notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all notes"""
    user_id = update.effective_user.id
    notes = await Storage.get_notes(user_id)
    
    if not notes:
        await update.message.reply_text(
            "📝 No notes yet!\n\n"
            "Save one with `/note Your note here`",
            parse_mode="Markdown"
        )
        return
    
    text = "📝 **Your Notes**\n\n"
    for n in notes[:10]:
        preview = n["content"][:50] + "..." if len(n["content"]) > 50 else n["content"]
        text += f"`{n['id']}` {preview}\n"
    
    if len(notes) > 10:
        text += f"\n... and {len(notes) - 10} more"
    
    await update.message.reply_text(text, parse_mode="Markdown")


# ═══════════════════════════════════════════════════════════════════════════════
# SETTINGS COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show storage settings menu"""
    user_id = update.effective_user.id
    settings = StorageSettings.get_settings(user_id)
    current = settings.get("storage_type", "local")
    
    text = f"""
⚙️ **Storage Settings**

**Current:** {current.title()}

Choose where to save your data:
"""
    
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
        text,
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
        await query.edit_message_text(
            "✅ **Storage set to Bot Storage**\n\n"
            "Your data is stored securely in the bot.",
            parse_mode="Markdown"
        )
        return
    
    if choice == "airtable":
        user_setup_state[user_id] = {"type": "airtable", "step": 1}
        
        await query.edit_message_text(
            "📊 **Airtable Setup**\n\n"
            "**Step 1/3:** Get your API Key\n\n"
            "1. Go to airtable.com/account\n"
            "2. Create a Personal Access Token\n"
            "3. Give it read/write access\n\n"
            "📝 **Send me your API Key:**\n"
            "(or /cancel to go back)",
            parse_mode="Markdown"
        )
        return
    
    if choice == "sheets":
        user_setup_state[user_id] = {"type": "sheets", "step": 1}
        
        await query.edit_message_text(
            "📄 **Google Sheets Setup**\n\n"
            "1. Create a Google Sheet\n"
            "2. Share it as 'Anyone with link can edit'\n"
            "3. Copy the Sheet ID from URL:\n"
            "   `docs.google.com/spreadsheets/d/`**SHEET_ID**`/edit`\n\n"
            "📝 **Send me your Sheet ID:**\n"
            "(or /cancel to go back)",
            parse_mode="Markdown"
        )
        return


async def handle_setup_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle input during storage setup"""
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    if text.lower() == "/cancel":
        user_setup_state.pop(user_id, None)
        await update.message.reply_text("Setup cancelled ❌\n\nUse /settings to try again.")
        return
    
    if user_id not in user_setup_state:
        return  # Not in setup mode
    
    state = user_setup_state[user_id]
    
    # ─── Airtable Setup ──────────────────────────────────────────────────────
    if state["type"] == "airtable":
        if state["step"] == 1:  # API Key
            if not text.startswith(("pat", "key")):
                await update.message.reply_text(
                    "⚠️ That doesn't look like a valid API key.\n"
                    "It should start with `pat` or `key`.\n\n"
                    "Please try again or /cancel"
                )
                return
            
            state["api_key"] = text
            state["step"] = 2
            
            await update.message.reply_text(
                "✅ API Key received!\n\n"
                "**Step 2/3:** Send me your **Base ID**\n"
                "(starts with `app`, from the URL)",
                parse_mode="Markdown"
            )
            return
        
        elif state["step"] == 2:  # Base ID
            if not text.startswith("app"):
                await update.message.reply_text(
                    "⚠️ Base ID should start with `app`.\n\n"
                    "Please try again or /cancel"
                )
                return
            
            state["base_id"] = text
            state["step"] = 3
            
            await update.message.reply_text(
                "✅ Base ID received!\n\n"
                "**Step 3/3:** Send me your **Table Name**\n"
                "(default: `Tasks`)",
                parse_mode="Markdown"
            )
            return
        
        elif state["step"] == 3:  # Table Name
            table_name = text or "Tasks"
            
            await update.message.reply_text("🔄 Testing connection...")
            
            client = AirtableClient(state["api_key"], state["base_id"], table_name)
            result = await client.test_connection()
            
            if result["success"]:
                StorageSettings.set_airtable(
                    user_id, state["api_key"], state["base_id"], table_name
                )
                user_setup_state.pop(user_id, None)
                
                await update.message.reply_text(
                    f"✅ **Airtable Connected!**\n\n"
                    f"📊 Base: `{state['base_id']}`\n"
                    f"📋 Table: `{table_name}`\n\n"
                    f"Your data will now sync to Airtable! 🎉",
                    parse_mode="Markdown"
                )
            else:
                await update.message.reply_text(
                    f"❌ **Connection Failed**\n\n"
                    f"{result['message']}\n\n"
                    f"Use /settings to try again."
                )
                user_setup_state.pop(user_id, None)
            return
    
    # ─── Google Sheets Setup ─────────────────────────────────────────────────
    elif state["type"] == "sheets":
        # Extract ID if full URL provided
        if "docs.google.com/spreadsheets" in text:
            import re
            match = re.search(r'/d/([a-zA-Z0-9-_]+)', text)
            if match:
                text = match.group(1)
        
        if len(text) < 20:
            await update.message.reply_text(
                "⚠️ Invalid Sheet ID.\n\n"
                "Please try again or /cancel"
            )
            return
        
        await update.message.reply_text("🔄 Testing connection...")
        
        client = GoogleSheetsClient(text)
        result = await client.test_connection()
        
        if result["success"]:
            StorageSettings.set_google_sheets(user_id, text)
            user_setup_state.pop(user_id, None)
            
            await update.message.reply_text(
                f"✅ **Google Sheets Connected!**\n\n"
                f"📄 Sheet ID: `{text[:20]}...`\n\n"
                f"Your data will now sync to Google Sheets! 🎉",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                f"❌ **Connection Failed**\n\n"
                f"{result['message']}\n\n"
                f"Use /settings to try again."
            )
            user_setup_state.pop(user_id, None)


async def mystorage_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current storage configuration"""
    user_id = update.effective_user.id
    settings = StorageSettings.get_settings(user_id)
    storage_type = settings.get("storage_type", "local")
    
    icons = {
        "local": "📱",
        "airtable": "📊",
        "sheets": "📄",
        "drive": "📁"
    }
    
    text = f"{icons.get(storage_type, '📱')} **Your Storage: {storage_type.title()}**\n\n"
    
    if storage_type == "local":
        text += "Data is stored in the bot's database."
    elif storage_type == "airtable":
        text += f"Base: `{settings.get('airtable_base_id', 'N/A')}`\n"
        text += f"Table: `{settings.get('airtable_table_name', 'Tasks')}`"
    elif storage_type == "sheets":
        sheet_id = settings.get("google_sheet_id", "N/A")
        text += f"Sheet: `{sheet_id[:20]}...`"
    
    text += "\n\n💡 Use /settings to change"
    
    await update.message.reply_text(text, parse_mode="Markdown")


# ═══════════════════════════════════════════════════════════════════════════════
# NATURAL LANGUAGE HANDLER
# ═══════════════════════════════════════════════════════════════════════════════

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle natural language messages"""
    user_id = update.effective_user.id
    text = update.message.text
    
    # Check if in setup mode
    if user_id in user_setup_state:
        await handle_setup_input(update, context)
        return
    
    # Simple intent detection
    text_lower = text.lower()
    
    # Reminder patterns
    if any(word in text_lower for word in ["remind", "เตือน", "reminder"]):
        # Try to parse with AI or simple patterns
        await update.message.reply_text(
            "⏰ To set a reminder, use:\n"
            "`/remind 30m <your reminder>`\n\n"
            "Examples:\n"
            "• `/remind 1h Call client`\n"
            "• `/remind 2d Birthday party`",
            parse_mode="Markdown"
        )
        return
    
    # Task patterns
    if any(word in text_lower for word in ["task", "todo", "add", "งาน", "เพิ่ม"]):
        # Extract task from message
        context.args = text.split()[1:] if len(text.split()) > 1 else []
        if context.args:
            await task_command(update, context)
        else:
            await update.message.reply_text(
                "📝 To add a task, use:\n"
                "`/task <task description>`",
                parse_mode="Markdown"
            )
        return
    
    # Default response
    await update.message.reply_text(
        "🤖 I'm not sure what you need.\n\n"
        "Try these commands:\n"
        "• `/task <title>` - Add task\n"
        "• `/remind 30m <text>` - Set reminder\n"
        "• `/note <content>` - Save note\n"
        "• `/help` - Full guide",
        parse_mode="Markdown"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    """Start the bot"""
    # Initialize database
    init_db()
    
    # Create application
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    
    # Task commands
    app.add_handler(CommandHandler("task", task_command))
    app.add_handler(CommandHandler("tasks", tasks_command))
    app.add_handler(CommandHandler("done", done_command))
    
    # Reminder commands
    app.add_handler(CommandHandler("remind", remind_command))
    app.add_handler(CommandHandler("reminders", reminders_command))
    
    # Note commands
    app.add_handler(CommandHandler("note", note_command))
    app.add_handler(CommandHandler("notes", notes_command))
    
    # Settings commands
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("mystorage", mystorage_command))
    app.add_handler(CallbackQueryHandler(storage_callback, pattern="^storage:"))
    
    # Natural language handler (must be last)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Start scheduler for reminders
    scheduler = AsyncIOScheduler()
    # Add reminder check job here if needed
    scheduler.start()
    
    # Start polling
    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
