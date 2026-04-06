"""
Calendar integration — manage events and export them as iCal (.ics) files.
"""

import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import sqlite3

import pytz
from icalendar import Calendar, Event, vText

import config
from assistant import storage

logger = logging.getLogger(__name__)


# ── Event management ──────────────────────────────────────────────────────────

def add_event(
    conn: sqlite3.Connection,
    user_id: int,
    title: str,
    start_time: datetime,
    end_time: Optional[datetime] = None,
    description: str = "",
    location: str = "",
) -> Dict[str, Any]:
    """Create a calendar event, persist it, and return the event dict."""
    if end_time is None:
        end_time = start_time + timedelta(hours=1)

    event_id = storage.create_event(
        conn, user_id, title, start_time, end_time, description, location
    )
    return {
        "id": event_id,
        "title": title,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "description": description,
        "location": location,
    }


def get_upcoming_events(
    conn: sqlite3.Connection, user_id: int, days: int = 7
) -> List[Dict[str, Any]]:
    """Return events in the next *days* days for *user_id*."""
    from_dt = datetime.utcnow()
    events = storage.get_events(conn, user_id, from_dt=from_dt)
    cutoff = (from_dt + timedelta(days=days)).isoformat()
    return [e for e in events if e["start_time"] <= cutoff]


def format_event(event: Dict[str, Any]) -> str:
    """Format a single event dict as a human-readable string."""
    lines = [
        f"📅 *{event['title']}* (#{event['id']})",
        f"  Start : {event['start_time']}",
        f"  End   : {event.get('end_time', 'N/A')}",
    ]
    if event.get("location"):
        lines.append(f"  📍 {event['location']}")
    if event.get("description"):
        lines.append(f"  {event['description']}")
    return "\n".join(lines)


def format_event_list(events: List[Dict[str, Any]]) -> str:
    """Format a list of events as Markdown-friendly text."""
    if not events:
        return "No upcoming events."
    return "\n\n".join(format_event(e) for e in events)


# ── iCal export ───────────────────────────────────────────────────────────────

def export_ical(
    events: List[Dict[str, Any]], filename: Optional[str] = None
) -> Path:
    """
    Export *events* to an iCal (.ics) file and return the file path.
    """
    cal = Calendar()
    cal.add("prodid", "-//AI Personal Assistant//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")

    tz = pytz.utc

    for ev in events:
        vevent = Event()
        vevent.add("summary", ev["title"])
        vevent.add("uid", str(uuid.uuid4()))

        try:
            start = datetime.fromisoformat(ev["start_time"]).replace(tzinfo=tz)
            vevent.add("dtstart", start)
        except (ValueError, KeyError):
            continue

        if ev.get("end_time"):
            try:
                end = datetime.fromisoformat(ev["end_time"]).replace(tzinfo=tz)
                vevent.add("dtend", end)
            except ValueError:
                pass

        if ev.get("description"):
            vevent.add("description", ev["description"])
        if ev.get("location"):
            vevent.add("location", vText(ev["location"]))

        cal.add_component(vevent)

    exports_dir = Path(config.EXPORTS_DIR)
    exports_dir.mkdir(parents=True, exist_ok=True)
    if filename is None:
        filename = f"calendar_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.ics"
    filepath = exports_dir / filename

    with open(filepath, "wb") as fh:
        fh.write(cal.to_ical())

    logger.info("Exported %d events to %s", len(events), filepath)
    return filepath
