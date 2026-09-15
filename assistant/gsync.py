"""
Google sync — push the secretary's output to the shared JF calendar and the
shared finance spreadsheet in Drive.

Everything in this module is optional.  When credentials are absent every entry
point returns a "skipped" result instead of raising, so the bot runs perfectly
well with local-only storage and starts syncing the moment the environment is
configured.

Credentials
-----------
Two supported modes, checked in this order:

1. **Service account** — set ``GOOGLE_SERVICE_ACCOUNT_JSON`` to either the raw
   JSON or a path to the key file.  Share the JF calendar and the Drive folder
   with the service account's email address.
2. **OAuth refresh token** — set ``GOOGLE_CLIENT_ID``, ``GOOGLE_CLIENT_SECRET``
   and ``GOOGLE_REFRESH_TOKEN`` for the Google account that owns the calendar.

Targets
-------
``JF_CALENDAR_ID``   calendar id of the shared J/Farid calendar
``LEDGER_SHEET_ID``  spreadsheet id of the shared expense sheet
``DRIVE_FOLDER_ID``  folder to drop generated reports into
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import config
from assistant import finance

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

#: Header row written to a fresh ledger sheet.
LEDGER_HEADER = [
    "ID", "Date", "Amount", "Currency", "Category", "Description",
    "Payer", "Payer share", "Source", "Logged at",
]

_services: Dict[str, Any] = {}


# ── Config helpers ────────────────────────────────────────────────────────────

def _cfg(name: str, default: str = "") -> str:
    """Read a setting from config first, then the environment."""
    return (getattr(config, name, "") or os.getenv(name, "") or default).strip()


def calendar_id() -> str:
    return _cfg("JF_CALENDAR_ID")


def ledger_sheet_id() -> str:
    return _cfg("LEDGER_SHEET_ID")


def drive_folder_id() -> str:
    return _cfg("DRIVE_FOLDER_ID")


def _credentials():
    """Build Google credentials from whichever mode is configured, or None."""
    raw_sa = _cfg("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw_sa:
        try:
            from google.oauth2 import service_account
            if raw_sa.lstrip().startswith("{"):
                info = json.loads(raw_sa)
            else:
                info = json.loads(Path(raw_sa).read_text())
            return service_account.Credentials.from_service_account_info(
                info, scopes=SCOPES
            )
        except Exception as exc:
            logger.error("Invalid GOOGLE_SERVICE_ACCOUNT_JSON: %s", exc)
            return None

    client_id = _cfg("GOOGLE_CLIENT_ID")
    client_secret = _cfg("GOOGLE_CLIENT_SECRET")
    refresh_token = _cfg("GOOGLE_REFRESH_TOKEN")
    if client_id and client_secret and refresh_token:
        try:
            from google.oauth2.credentials import Credentials
            return Credentials(
                token=None,
                refresh_token=refresh_token,
                client_id=client_id,
                client_secret=client_secret,
                token_uri="https://oauth2.googleapis.com/token",
                scopes=SCOPES,
            )
        except Exception as exc:
            logger.error("Could not build OAuth credentials: %s", exc)
            return None

    return None


def _service(name: str, version: str):
    """Return a cached Google API client, or None when unavailable."""
    key = f"{name}:{version}"
    if key in _services:
        return _services[key]
    creds = _credentials()
    if creds is None:
        return None
    try:
        from googleapiclient.discovery import build
        service = build(name, version, credentials=creds, cache_discovery=False)
    except ImportError:
        logger.warning(
            "google-api-python-client is not installed — Google sync disabled"
        )
        return None
    except Exception as exc:
        logger.error("Could not build the %s service: %s", name, exc)
        return None
    _services[key] = service
    return service


def is_configured() -> bool:
    """True when credentials exist (regardless of which targets are set)."""
    return _credentials() is not None


def status() -> Dict[str, Any]:
    """Report what is wired up — used by the /gsync command."""
    return {
        "credentials": is_configured(),
        "calendar_id": calendar_id() or None,
        "ledger_sheet_id": ledger_sheet_id() or None,
        "drive_folder_id": drive_folder_id() or None,
        "timezone": _cfg("TIMEZONE", "Asia/Bangkok"),
    }


# ── Local sync bookkeeping ────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS gsync_map (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT    NOT NULL,          -- 'event' | 'expense'
    local_id    INTEGER NOT NULL,
    remote_id   TEXT    NOT NULL,
    synced_at   TEXT    NOT NULL,
    UNIQUE (kind, local_id)
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the sync-mapping table.  Safe to call repeatedly."""
    conn.executescript(_SCHEMA)
    conn.commit()


def _remember(conn: sqlite3.Connection, kind: str, local_id: int, remote_id: str) -> None:
    ensure_schema(conn)
    conn.execute(
        "INSERT OR REPLACE INTO gsync_map (kind, local_id, remote_id, synced_at) "
        "VALUES (?, ?, ?, ?)",
        (kind, local_id, remote_id, datetime.utcnow().isoformat()),
    )
    conn.commit()


def _already_synced(conn: sqlite3.Connection, kind: str, local_id: int) -> Optional[str]:
    ensure_schema(conn)
    row = conn.execute(
        "SELECT remote_id FROM gsync_map WHERE kind=? AND local_id=?", (kind, local_id)
    ).fetchone()
    return row["remote_id"] if row else None


# ── Calendar ──────────────────────────────────────────────────────────────────

def push_event(
    conn: sqlite3.Connection,
    event: Dict[str, Any],
    timezone_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create *event* on the shared calendar.

    Returns ``{"status": "created"|"skipped"|"error"|"duplicate", ...}``.
    Idempotent: an event already pushed is reported as ``duplicate``.
    """
    cal_id = calendar_id()
    if not cal_id:
        return {"status": "skipped", "reason": "JF_CALENDAR_ID not set"}

    service = _service("calendar", "v3")
    if service is None:
        return {"status": "skipped", "reason": "Google credentials not configured"}

    local_id = event.get("id")
    if local_id is not None:
        existing = _already_synced(conn, "event", int(local_id))
        if existing:
            return {"status": "duplicate", "remote_id": existing}

    tz = timezone_name or _cfg("TIMEZONE", "Asia/Bangkok")
    start = str(event.get("start_time") or "")
    end = str(event.get("end_time") or "")
    if not start:
        return {"status": "error", "reason": "event has no start time"}
    if not end:
        try:
            end = (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat()
        except ValueError:
            end = start

    body = {
        "summary": event.get("title", "(untitled)"),
        "description": event.get("description") or "",
        "location": event.get("location") or "",
        "start": {"dateTime": start, "timeZone": tz},
        "end": {"dateTime": end, "timeZone": tz},
        "source": {"title": "Assistant EveryTask Bot", "url": "https://t.me"},
    }
    if event.get("all_day"):
        body["start"] = {"date": start[:10]}
        body["end"] = {"date": (end or start)[:10]}

    try:
        created = service.events().insert(calendarId=cal_id, body=body).execute()
    except Exception as exc:
        logger.error("Calendar insert failed: %s", exc)
        return {"status": "error", "reason": str(exc)}

    remote_id = created.get("id", "")
    if local_id is not None and remote_id:
        _remember(conn, "event", int(local_id), remote_id)
    return {
        "status": "created",
        "remote_id": remote_id,
        "link": created.get("htmlLink"),
    }


def push_events(conn: sqlite3.Connection, events: List[Dict[str, Any]]) -> Dict[str, int]:
    """Push several events; returns a count per outcome."""
    tally = {"created": 0, "duplicate": 0, "skipped": 0, "error": 0}
    for event in events:
        result = push_event(conn, event)
        tally[result["status"]] = tally.get(result["status"], 0) + 1
    return tally


# ── Sheets ────────────────────────────────────────────────────────────────────

def _expense_row(exp: Dict[str, Any]) -> List[Any]:
    return [
        exp["id"],
        exp["spent_on"],
        exp["amount"],
        exp["currency"],
        exp["category"],
        exp["description"],
        exp["payer"],
        exp["payer_share"],
        exp["source"],
        datetime.utcnow().isoformat(timespec="seconds"),
    ]


def _ensure_header(service, sheet_id: str, tab: str) -> None:
    """Write the header row when the tab is empty."""
    try:
        existing = service.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"{tab}!A1:J1"
        ).execute()
        if existing.get("values"):
            return
    except Exception:
        pass  # tab may not exist yet; the append below creates content anyway
    try:
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"{tab}!A1",
            valueInputOption="RAW",
            body={"values": [LEDGER_HEADER]},
        ).execute()
    except Exception as exc:
        logger.warning("Could not write ledger header: %s", exc)


def push_expenses(
    conn: sqlite3.Connection,
    expenses: List[Dict[str, Any]],
    tab: str = "Ledger",
) -> Dict[str, Any]:
    """
    Append expense rows to the shared spreadsheet, skipping ones already synced.
    """
    sheet_id = ledger_sheet_id()
    if not sheet_id:
        return {"status": "skipped", "reason": "LEDGER_SHEET_ID not set", "appended": 0}

    service = _service("sheets", "v4")
    if service is None:
        return {"status": "skipped", "reason": "Google credentials not configured",
                "appended": 0}

    pending = [e for e in expenses
               if e.get("id") is None or not _already_synced(conn, "expense", int(e["id"]))]
    if not pending:
        return {"status": "duplicate", "appended": 0}

    _ensure_header(service, sheet_id, tab)

    try:
        service.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range=f"{tab}!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [_expense_row(e) for e in pending]},
        ).execute()
    except Exception as exc:
        logger.error("Sheets append failed: %s", exc)
        return {"status": "error", "reason": str(exc), "appended": 0}

    for exp in pending:
        if exp.get("id") is not None:
            _remember(conn, "expense", int(exp["id"]), f"{sheet_id}:{tab}")

    return {"status": "appended", "appended": len(pending)}


def sync_ledger(
    conn: sqlite3.Connection,
    month: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Push every not-yet-synced expense (optionally just one month) to Sheets."""
    expenses = finance.list_expenses(conn, user_id=user_id, month=month)
    # Oldest first so the sheet reads chronologically.
    return push_expenses(conn, list(reversed(expenses)))


# ── Drive ─────────────────────────────────────────────────────────────────────

def upload_report(path: Path, name: Optional[str] = None,
                  mime_type: str = "text/markdown") -> Dict[str, Any]:
    """Upload a generated report into the shared Drive folder."""
    folder_id = drive_folder_id()
    if not folder_id:
        return {"status": "skipped", "reason": "DRIVE_FOLDER_ID not set"}

    service = _service("drive", "v3")
    if service is None:
        return {"status": "skipped", "reason": "Google credentials not configured"}

    try:
        from googleapiclient.http import MediaFileUpload
        media = MediaFileUpload(str(path), mimetype=mime_type, resumable=False)
        created = service.files().create(
            body={"name": name or path.name, "parents": [folder_id]},
            media_body=media,
            fields="id, webViewLink",
        ).execute()
    except Exception as exc:
        logger.error("Drive upload failed: %s", exc)
        return {"status": "error", "reason": str(exc)}

    return {"status": "uploaded", "file_id": created.get("id"),
            "link": created.get("webViewLink")}


def format_status() -> str:
    """Render :func:`status` for Telegram."""
    info = status()
    tick = lambda v: "✅" if v else "⚪️"
    return "\n".join([
        "🔗 *Google sync*",
        "",
        f"{tick(info['credentials'])} Credentials",
        f"{tick(info['calendar_id'])} Shared calendar "
        f"(`{info['calendar_id'] or 'not set'}`)",
        f"{tick(info['ledger_sheet_id'])} Ledger sheet "
        f"(`{info['ledger_sheet_id'] or 'not set'}`)",
        f"{tick(info['drive_folder_id'])} Drive folder "
        f"(`{info['drive_folder_id'] or 'not set'}`)",
        "",
        f"_Timezone: {info['timezone']}_",
    ])
