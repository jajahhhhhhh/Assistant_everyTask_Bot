"""
NLP Processor — intent detection, summarisation, and task extraction.

When an OpenAI API key is configured the module delegates to GPT.
When no key is available (tests, offline mode) a lightweight keyword-based
fallback is used so that the rest of the system can still operate.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger(__name__)

# ── Lazy OpenAI import ────────────────────────────────────────────────────────

_openai_client = None


def _get_openai():
    global _openai_client
    if _openai_client is None and config.OPENAI_API_KEY:
        try:
            from openai import OpenAI
            _openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
        except Exception as exc:  # pragma: no cover
            logger.warning("Could not initialise OpenAI client: %s", exc)
    return _openai_client


# ── Helpers ───────────────────────────────────────────────────────────────────

def _chat(system_prompt: str, user_prompt: str) -> Optional[str]:
    """Send a single-turn chat request to OpenAI and return the text reply."""
    client = _get_openai()
    if client is None:
        return None
    try:
        response = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:  # pragma: no cover
        logger.error("OpenAI request failed: %s", exc)
        return None


# ── Intent detection ──────────────────────────────────────────────────────────

INTENT_KEYWORDS: Dict[str, List[str]] = {
    "task": ["task", "todo", "to-do", "do", "complete", "finish", "need to", "must", "should"],
    "reminder": ["remind", "reminder", "alert", "notify", "don't forget", "remember"],
    "calendar": ["meeting", "event", "appointment", "schedule a", "calendar", "sync"],
    "summary": ["summary", "summarise", "summarize", "recap", "overview", "brief"],
    "status": ["status", "progress", "update", "how many", "done", "pending"],
    "help": ["help", "commands", "what can you", "how do"],
}


def detect_intent(text: str) -> str:
    """
    Return the primary intent of *text* as a string label.

    Uses OpenAI when available, otherwise falls back to keyword matching.
    Priority order: reminder > calendar > task > summary > status > help > general.
    """
    client = _get_openai()
    if client:
        system = (
            "You are an intent classifier for a personal assistant bot. "
            "Classify the user message into exactly one of: "
            "task, reminder, calendar, summary, status, help, general. "
            "Reply with ONLY the label."
        )
        result = _chat(system, text)
        if result and result.lower() in INTENT_KEYWORDS or result == "general":
            return result.lower()

    # Keyword fallback
    lower = text.lower()
    for intent in ("reminder", "calendar", "summary", "status", "task", "help"):
        if any(kw in lower for kw in INTENT_KEYWORDS[intent]):
            return intent
    return "general"


# ── Summarisation ─────────────────────────────────────────────────────────────

def summarise(messages: List[Dict[str, Any]], mode: str = "short") -> str:
    """
    Return a summary of a list of message dicts (each has keys 'role' and 'text').

    *mode* can be 'short', 'detailed', or 'executive'.
    Falls back to a simple sentence-count truncation when OpenAI is unavailable.
    """
    if not messages:
        return "No messages to summarise."

    conversation = "\n".join(
        f"{m.get('role', 'user').capitalize()}: {m['text']}" for m in messages
    )

    mode_instructions = {
        "short": "Write a single concise paragraph (max 3 sentences).",
        "detailed": "Write a detailed summary covering all key points and decisions.",
        "executive": (
            "Write an executive summary: key decisions, action items, and next steps "
            "in bullet-point format."
        ),
    }
    instruction = mode_instructions.get(mode, mode_instructions["short"])

    system = (
        f"You are a summarisation assistant. {instruction} "
        "Focus on actionable items and important facts."
    )
    result = _chat(system, conversation)
    if result:
        return result

    # Fallback: return the last few messages as a plain text summary
    lines = [f"- {m['text']}" for m in messages[-5:]]
    return "Recent conversation highlights:\n" + "\n".join(lines)


# ── Task extraction ───────────────────────────────────────────────────────────

PRIORITY_WORDS = {
    1: ["urgent", "asap", "immediately", "critical", "high priority", "important"],
    3: ["low priority", "whenever", "someday", "maybe", "optional"],
}
CATEGORY_WORDS = {
    "business": ["meeting", "report", "client", "project", "deadline", "budget", "invoice", "work"],
    "personal": ["doctor", "gym", "family", "friend", "birthday", "shopping", "health"],
    "urgent": ["urgent", "asap", "emergency", "critical"],
}


def _infer_priority(text: str) -> int:
    lower = text.lower()
    for priority, keywords in PRIORITY_WORDS.items():
        if any(kw in lower for kw in keywords):
            return priority
    return 2  # default: medium


def _infer_category(text: str) -> str:
    lower = text.lower()
    for category in ("urgent", "business", "personal"):
        if any(kw in lower for kw in CATEGORY_WORDS[category]):
            return category
    return "general"


def extract_tasks(text: str) -> List[Dict[str, Any]]:
    """
    Extract a list of actionable tasks from *text*.

    Each task is a dict with keys: title, description, category, priority.
    Uses OpenAI when available; falls back to keyword-based extraction.
    """
    client = _get_openai()
    if client:
        system = """You are a task extraction assistant.
Extract all actionable tasks from the user message.
Return a JSON array where each element has:
  "title"       : short task title (max 10 words)
  "description" : more detail or empty string
  "category"    : one of business | personal | urgent | general
  "priority"    : integer 1 (high), 2 (medium), or 3 (low)
Reply with ONLY the JSON array (no markdown fences)."""
        raw = _chat(system, text)
        if raw:
            try:
                tasks = json.loads(raw)
                if isinstance(tasks, list):
                    return tasks
            except json.JSONDecodeError:
                logger.debug("Could not parse task JSON from OpenAI response.")

    # Keyword fallback — very simple heuristic
    tasks: List[Dict[str, Any]] = []
    sentences = re.split(r"[.!?;]|\band\b", text)
    action_verbs = r"\b(call|email|send|write|fix|review|schedule|prepare|buy|check|update|complete|finish|create|organise|organize|follow up|submit|book)\b"
    for sentence in sentences:
        sentence = sentence.strip()
        if re.search(action_verbs, sentence, re.IGNORECASE) and len(sentence) > 5:
            tasks.append(
                {
                    "title": sentence[:80],
                    "description": "",
                    "category": _infer_category(sentence),
                    "priority": _infer_priority(sentence),
                }
            )
    return tasks


# ── Date / time extraction (simple) ──────────────────────────────────────────

def extract_datetime(text: str) -> Optional[str]:
    """
    Try to extract a date/time expression from *text* using dateparser.
    Returns an ISO-8601 string or None.
    """
    try:
        import dateparser
        result = dateparser.parse(text, settings={"PREFER_DATES_FROM": "future"})
        if result:
            return result.isoformat()
    except Exception as exc:
        logger.debug("dateparser failed: %s", exc)
    return None
