"""
Auto-scan module — automatic extraction of actionable items from conversations.

Monitors conversations and extracts:
- Tasks
- Appointments
- Reminders
- Ideas/plans
- Property listings

Uses AI to identify actionable items with confidence scoring.
"""

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import config

logger = logging.getLogger(__name__)

# ── Item types ────────────────────────────────────────────────────────────────

ITEM_TYPES = {
    "task": {"icon": "✅", "label": "Task"},
    "appointment": {"icon": "📅", "label": "Appointment"},
    "reminder": {"icon": "🔔", "label": "Reminder"},
    "idea": {"icon": "💡", "label": "Idea"},
    "listing": {"icon": "🏠", "label": "Listing"},
    "project": {"icon": "🚀", "label": "Project"},
}

LANG_FLAGS = {
    "th": "🇹🇭",
    "ru": "🇷🇺",
    "en": "🇬🇧",
}


@dataclass
class ExtractedItem:
    """Represents an extracted actionable item."""
    type: str
    title: str
    detail: str
    who: str
    when: str
    confidence: float
    language: str
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExtractedItem":
        return cls(
            type=data.get("type", "task"),
            title=data.get("title", ""),
            detail=data.get("detail", ""),
            who=data.get("who", "User"),
            when=data.get("when", ""),
            confidence=float(data.get("confidence", 0.5)),
            language=data.get("lang", data.get("language", "en")),
        )


# ── Pending items storage ─────────────────────────────────────────────────────

def ensure_pending_table(conn: sqlite3.Connection) -> None:
    """Create pending_items table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            items_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()


def save_pending_items(
    conn: sqlite3.Connection,
    user_id: int,
    items: List[ExtractedItem],
) -> None:
    """Save pending items for a user."""
    ensure_pending_table(conn)
    
    # Clear old pending items
    conn.execute("DELETE FROM pending_items WHERE user_id = ?", (user_id,))
    
    # Save new items
    if items:
        items_json = json.dumps([item.to_dict() for item in items])
        conn.execute(
            "INSERT INTO pending_items (user_id, items_json, created_at) VALUES (?, ?, ?)",
            (user_id, items_json, datetime.now(timezone.utc).isoformat())
        )
    
    conn.commit()


def get_pending_items(
    conn: sqlite3.Connection,
    user_id: int,
) -> List[ExtractedItem]:
    """Get pending items for a user."""
    ensure_pending_table(conn)
    
    cursor = conn.execute(
        "SELECT items_json FROM pending_items WHERE user_id = ?",
        (user_id,)
    )
    row = cursor.fetchone()
    
    if not row:
        return []
    
    try:
        items_data = json.loads(row[0])
        return [ExtractedItem.from_dict(item) for item in items_data]
    except Exception as exc:
        logger.error("Failed to parse pending items: %s", exc)
        return []


def clear_pending_items(conn: sqlite3.Connection, user_id: int) -> None:
    """Clear pending items for a user."""
    ensure_pending_table(conn)
    conn.execute("DELETE FROM pending_items WHERE user_id = ?", (user_id,))
    conn.commit()


# ── Message counter for auto-scan ─────────────────────────────────────────────

_message_counters: Dict[int, int] = {}
AUTO_SCAN_INTERVAL = 10  # Scan every N messages


def increment_message_count(user_id: int) -> Tuple[int, bool]:
    """
    Increment message count for a user.
    
    Returns:
        Tuple of (current_count, should_auto_scan)
    """
    count = _message_counters.get(user_id, 0) + 1
    _message_counters[user_id] = count
    
    should_scan = count % AUTO_SCAN_INTERVAL == 0
    return count, should_scan


def reset_message_count(user_id: int) -> None:
    """Reset message count for a user."""
    _message_counters[user_id] = 0


# ── AI extraction ─────────────────────────────────────────────────────────────

_openai_client = None


def _get_openai():
    """Get or create OpenAI client."""
    global _openai_client
    if _openai_client is None and config.OPENAI_API_KEY:
        try:
            from openai import OpenAI
            _openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
        except Exception as exc:
            logger.warning("Could not initialise OpenAI client: %s", exc)
    return _openai_client


EXTRACTION_SYSTEM_PROMPT = """You are an extraction AI reading a conversation.
Extract ALL actionable items. Return ONLY valid JSON array:
[{
  "type": "task"|"appointment"|"reminder"|"idea"|"listing"|"project",
  "title": "Brief title in English",
  "detail": "Additional context",
  "who": "User name or 'User'",
  "when": "datetime or deadline or empty",
  "confidence": 0.0-1.0,
  "lang": "th"|"ru"|"en"
}]

Rules:
- Minimum confidence 0.7
- Return [] if nothing found
- No markdown, pure JSON only
- type must be one of: task, appointment, reminder, idea, listing, project"""


def extract_from_messages(
    messages: List[Dict[str, Any]],
    min_confidence: float = 0.7,
) -> List[ExtractedItem]:
    """
    Extract actionable items from messages using AI.
    
    Args:
        messages: List of message dicts with 'content' key
        min_confidence: Minimum confidence threshold
        
    Returns:
        List of extracted items
    """
    if len(messages) < 2:
        return []
    
    client = _get_openai()
    if client is None:
        # Fallback to keyword extraction
        return _keyword_extraction(messages)
    
    # Build conversation context
    conversation = "\n".join([
        f"User: {m.get('content', '')[:200]}"
        for m in messages[-20:]  # Last 20 messages
    ])
    
    try:
        response = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": f"Conversation:\n{conversation}\n\nExtract items:"},
            ],
            temperature=0.2,
            max_tokens=800,
        )
        
        result = response.choices[0].message.content.strip()
        # Clean markdown if present
        result = re.sub(r'```json|```', '', result).strip()
        
        items_data = json.loads(result)
        
        if not isinstance(items_data, list):
            return []
        
        items = []
        for item_data in items_data:
            item = ExtractedItem.from_dict(item_data)
            if item.confidence >= min_confidence and item.title:
                items.append(item)
        
        return items
        
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse extraction result: %s", exc)
        return []
    except Exception as exc:
        logger.error("Extraction failed: %s", exc)
        return []


def _keyword_extraction(messages: List[Dict[str, Any]]) -> List[ExtractedItem]:
    """Fallback keyword-based extraction without AI."""
    items = []
    
    # Patterns for different item types
    task_patterns = [
        r'(?:need to|must|should|have to|todo|task[:\s])\s*(.+)',
        r'(?:ต้อง|ทำ|งาน)[:\s]*(.+)',
        r'(?:надо|нужно|задача)[:\s]*(.+)',
    ]
    
    reminder_patterns = [
        r'(?:remind|reminder|don\'t forget)\s*(.+)',
        r'(?:เตือน|อย่าลืม)[:\s]*(.+)',
        r'(?:напомни|напоминание)[:\s]*(.+)',
    ]
    
    appointment_patterns = [
        r'(?:meeting|appointment|call)\s+(?:at|on|with)\s*(.+)',
        r'(?:นัด|ประชุม)[:\s]*(.+)',
        r'(?:встреча|созвон)[:\s]*(.+)',
    ]
    
    for msg in messages[-10:]:
        content = msg.get("content", "").lower()
        
        # Check for tasks
        for pattern in task_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                items.append(ExtractedItem(
                    type="task",
                    title=match.group(1)[:100].strip(),
                    detail="",
                    who="User",
                    when="",
                    confidence=0.7,
                    language=_detect_language(content),
                ))
                break
        
        # Check for reminders
        for pattern in reminder_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                items.append(ExtractedItem(
                    type="reminder",
                    title=match.group(1)[:100].strip(),
                    detail="",
                    who="User",
                    when="",
                    confidence=0.7,
                    language=_detect_language(content),
                ))
                break
        
        # Check for appointments
        for pattern in appointment_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                items.append(ExtractedItem(
                    type="appointment",
                    title=match.group(1)[:100].strip(),
                    detail="",
                    who="User",
                    when="",
                    confidence=0.7,
                    language=_detect_language(content),
                ))
                break
    
    return items


def _detect_language(text: str) -> str:
    """Detect language from text."""
    if any('\u0E00' <= c <= '\u0E7F' for c in text):
        return "th"
    if any('\u0400' <= c <= '\u04FF' for c in text):
        return "ru"
    return "en"


# ── Formatting ────────────────────────────────────────────────────────────────

def format_extracted_items(items: List[ExtractedItem]) -> str:
    """Format extracted items for preview."""
    if not items:
        return "✅ Nothing new to extract."
    
    lines = []
    for i, item in enumerate(items, 1):
        type_info = ITEM_TYPES.get(item.type, ITEM_TYPES["task"])
        flag = LANG_FLAGS.get(item.language, "")
        
        line = f"{i}. {type_info['icon']} *{type_info['label']}*: {item.title}"
        if item.when:
            line += f" — {item.when}"
        if flag:
            line += f" {flag}"
        
        lines.append(line)
    
    return "\n".join(lines)


def format_scan_result(items: List[ExtractedItem], language: str = "en") -> str:
    """Format scan result message."""
    if not items:
        messages = {
            "th": "✅ ไม่พบรายการใหม่",
            "ru": "✅ Ничего нового.",
            "en": "✅ Nothing new to extract.",
        }
        return messages.get(language, messages["en"])
    
    headers = {
        "th": f"🧠 *พบ {len(items)} รายการ:*",
        "ru": f"🧠 *Найдено {len(items)}:*",
        "en": f"🧠 *Found {len(items)} item(s):*",
    }
    
    header = headers.get(language, headers["en"])
    preview = format_extracted_items(items)
    
    return f"{header}\n\n{preview}\n\n/save · /skip"


def format_saved_items(items: List[ExtractedItem]) -> str:
    """Format saved items confirmation."""
    if not items:
        return "Nothing saved."
    
    lines = [f"✅ *Saved {len(items)} item(s)*"]
    for item in items:
        type_info = ITEM_TYPES.get(item.type, ITEM_TYPES["task"])
        lines.append(f"{type_info['icon']} {item.title}")
    
    return "\n".join(lines)
