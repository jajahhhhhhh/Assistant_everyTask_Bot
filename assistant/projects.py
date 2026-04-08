"""
Projects module — project management and AI-powered analysis.

Manages projects, ideas, and provides deep business analysis
with market scores, revenue estimates, and recommendations.
"""

import json
import logging
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger(__name__)

# ── Project status and icons ──────────────────────────────────────────────────

PROJECT_STATUS = {
    "idea": {"icon": "💡", "label": "Idea"},
    "planning": {"icon": "📋", "label": "Planning"},
    "active": {"icon": "⚡", "label": "Active"},
    "paused": {"icon": "⏸", "label": "Paused"},
    "done": {"icon": "✅", "label": "Done"},
    "cancelled": {"icon": "❌", "label": "Cancelled"},
}

DEFAULT_STATUS = "idea"


# ── Data class ────────────────────────────────────────────────────────────────

@dataclass
class Project:
    id: int
    user_id: int
    title: str
    status: str
    summary: str
    notes: str
    analysis: str
    score: Optional[float]
    created_at: str
    updated_at: str
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Project":
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            title=row["title"],
            status=row["status"],
            summary=row["summary"] or "",
            notes=row["notes"] or "",
            analysis=row["analysis"] or "",
            score=row["score"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# ── Database operations ───────────────────────────────────────────────────────

def ensure_projects_table(conn: sqlite3.Connection) -> None:
    """Create projects table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            status TEXT DEFAULT 'idea',
            summary TEXT,
            notes TEXT,
            analysis TEXT,
            score REAL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()


def get_projects(conn: sqlite3.Connection, user_id: int) -> List[Project]:
    """Get all projects for a user."""
    ensure_projects_table(conn)
    cursor = conn.execute(
        "SELECT * FROM projects WHERE user_id = ? ORDER BY updated_at DESC",
        (user_id,)
    )
    return [Project.from_row(row) for row in cursor.fetchall()]


def get_project_by_id(conn: sqlite3.Connection, project_id: int) -> Optional[Project]:
    """Get a project by ID."""
    ensure_projects_table(conn)
    cursor = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
    row = cursor.fetchone()
    return Project.from_row(row) if row else None


def find_project_by_title(
    conn: sqlite3.Connection,
    user_id: int,
    title: str,
) -> Optional[Project]:
    """Find a project by title (partial match)."""
    ensure_projects_table(conn)
    cursor = conn.execute(
        "SELECT * FROM projects WHERE user_id = ? AND LOWER(title) LIKE ?",
        (user_id, f"%{title.lower()}%")
    )
    row = cursor.fetchone()
    return Project.from_row(row) if row else None


def create_project(
    conn: sqlite3.Connection,
    user_id: int,
    title: str,
    status: str = DEFAULT_STATUS,
    summary: str = "",
    notes: str = "",
    analysis: str = "",
    score: Optional[float] = None,
) -> Project:
    """Create a new project."""
    ensure_projects_table(conn)
    now = datetime.now(timezone.utc).isoformat()
    
    cursor = conn.execute(
        """
        INSERT INTO projects (user_id, title, status, summary, notes, analysis, score, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, title, status, summary, notes, analysis, score, now, now)
    )
    conn.commit()
    
    return get_project_by_id(conn, cursor.lastrowid)


def update_project(
    conn: sqlite3.Connection,
    project_id: int,
    **kwargs,
) -> Optional[Project]:
    """Update a project."""
    ensure_projects_table(conn)
    
    # Filter valid fields
    valid_fields = {"title", "status", "summary", "notes", "analysis", "score"}
    updates = {k: v for k, v in kwargs.items() if k in valid_fields}
    
    if not updates:
        return get_project_by_id(conn, project_id)
    
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
    values = list(updates.values()) + [project_id]
    
    conn.execute(f"UPDATE projects SET {set_clause} WHERE id = ?", values)
    conn.commit()
    
    return get_project_by_id(conn, project_id)


def delete_project(conn: sqlite3.Connection, project_id: int) -> bool:
    """Delete a project."""
    ensure_projects_table(conn)
    cursor = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()
    return cursor.rowcount > 0


# ── AI Analysis ───────────────────────────────────────────────────────────────

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


PROJECT_ANALYSIS_SYSTEM = """You are a sharp business analyst. Analyze projects deeply.
Consider: market context, current resources, partnerships, competition.
Be direct. Give real numbers. Flag real risks. Find hidden opportunities.
Reply in the same language as the input (Thai/Russian/English)."""

LANG_INSTRUCTIONS = {
    "th": "ตอบเป็นภาษาไทย กระชับ ตรงประเด็น",
    "ru": "Отвечай по-русски, кратко и по делу",
    "en": "Reply in English, concise and direct",
}


def detect_language(text: str) -> str:
    """Detect language from text."""
    if not text:
        return "en"
    if any('\u0E00' <= c <= '\u0E7F' for c in text):
        return "th"
    if any('\u0400' <= c <= '\u04FF' for c in text):
        return "ru"
    return "en"


def analyze_project(
    title: str,
    existing_notes: str = "",
    conversation_context: str = "",
    language: str = "en",
) -> Optional[str]:
    """
    Perform deep analysis of a project/idea.
    
    Returns analysis text or None if AI unavailable.
    """
    client = _get_openai()
    if client is None:
        return None
    
    lang_instr = LANG_INSTRUCTIONS.get(language, LANG_INSTRUCTIONS["en"])
    
    prompt = f"""Analyze this project/idea deeply:

PROJECT: "{title}"
{f'Previous notes: {existing_notes}' if existing_notes else ''}
{f'Recent context:\n{conversation_context}' if conversation_context else ''}

Provide:
1. 🎯 Core opportunity (1 sentence)
2. 📊 Scores (Market/10, Feasibility/10, Impact/10)
3. 💰 Revenue estimate (monthly)
4. ⏱ Time to first revenue
5. 🔑 3 key action steps
6. ⚠️ 2 main risks
7. 💎 One hidden angle most miss
8. 🏆 Verdict: GO / WAIT / SKIP + reason

{lang_instr}

Be specific and practical."""

    try:
        response = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": PROJECT_ANALYSIS_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
            max_tokens=1000,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        logger.error("Project analysis failed: %s", exc)
        return None


def extract_score_from_analysis(analysis: str) -> Optional[float]:
    """Extract average score from analysis text."""
    import re
    
    # Look for patterns like "8/10", "Market: 7", etc.
    scores = re.findall(r'(\d+(?:\.\d+)?)\s*/\s*10', analysis)
    
    if scores:
        numeric_scores = [float(s) for s in scores[:3]]  # Take up to 3 scores
        return round(sum(numeric_scores) / len(numeric_scores), 1)
    
    return None


# ── Formatting ────────────────────────────────────────────────────────────────

def format_projects_list(projects: List[Project]) -> str:
    """Format list of projects for display."""
    if not projects:
        return "📁 No projects yet.\n\nUse `/analyze [project name]` to add and analyze one."
    
    lines = [f"🚀 *Projects ({len(projects)})*\n"]
    
    for i, p in enumerate(projects, 1):
        status = PROJECT_STATUS.get(p.status, PROJECT_STATUS["idea"])
        icon = status["icon"]
        score_str = f" · Score: {p.score}/10" if p.score else ""
        
        lines.append(f"{i}. {icon} *{p.title}*{score_str}")
        if p.summary:
            lines.append(f"   _{p.summary[:80]}{'...' if len(p.summary) > 80 else ''}_")
        lines.append("")
    
    lines.append("Use `/analyze [title]` to deep-dive any project.")
    
    return "\n".join(lines)


def format_project_analysis(project: Project, analysis: str) -> str:
    """Format project analysis for display."""
    status = PROJECT_STATUS.get(project.status, PROJECT_STATUS["idea"])
    
    lines = [
        f"🚀 *{project.title}*",
        f"Status: {status['icon']} {status['label']}",
        "",
        analysis,
    ]
    
    return "\n".join(lines)
