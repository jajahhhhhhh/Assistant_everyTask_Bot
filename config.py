"""
Configuration module for the AI Personal Assistant Bot.
Reads settings from environment variables (and an optional .env file).
"""

import os
import logging
from pathlib import Path

from dotenv import load_dotenv

# Load .env file if present (for local development)
load_dotenv()


# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

# ── OpenAI ────────────────────────────────────────────────────────────────────
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")

# ── Storage ───────────────────────────────────────────────────────────────────
DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/assistant.db")
EXPORTS_DIR: str = os.getenv("EXPORTS_DIR", "exports")

# ── Scheduling / Time ─────────────────────────────────────────────────────────
TIMEZONE: str = os.getenv("TIMEZONE", "Asia/Bangkok")

# ── Secretary / household ─────────────────────────────────────────────────────
# The two people whose shared life this bot keeps track of.
HOUSEHOLD_MEMBERS: list = [
    m.strip() for m in os.getenv("HOUSEHOLD_MEMBERS", "J,Farid").split(",") if m.strip()
]

# Default currency for the shared ledger.
DEFAULT_CURRENCY: str = os.getenv("DEFAULT_CURRENCY", "THB")

# Telegram user id of the household partner, so their voice notes are attributed
# to them automatically. Leave blank to attribute everything to the bot owner.
PARTNER_TELEGRAM_ID: str = os.getenv("PARTNER_TELEGRAM_ID", "")
PARTNER_NAME: str = os.getenv("PARTNER_NAME", "Farid")
OWNER_NAME: str = os.getenv("OWNER_NAME", "J")

# Run every voice note through the secretary pipeline automatically.
SECRETARY_AUTO: bool = os.getenv("SECRETARY_AUTO", "true").lower() in ("1", "true", "yes")

# Vision-capable model for reading receipt photos (falls back to a sane default).
OPENAI_VISION_MODEL: str = os.getenv("OPENAI_VISION_MODEL", "")

# ── Google sync ───────────────────────────────────────────────────────────────
# Shared "JF" calendar, shared ledger spreadsheet, and the Drive folder that
# generated reports land in. All optional — sync is skipped when unset.
JF_CALENDAR_ID: str = os.getenv("JF_CALENDAR_ID", "")
LEDGER_SHEET_ID: str = os.getenv("LEDGER_SHEET_ID", "")
DRIVE_FOLDER_ID: str = os.getenv("DRIVE_FOLDER_ID", "")

# Auth mode 1: a service account key (raw JSON or a path to the key file).
GOOGLE_SERVICE_ACCOUNT_JSON: str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")

# Auth mode 2: an OAuth refresh token for the account that owns the calendar.
GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REFRESH_TOKEN: str = os.getenv("GOOGLE_REFRESH_TOKEN", "")

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
)

# ── Directory bootstrap ───────────────────────────────────────────────────────
Path(DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
Path(EXPORTS_DIR).mkdir(parents=True, exist_ok=True)
