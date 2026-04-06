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
TIMEZONE: str = os.getenv("TIMEZONE", "UTC")

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
)

# ── Directory bootstrap ───────────────────────────────────────────────────────
Path(DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
Path(EXPORTS_DIR).mkdir(parents=True, exist_ok=True)
