"""
Secretary module — turns a raw note into a filed, summarised, actionable record.

The pipeline is deliberately linear so it can be reasoned about and tested:

    raw text (voice transcript / typed message / receipt OCR)
        → extract()          structured {summary, events, expenses, commitments, notes}
        → file_note()        persist log entry + calendar events + ledger rows + tasks
        → format_intake()    a single Telegram reply confirming what was filed

Extraction uses GPT when an API key is configured and falls back to regex /
keyword heuristics otherwise, so the module never hard-fails offline or in tests.
Notes arrive in Thai, English and Russian; the prompts and the fallback patterns
all handle the three.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import config
from assistant import finance

logger = logging.getLogger(__name__)

# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS secretary_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    logged_on   TEXT    NOT NULL,             -- ISO date
    speaker     TEXT    NOT NULL DEFAULT 'Farid',
    source      TEXT    NOT NULL DEFAULT 'voice',
    transcript  TEXT    NOT NULL DEFAULT '',
    summary     TEXT    NOT NULL DEFAULT '',
    payload     TEXT    NOT NULL DEFAULT '{}', -- JSON of the full extraction
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_secretary_log_day
    ON secretary_log (logged_on);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the secretary tables if absent.  Safe to call repeatedly."""
    conn.executescript(_SCHEMA)
    conn.commit()
    finance.ensure_schema(conn)


# ── OpenAI plumbing ───────────────────────────────────────────────────────────

_openai_client = None


def _get_openai():
    global _openai_client
    if _openai_client is None and getattr(config, "OPENAI_API_KEY", ""):
        try:
            from openai import OpenAI
            _openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
        except Exception as exc:  # pragma: no cover - depends on env
            logger.warning("Could not initialise OpenAI client: %s", exc)
    return _openai_client


def is_ai_available() -> bool:
    """True when GPT-backed extraction can be used."""
    return bool(getattr(config, "OPENAI_API_KEY", ""))


_EXTRACTION_SYSTEM = """You are a meticulous personal secretary for a two-person \
household in Koh Samui, Thailand: "J" and "Farid". You receive a transcript of a \
voice note or a message. Notes may be in Thai, English or Russian.

Extract everything actionable and return STRICT JSON with exactly these keys:

{
  "summary": "2-4 sentence neutral summary in English",
  "language": "th|en|ru",
  "events": [
    {"title": "...", "start": "YYYY-MM-DDTHH:MM", "end": "YYYY-MM-DDTHH:MM or null",
     "location": "... or null", "notes": "... or null", "all_day": false}
  ],
  "expenses": [
    {"amount": 1234.0, "currency": "THB", "category": "rent|food|household|utilities|transport|shared|other",
     "description": "...", "payer": "J|Farid", "split": "equal|mine|theirs",
     "paid_from": "fund|personal", "spent_on": "YYYY-MM-DD"}
  ],
  "commitments": [
    {"who": "J|Farid|both", "what": "...", "due": "YYYY-MM-DD or null", "priority": "high|medium|low"}
  ],
  "notes": ["standalone facts worth remembering that are not events, money or tasks"],
  "questions": ["anything the speaker explicitly asked and expects an answer to"]
}

Rules:
- Resolve relative dates ("tomorrow", "next Friday", "พรุ่งนี้", "завтра") against TODAY, given below.
- Thai baht is the default currency. "k" or "พัน" means thousands.
- If the speaker says they paid for something, payer is the speaker.
- "split" is "equal" unless the note says one person is covering it alone.
- The household runs a shared FOOD FUND that both members top up each month.
  Set "paid_from": "fund" for groceries, restaurants, coffee and food delivery —
  anything in the "food" category — unless the speaker says they paid for it
  personally or that it was their own meal. Everything else is "personal".
  Fund spending creates no debt between them, so "split" is ignored for it.
- Use empty arrays when a category has nothing. Never invent data.
- Output JSON only, no prose, no markdown fences."""


def _empty_extraction() -> Dict[str, Any]:
    return {
        "summary": "",
        "language": "en",
        "events": [],
        "expenses": [],
        "commitments": [],
        "notes": [],
        "questions": [],
    }


def _strip_fences(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


# ── Offline fallback extraction ───────────────────────────────────────────────

_AMOUNT_RE = re.compile(
    r"(?P<pre>[฿$€₽]|thb|baht|บาท|руб)?\s*"
    r"(?P<num>\d{1,3}(?:[,\s]\d{3})+|\d+(?:\.\d{1,2})?)\s*"
    r"(?P<suffix>k|พัน|тыс)?\s*"
    r"(?P<post>[฿$€₽]|thb|baht|บาท|руб)?",
    re.IGNORECASE,
)

_SYMBOL_CURRENCY = {
    "฿": "THB", "thb": "THB", "baht": "THB", "บาท": "THB",
    "$": "USD", "€": "EUR", "₽": "RUB", "руб": "RUB",
}

_SPEND_VERBS = (
    "paid", "spent", "bought", "cost", "charge", "bill", "จ่าย", "ซื้อ",
    "ค่า", "заплатил", "купил", "потратил",
)


def _parse_amount(match: re.Match) -> Optional[float]:
    raw = match.group("num").replace(",", "").replace(" ", "")
    try:
        value = float(raw)
    except ValueError:
        return None
    if match.group("suffix"):
        value *= 1000
    return value


#: Splits a note into sentences while keeping the terminator, so a question mark
#: survives for question detection.  Thai polite particles end clauses too.
_SENTENCE_RE = re.compile(r"[^.!?\n\u0e46]+(?:[.!?\n]|\u0e04\u0e23\u0e31\u0e1a|\u0e04\u0e48\u0e30|$)")

_COMMITMENT_MARKERS = (
    "need to", "must", "should", "don't forget", "dont forget", "remember to",
    "\u0e15\u0e49\u0e2d\u0e07", "\u0e2d\u0e22\u0e48\u0e32\u0e25\u0e37\u0e21", "\u043d\u0443\u0436\u043d\u043e", "\u043d\u0430\u0434\u043e", "\u043d\u0435 \u0437\u0430\u0431\u0443\u0434\u044c",
)


def _sentences(text: str) -> List[str]:
    """Split *text* into trimmed sentences, terminators retained."""
    return [m.group(0).strip() for m in _SENTENCE_RE.finditer(text) if m.group(0).strip()]


def _fallback_extract(text: str, today: date) -> Dict[str, Any]:
    """
    Heuristic extraction used when no OpenAI key is configured.

    Works sentence by sentence: an amount is only read as an expense when its
    own sentence mentions spending, and it is described and categorised from
    that sentence alone.  Classifying against the whole note would let an
    unrelated clause ("send the landlord the contract") hijack the category.
    """
    result = _empty_extraction()
    if not text:
        return result

    result["summary"] = text.strip()[:400]

    # Language guess by script.
    if any("\u0e00" <= c <= "\u0e7f" for c in text):
        result["language"] = "th"
    elif any("\u0400" <= c <= "\u04ff" for c in text):
        result["language"] = "ru"

    sentences = _sentences(text) or [text.strip()]

    for sentence in sentences:
        low = sentence.lower()
        clean = sentence.rstrip(".!?\n").strip()

        # Questions.
        if sentence.rstrip().endswith("?"):
            result["questions"].append(sentence.strip()[:200])
            continue

        # Commitments.
        if any(marker in low for marker in _COMMITMENT_MARKERS):
            result["commitments"].append({
                "who": "both", "what": clean[:200], "due": None, "priority": "medium",
            })
            continue

        # Expenses — only when this sentence itself is about spending.
        if not result["expenses"] and any(verb in low for verb in _SPEND_VERBS):
            for match in _AMOUNT_RE.finditer(sentence):
                amount = _parse_amount(match)
                if not amount or amount < 1:
                    continue
                symbol = (match.group("pre") or match.group("post") or "").lower()
                category = finance.guess_category(sentence)
                result["expenses"].append({
                    "amount": amount,
                    "currency": _SYMBOL_CURRENCY.get(symbol, finance.DEFAULT_CURRENCY),
                    "category": category,
                    "description": clean[:120],
                    "payer": "Farid",
                    "split": "equal",
                    # Food comes out of the shared fund by default.
                    "paid_from": "fund" if category == "food" else "personal",
                    "spent_on": today.isoformat(),
                })
                break

    if not result["expenses"] and not result["commitments"]:
        result["notes"].append(text.strip()[:300])

    return result


# ── Extraction ────────────────────────────────────────────────────────────────

def extract(text: str, today: Optional[date] = None, speaker: str = "Farid") -> Dict[str, Any]:
    """
    Turn raw note text into the structured secretary payload.

    Falls back to heuristics when GPT is unavailable or returns unusable JSON.
    The returned dict always has every key present with the right container type.
    """
    today = today or date.today()
    if not text or not text.strip():
        return _empty_extraction()

    client = _get_openai()
    if client is None:
        return _fallback_extract(text, today)

    user_prompt = (
        f"TODAY is {today.isoformat()} ({today.strftime('%A')}). "
        f"The speaker is {speaker}. Timezone Asia/Bangkok.\n\n"
        f"TRANSCRIPT:\n{text.strip()}"
    )

    try:
        response = client.chat.completions.create(
            model=getattr(config, "OPENAI_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": _EXTRACTION_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
        )
        raw = response.choices[0].message.content
        payload = json.loads(_strip_fences(raw or ""))
    except json.JSONDecodeError as exc:
        logger.warning("Secretary extraction returned invalid JSON: %s", exc)
        return _fallback_extract(text, today)
    except Exception as exc:  # pragma: no cover - network dependent
        logger.error("Secretary extraction failed: %s", exc)
        return _fallback_extract(text, today)

    merged = _empty_extraction()
    if isinstance(payload, dict):
        for key, default in merged.items():
            value = payload.get(key, default)
            if isinstance(default, list) and not isinstance(value, list):
                value = [value] if value else []
            if isinstance(default, str) and not isinstance(value, str):
                value = str(value) if value is not None else ""
            merged[key] = value
    if not merged["summary"]:
        merged["summary"] = text.strip()[:400]
    return merged


# ── Filing ────────────────────────────────────────────────────────────────────

def _parse_dt(value: Any) -> Optional[datetime]:
    """Best-effort parse of an ISO-ish datetime string."""
    if value in (None, "", "null"):
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("Z", "+00:00")
    for parser in (
        lambda t: datetime.fromisoformat(t),
        lambda t: datetime.strptime(t, "%Y-%m-%d %H:%M"),
        lambda t: datetime.strptime(t, "%Y-%m-%d"),
    ):
        try:
            return parser(text)
        except (ValueError, TypeError):
            continue
    return None


def file_note(
    conn: sqlite3.Connection,
    user_id: int,
    text: str,
    speaker: str = "Farid",
    source: str = "voice",
    source_ref: Optional[str] = None,
    extraction: Optional[Dict[str, Any]] = None,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Run the full secretary pipeline for one note and persist everything.

    Returns a receipt dict describing what was filed::

        {"log_id", "summary", "events", "expenses", "commitments",
         "notes", "questions", "extraction"}

    ``events`` and ``expenses`` in the receipt are the persisted rows, so they
    carry database ids and can be pushed to Google afterwards.
    """
    ensure_schema(conn)
    today = today or date.today()
    data = extraction or extract(text, today=today, speaker=speaker)

    # ── Calendar events ──
    from assistant import calendar_integration  # imported late to avoid a cycle

    filed_events: List[Dict[str, Any]] = []
    for ev in data.get("events", []):
        if not isinstance(ev, dict):
            continue
        title = (ev.get("title") or "").strip()
        start = _parse_dt(ev.get("start"))
        if not title or not start:
            continue
        end = _parse_dt(ev.get("end")) or start + timedelta(hours=1)
        try:
            filed = calendar_integration.add_event(
                conn, user_id,
                title=title,
                start_time=start,
                end_time=end,
                description=(ev.get("notes") or "") + (f"\n— from {speaker}'s {source} note" if speaker else ""),
                location=ev.get("location") or "",
            )
            filed["all_day"] = bool(ev.get("all_day"))
            filed_events.append(filed)
        except Exception as exc:
            logger.error("Could not file event %r: %s", title, exc)

    # ── Expenses ──
    filed_expenses: List[Dict[str, Any]] = []
    for exp in data.get("expenses", []):
        if not isinstance(exp, dict):
            continue
        amount = exp.get("amount")
        if amount in (None, "", 0):
            continue
        try:
            filed_expenses.append(finance.add_expense(
                conn, user_id,
                amount=float(amount),
                description=exp.get("description") or "",
                category=exp.get("category"),
                payer=exp.get("payer") or speaker,
                payer_share=exp.get("split", "equal"),
                paid_from=exp.get("paid_from") or (
                    "fund" if (exp.get("category") or "").lower() == "food" else "personal"
                ),
                currency=exp.get("currency") or finance.DEFAULT_CURRENCY,
                spent_on=exp.get("spent_on") or today,
                source=source,
                source_ref=source_ref,
            ))
        except (TypeError, ValueError) as exc:
            logger.error("Could not file expense %r: %s", exp, exc)

    # ── Commitments become tasks ──
    from assistant import tasks as task_module

    filed_tasks: List[Dict[str, Any]] = []
    priority_map = {"high": 1, "medium": 2, "low": 3}
    for com in data.get("commitments", []):
        if not isinstance(com, dict):
            continue
        what = (com.get("what") or "").strip()
        if not what:
            continue
        who = (com.get("who") or "both").strip()
        try:
            task_id = None
            if hasattr(task_module, "add_task"):
                created = task_module.add_task(
                    conn, user_id,
                    title=what[:200],
                    description=f"From {speaker}'s {source} note (log #pending) — owner: {who}",
                    category="household",
                    priority=priority_map.get(com.get("priority"), 2),
                    deadline=com.get("due") or None,
                )
                task_id = created.get("id") if isinstance(created, dict) else created
            filed_tasks.append({"id": task_id, "who": who, "what": what,
                                "due": com.get("due"),
                                "priority": priority_map.get(com.get("priority"), 2)})
        except Exception as exc:
            logger.error("Could not file commitment %r: %s", what, exc)

    # ── Log entry ──
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO secretary_log
               (user_id, logged_on, speaker, source, transcript, summary, payload, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id, today.isoformat(), speaker, source,
            (text or "").strip(), data.get("summary", ""),
            json.dumps(data, ensure_ascii=False), now,
        ),
    )
    conn.commit()

    return {
        "log_id": cur.lastrowid,
        "summary": data.get("summary", ""),
        "language": data.get("language", "en"),
        "events": filed_events,
        "expenses": filed_expenses,
        "commitments": filed_tasks,
        "notes": [n for n in data.get("notes", []) if n],
        "questions": [q for q in data.get("questions", []) if q],
        "extraction": data,
    }


# ── Log reads ─────────────────────────────────────────────────────────────────

def get_log(
    conn: sqlite3.Connection,
    user_id: Optional[int] = None,
    since: Optional[date] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Return log entries newest-first, optionally from *since* onwards."""
    ensure_schema(conn)
    clauses: List[str] = []
    params: List[Any] = []
    if user_id is not None:
        clauses.append("user_id = ?")
        params.append(user_id)
    if since:
        clauses.append("logged_on >= ?")
        params.append(since.isoformat())

    sql = "SELECT * FROM secretary_log"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY logged_on DESC, id DESC LIMIT ?"
    params.append(limit)

    entries = []
    for row in conn.execute(sql, params).fetchall():
        try:
            payload = json.loads(row["payload"])
        except (json.JSONDecodeError, TypeError):
            payload = {}
        entries.append({
            "id": row["id"],
            "logged_on": row["logged_on"],
            "speaker": row["speaker"],
            "source": row["source"],
            "transcript": row["transcript"],
            "summary": row["summary"],
            "payload": payload,
        })
    return entries


# ── Formatting ────────────────────────────────────────────────────────────────

_SOURCE_EMOJI = {"voice": "🎤", "receipt": "🧾", "text": "💬", "manual": "✍️"}


def format_intake(receipt: Dict[str, Any], speaker: str = "Farid") -> str:
    """One Telegram message confirming exactly what the secretary filed."""
    lines = [f"🗂 *Filed {speaker}'s note*", ""]

    if receipt.get("summary"):
        lines.append(f"_{receipt['summary']}_")
        lines.append("")

    if receipt.get("events"):
        lines.append("📅 *Calendar*")
        for ev in receipt["events"]:
            when = str(ev.get("start_time", ""))[:16].replace("T", " ")
            where = f" · {ev['location']}" if ev.get("location") else ""
            lines.append(f"  • {ev['title']} — {when}{where}")
        lines.append("")

    if receipt.get("expenses"):
        lines.append("💸 *Ledger*")
        for exp in receipt["expenses"]:
            emoji = finance.CATEGORY_EMOJI.get(exp["category"], "•")
            pot = ("from the food fund" if exp.get("paid_from") == "fund"
                   else f"{exp['payer']} paid")
            lines.append(
                f"  {emoji} {finance.format_money(exp['amount'], exp['currency'])} "
                f"— {exp['description'] or exp['category']} ({pot})"
            )
        lines.append("")

    if receipt.get("commitments"):
        lines.append("✅ *To do*")
        for com in receipt["commitments"]:
            due = f" (by {com['due']})" if com.get("due") else ""
            lines.append(f"  • [{com['who']}] {com['what']}{due}")
        lines.append("")

    if receipt.get("questions"):
        lines.append("❓ *Asked you*")
        for q in receipt["questions"]:
            lines.append(f"  • {q}")
        lines.append("")

    if receipt.get("notes"):
        lines.append("📌 *Noted*")
        for n in receipt["notes"]:
            lines.append(f"  • {n}")
        lines.append("")

    nothing = not any(receipt.get(k) for k in
                      ("events", "expenses", "commitments", "questions", "notes"))
    if nothing:
        lines.append("_Nothing actionable — logged as context._")

    return "\n".join(lines).rstrip()


def format_log(entries: List[Dict[str, Any]], limit: int = 15) -> str:
    """Render the secretary log as a readable Telegram digest."""
    if not entries:
        return "🗂 *Log*\n\nNothing logged yet."

    lines = ["🗂 *Secretary log*", ""]
    current_day = None
    for entry in entries[:limit]:
        if entry["logged_on"] != current_day:
            current_day = entry["logged_on"]
            lines.append(f"*{current_day}*")
        emoji = _SOURCE_EMOJI.get(entry["source"], "•")
        summary = entry["summary"] or entry["transcript"][:120]
        lines.append(f"  {emoji} `#{entry['id']}` {entry['speaker']}: {summary}")
        lines.append("")

    if len(entries) > limit:
        lines.append(f"_…and {len(entries) - limit} older entries._")
    return "\n".join(lines).rstrip()


def weekly_digest(
    conn: sqlite3.Connection,
    user_id: Optional[int] = None,
    today: Optional[date] = None,
) -> str:
    """A week-in-review combining the log, the ledger and the outstanding balance."""
    today = today or date.today()
    since = today - timedelta(days=7)
    entries = get_log(conn, user_id=user_id, since=since, limit=100)
    rollup = finance.monthly_rollup(conn, month=finance.month_key(today), user_id=user_id)
    balances = finance.compute_balance(conn, user_id=user_id)

    lines = [f"📖 *Week in review* — {since.isoformat()} to {today.isoformat()}", ""]

    if entries:
        events = sum(len(e["payload"].get("events", [])) for e in entries)
        expenses = sum(len(e["payload"].get("expenses", [])) for e in entries)
        commitments = sum(len(e["payload"].get("commitments", [])) for e in entries)
        lines.append(
            f"{len(entries)} notes filed · {events} events · "
            f"{expenses} expenses · {commitments} commitments"
        )
        lines.append("")
        for entry in entries[:8]:
            lines.append(f"  • {entry['logged_on'][5:]} {entry['speaker']}: "
                         f"{(entry['summary'] or entry['transcript'])[:140]}")
        lines.append("")
    else:
        lines.append("_No notes filed this week._")
        lines.append("")

    lines.append(finance.format_rollup(rollup))
    lines.append("")
    lines.append(finance.format_balance(balances))
    return "\n".join(lines)
