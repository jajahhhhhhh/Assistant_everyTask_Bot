"""
Digest module — daily summaries and scheduled digests.

Generates comprehensive daily digests summarizing:
- Recent conversations
- Pending tasks
- Upcoming appointments
- Active projects
- Key recommendations
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger(__name__)

# ── Language templates ────────────────────────────────────────────────────────

DIGEST_TEMPLATES = {
    "th": {
        "greeting": "🌅 *สวัสดีตอนเช้า — {date}*",
        "time": "⏰ {time}",
        "pending_tasks": "📋 *งานที่ค้างอยู่*",
        "today": "📅 *วันนี้*",
        "ideas": "💡 *ไอเดียที่ต้องติดตาม*",
        "projects": "🚀 *โปรเจคที่กำลังดำเนินการ*",
        "top_actions": "⚡ *สิ่งที่ต้องทำวันนี้ (Top 3)*",
        "recommendation": "🧠 *Assistant แนะนำ*",
        "no_tasks": "ไม่มีงานค้าง",
        "no_appointments": "ไม่มีนัด",
        "no_ideas": "ไม่มี",
        "no_projects": "ไม่มีอัปเดต",
    },
    "ru": {
        "greeting": "🌅 *Доброе утро — {date}*",
        "time": "⏰ {time}",
        "pending_tasks": "📋 *Незавершённые задачи*",
        "today": "📅 *Сегодня*",
        "ideas": "💡 *Идеи для отслеживания*",
        "projects": "🚀 *Активные проекты*",
        "top_actions": "⚡ *Топ-3 на сегодня*",
        "recommendation": "🧠 *Рекомендация*",
        "no_tasks": "Нет задач",
        "no_appointments": "Нет встреч",
        "no_ideas": "Нет",
        "no_projects": "Нет обновлений",
    },
    "en": {
        "greeting": "🌅 *Good Morning — {date}*",
        "time": "⏰ {time}",
        "pending_tasks": "📋 *Pending Tasks*",
        "today": "📅 *Today*",
        "ideas": "💡 *Ideas to Follow Up*",
        "projects": "🚀 *Active Projects*",
        "top_actions": "⚡ *Top 3 Actions Today*",
        "recommendation": "🧠 *Assistant Recommends*",
        "no_tasks": "No pending tasks",
        "no_appointments": "No appointments",
        "no_ideas": "None",
        "no_projects": "No updates",
    },
}


# ── AI integration ────────────────────────────────────────────────────────────

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


DIGEST_SYSTEM_PROMPT = """You are a helpful personal assistant creating a morning digest.
Be concise, actionable, and encouraging. Focus on what matters most.
Format with emojis and clear structure. Reply in the requested language."""


def generate_ai_digest(
    messages_context: str,
    tasks_context: str,
    events_context: str,
    projects_context: str,
    language: str = "en",
    date_str: str = "",
    time_str: str = "",
) -> Optional[str]:
    """Generate AI-powered daily digest."""
    client = _get_openai()
    if client is None:
        return None
    
    lang_templates = DIGEST_TEMPLATES.get(language, DIGEST_TEMPLATES["en"])
    
    lang_instruction = {
        "th": "ตอบเป็นภาษาไทย",
        "ru": "Отвечай на русском",
        "en": "Reply in English",
    }.get(language, "Reply in English")
    
    prompt = f"""Create a morning daily digest. Be concise and actionable.

DATE: {date_str} {time_str}

RECENT CONVERSATIONS (last 24h):
{messages_context or "No conversations"}

PENDING TASKS:
{tasks_context or "No tasks"}

TODAY'S EVENTS:
{events_context or "No events"}

ACTIVE PROJECTS:
{projects_context or "No projects"}

Format the digest with these sections:
{lang_templates['greeting'].format(date=date_str)}
{lang_templates['time'].format(time=time_str)}

{lang_templates['pending_tasks']}
• [list or "{lang_templates['no_tasks']}"]

{lang_templates['today']}
• [appointments/deadlines or "{lang_templates['no_appointments']}"]

{lang_templates['projects']}
• [project updates or "{lang_templates['no_projects']}"]

{lang_templates['top_actions']}
1.
2.
3.

{lang_templates['recommendation']}
[One sharp actionable insight]

{lang_instruction}. Keep it brief and motivating."""

    try:
        response = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": DIGEST_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.5,
            max_tokens=800,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        logger.error("Digest generation failed: %s", exc)
        return None


# ── Digest generation ─────────────────────────────────────────────────────────

def generate_digest(
    conn,
    user_id: int,
    language: str = "en",
    timezone_name: str = "Asia/Bangkok",
) -> str:
    """
    Generate a comprehensive daily digest.
    
    Args:
        conn: Database connection
        user_id: User ID
        language: Preferred language (th, ru, en)
        timezone_name: User's timezone
        
    Returns:
        Formatted digest string
    """
    from assistant import storage, tasks as task_module, calendar_integration
    
    try:
        import pytz
        tz = pytz.timezone(timezone_name)
        now = datetime.now(tz)
    except Exception:
        now = datetime.now(timezone.utc)
    
    date_str = now.strftime("%A, %B %d, %Y")
    time_str = now.strftime("%H:%M")
    
    # Get recent messages (last 24h)
    messages = storage.get_messages(conn, user_id, limit=50)
    since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    recent_messages = [
        m for m in messages
        if datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00")) > since_24h
    ]
    messages_context = "\n".join([
        f"- {m['content'][:100]}" for m in recent_messages[:20]
    ]) if recent_messages else ""
    
    # Get pending tasks
    pending_tasks = task_module.list_tasks(conn, user_id, status="pending")
    tasks_context = "\n".join([
        f"- [{t['priority']}] {t['description']}" + (f" (due: {t['due_date']})" if t.get('due_date') else "")
        for t in pending_tasks[:10]
    ]) if pending_tasks else ""
    
    # Get today's events
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    events = calendar_integration.get_events_in_range(
        conn, user_id,
        today_start.isoformat(),
        today_end.isoformat()
    )
    events_context = "\n".join([
        f"- {e['title']} at {e['start_time']}"
        for e in events
    ]) if events else ""
    
    # Get active projects
    try:
        from assistant import projects as proj_module
        proj_module.ensure_projects_table(conn)
        active_projects = [
            p for p in proj_module.get_projects(conn, user_id)
            if p.status in ("active", "planning")
        ]
        projects_context = "\n".join([
            f"- {p.title} ({p.status})" + (f": {p.summary[:50]}" if p.summary else "")
            for p in active_projects[:5]
        ]) if active_projects else ""
    except Exception:
        projects_context = ""
    
    # Try AI digest first
    ai_digest = generate_ai_digest(
        messages_context=messages_context,
        tasks_context=tasks_context,
        events_context=events_context,
        projects_context=projects_context,
        language=language,
        date_str=date_str,
        time_str=time_str,
    )
    
    if ai_digest:
        return ai_digest
    
    # Fallback: generate simple digest
    return _generate_simple_digest(
        language=language,
        date_str=date_str,
        time_str=time_str,
        pending_tasks=pending_tasks,
        events=events,
        active_projects=active_projects if 'active_projects' in dir() else [],
    )


def _generate_simple_digest(
    language: str,
    date_str: str,
    time_str: str,
    pending_tasks: List[Dict],
    events: List[Dict],
    active_projects: List,
) -> str:
    """Generate simple digest without AI."""
    t = DIGEST_TEMPLATES.get(language, DIGEST_TEMPLATES["en"])
    
    lines = [
        t["greeting"].format(date=date_str),
        t["time"].format(time=time_str),
        "",
        t["pending_tasks"],
    ]
    
    if pending_tasks:
        for task in pending_tasks[:5]:
            lines.append(f"• {task['description']}")
    else:
        lines.append(f"• {t['no_tasks']}")
    
    lines.extend(["", t["today"]])
    
    if events:
        for event in events[:3]:
            lines.append(f"• {event['title']} at {event['start_time']}")
    else:
        lines.append(f"• {t['no_appointments']}")
    
    if active_projects:
        lines.extend(["", t["projects"]])
        for proj in active_projects[:3]:
            lines.append(f"• {proj.title}")
    
    lines.extend([
        "",
        t["top_actions"],
        "1. Review pending tasks",
        "2. Check calendar",
        "3. Follow up on priorities",
        "",
        t["recommendation"],
        "Focus on high-priority items first.",
    ])
    
    return "\n".join(lines)


# ── Brief summary ─────────────────────────────────────────────────────────────

def generate_brief(
    conn,
    user_id: int,
    language: str = "en",
) -> str:
    """
    Generate a brief summary of key points.
    
    Returns 3-5 bullet points of the most important things.
    """
    from assistant import storage
    
    # Get recent messages
    messages = storage.get_messages(conn, user_id, limit=20)
    
    if len(messages) < 3:
        return "📝 Not enough conversation to summarize yet."
    
    messages_text = "\n".join([
        f"- {m['content'][:100]}" for m in messages[:15]
    ])
    
    client = _get_openai()
    if client is None:
        # Fallback: simple extraction
        return _simple_brief(messages)
    
    lang_instruction = {
        "th": "ตอบเป็นภาษาไทย กระชับ ตรงประเด็น",
        "ru": "Отвечай по-русски кратко",
        "en": "Reply in English, be concise",
    }.get(language, "Reply in English, be concise")
    
    try:
        response = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You are a helpful assistant. Extract key points from conversations."},
                {"role": "user", "content": f"""Give 3-5 bullet points of the most important things from this conversation.
Be very short. {lang_instruction}

Recent messages:
{messages_text}"""},
            ],
            temperature=0.3,
            max_tokens=300,
        )
        
        brief = response.choices[0].message.content.strip()
        return f"📝 *Key Points*\n\n{brief}"
        
    except Exception as exc:
        logger.error("Brief generation failed: %s", exc)
        return _simple_brief(messages)


def _simple_brief(messages: List[Dict]) -> str:
    """Generate simple brief without AI."""
    # Extract first few message snippets
    points = []
    for m in messages[:5]:
        content = m.get("content", "")[:50]
        if content:
            points.append(f"• {content}")
    
    return "📝 *Recent Activity*\n\n" + "\n".join(points) if points else "📝 No recent activity."
