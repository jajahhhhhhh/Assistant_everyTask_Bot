"""
Voice transcription module — OpenAI Whisper integration.

Transcribes voice messages sent to the Telegram bot.
Supports automatic language detection for TH/RU/EN.
"""

import logging
import tempfile
from pathlib import Path
from typing import Optional, Tuple

import config

logger = logging.getLogger(__name__)

# ── Lazy OpenAI import ────────────────────────────────────────────────────────

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


def is_available() -> bool:
    """Check if voice transcription is available."""
    return config.OPENAI_API_KEY is not None and len(config.OPENAI_API_KEY) > 0


async def download_voice_file(bot, file_id: str) -> Optional[Path]:
    """
    Download a voice file from Telegram.
    
    Args:
        bot: The Telegram bot instance
        file_id: The file_id of the voice message
        
    Returns:
        Path to the downloaded file, or None if failed
    """
    try:
        file = await bot.get_file(file_id)
        
        # Create temp file with .ogg extension (Telegram voice format)
        temp_dir = tempfile.gettempdir()
        file_path = Path(temp_dir) / f"voice_{file_id}.ogg"
        
        await file.download_to_drive(file_path)
        return file_path
        
    except Exception as exc:
        logger.error("Failed to download voice file: %s", exc)
        return None


def transcribe(audio_path: Path, language: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    Transcribe an audio file using OpenAI Whisper.
    
    Args:
        audio_path: Path to the audio file
        language: Optional language hint (e.g., 'th', 'ru', 'en')
        
    Returns:
        Tuple of (transcribed_text, detected_language) or (None, None) on failure
    """
    client = _get_openai()
    if client is None:
        logger.warning("OpenAI client not available for transcription")
        return None, None
    
    try:
        with open(audio_path, "rb") as audio_file:
            # Use Whisper API
            kwargs = {
                "model": "whisper-1",
                "file": audio_file,
                "response_format": "verbose_json",  # Get language detection
            }
            
            # Add language hint if provided
            if language and language in ("th", "ru", "en", "zh", "ja", "ko", "es", "fr", "de"):
                kwargs["language"] = language
            
            response = client.audio.transcriptions.create(**kwargs)
            
            text = response.text.strip() if hasattr(response, 'text') else str(response).strip()
            detected_lang = getattr(response, 'language', None)
            
            return text, detected_lang
            
    except Exception as exc:
        logger.error("Whisper transcription failed: %s", exc)
        return None, None
    finally:
        # Clean up temp file
        try:
            if audio_path.exists():
                audio_path.unlink()
        except Exception:
            pass


def detect_language_from_text(text: str) -> str:
    """Detect language from transcribed text using Unicode ranges."""
    if not text:
        return "en"
    
    # Check for Thai
    if any('\u0E00' <= c <= '\u0E7F' for c in text):
        return "th"
    
    # Check for Russian/Cyrillic
    if any('\u0400' <= c <= '\u04FF' for c in text):
        return "ru"
    
    # Check for Chinese
    if any('\u4E00' <= c <= '\u9FFF' for c in text):
        return "zh"
    
    # Check for Japanese (Hiragana/Katakana)
    if any(('\u3040' <= c <= '\u309F') or ('\u30A0' <= c <= '\u30FF') for c in text):
        return "ja"
    
    # Check for Korean
    if any('\uAC00' <= c <= '\uD7AF' for c in text):
        return "ko"
    
    # Default to English
    return "en"


# Language flags for display
LANG_FLAGS = {
    "th": "🇹🇭",
    "ru": "🇷🇺",
    "en": "🇬🇧",
    "zh": "🇨🇳",
    "ja": "🇯🇵",
    "ko": "🇰🇷",
    "es": "🇪🇸",
    "fr": "🇫🇷",
    "de": "🇩🇪",
}

LANG_LABELS = {
    "th": {"th": "ถอดเสียง", "en": "Transcribed"},
    "ru": {"ru": "Расшифровка", "en": "Transcribed"},
    "en": {"en": "Transcribed"},
}


def format_transcription(text: str, language: Optional[str] = None) -> str:
    """Format transcription result for display."""
    if not text:
        return "❌ Could not transcribe voice message."
    
    lang = language or detect_language_from_text(text)
    flag = LANG_FLAGS.get(lang, "🎤")
    
    # Get localized label
    labels = LANG_LABELS.get(lang, LANG_LABELS["en"])
    label = labels.get(lang, labels.get("en", "Transcribed"))
    
    return f"🎤 *{label}* {flag}\n\n_{text}_"
