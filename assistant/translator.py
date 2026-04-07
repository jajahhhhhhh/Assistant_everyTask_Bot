"""
Translator module — multi-language translation support.

Supports translation between multiple languages using OpenAI GPT.
Falls back to basic language detection when no API key is available.

Supported languages:
    en (English), th (Thai), ru (Russian), zh (Chinese), 
    ja (Japanese), ko (Korean), es (Spanish), fr (French),
    de (German), it (Italian), pt (Portuguese), vi (Vietnamese),
    ar (Arabic), hi (Hindi), id (Indonesian), ms (Malay)
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import config

logger = logging.getLogger(__name__)

# ── Language codes and names ──────────────────────────────────────────────────

SUPPORTED_LANGUAGES: Dict[str, str] = {
    "en": "English",
    "th": "Thai",
    "ru": "Russian",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "vi": "Vietnamese",
    "ar": "Arabic",
    "hi": "Hindi",
    "id": "Indonesian",
    "ms": "Malay",
    "uk": "Ukrainian",
    "nl": "Dutch",
    "pl": "Polish",
    "tr": "Turkish",
}

# Language name to code mapping (for natural input)
LANGUAGE_ALIASES: Dict[str, str] = {
    # English names
    "english": "en",
    "thai": "th",
    "russian": "ru",
    "chinese": "zh",
    "mandarin": "zh",
    "japanese": "ja",
    "korean": "ko",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "portuguese": "pt",
    "vietnamese": "vi",
    "arabic": "ar",
    "hindi": "hi",
    "indonesian": "id",
    "malay": "ms",
    "ukrainian": "uk",
    "dutch": "nl",
    "polish": "pl",
    "turkish": "tr",
    # Native names
    "ไทย": "th",
    "ภาษาไทย": "th",
    "русский": "ru",
    "中文": "zh",
    "日本語": "ja",
    "한국어": "ko",
    "español": "es",
    "français": "fr",
    "deutsch": "de",
    "italiano": "it",
    "português": "pt",
    "tiếng việt": "vi",
    "العربية": "ar",
    "हिन्दी": "hi",
    "bahasa indonesia": "id",
    "bahasa melayu": "ms",
    "українська": "uk",
    "nederlands": "nl",
    "polski": "pl",
    "türkçe": "tr",
}

# Language emoji flags
LANGUAGE_FLAGS: Dict[str, str] = {
    "en": "🇬🇧",
    "th": "🇹🇭",
    "ru": "🇷🇺",
    "zh": "🇨🇳",
    "ja": "🇯🇵",
    "ko": "🇰🇷",
    "es": "🇪🇸",
    "fr": "🇫🇷",
    "de": "🇩🇪",
    "it": "🇮🇹",
    "pt": "🇵🇹",
    "vi": "🇻🇳",
    "ar": "🇸🇦",
    "hi": "🇮🇳",
    "id": "🇮🇩",
    "ms": "🇲🇾",
    "uk": "🇺🇦",
    "nl": "🇳🇱",
    "pl": "🇵🇱",
    "tr": "🇹🇷",
}


# ── Lazy OpenAI import ────────────────────────────────────────────────────────

_openai_client = None


def _get_openai():
    global _openai_client
    if _openai_client is None and config.OPENAI_API_KEY:
        try:
            from openai import OpenAI
            _openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
        except Exception as exc:
            logger.warning("Could not initialise OpenAI client: %s", exc)
    return _openai_client


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
    except Exception as exc:
        logger.error("OpenAI request failed: %s", exc)
        return None


# ── Language detection ────────────────────────────────────────────────────────

# Unicode ranges for basic language detection (fallback)
UNICODE_RANGES: Dict[str, List[Tuple[int, int]]] = {
    "th": [(0x0E00, 0x0E7F)],  # Thai
    "ru": [(0x0400, 0x04FF)],  # Cyrillic
    "zh": [(0x4E00, 0x9FFF), (0x3400, 0x4DBF)],  # CJK
    "ja": [(0x3040, 0x309F), (0x30A0, 0x30FF)],  # Hiragana, Katakana
    "ko": [(0xAC00, 0xD7AF), (0x1100, 0x11FF)],  # Hangul
    "ar": [(0x0600, 0x06FF)],  # Arabic
    "hi": [(0x0900, 0x097F)],  # Devanagari
    "vi": [(0x1EA0, 0x1EF9)],  # Vietnamese diacritics
}


def _detect_by_unicode(text: str) -> Optional[str]:
    """Detect language by Unicode character ranges (fallback method)."""
    for lang, ranges in UNICODE_RANGES.items():
        for char in text:
            code = ord(char)
            for start, end in ranges:
                if start <= code <= end:
                    return lang
    return None


def detect_language(text: str) -> str:
    """
    Detect the language of the given text.
    
    Uses OpenAI when available, falls back to Unicode-based detection.
    Returns a language code (e.g., 'en', 'th', 'ru').
    """
    if not text.strip():
        return "en"
    
    # Try OpenAI first
    client = _get_openai()
    if client:
        system = (
            "You are a language detection system. "
            "Identify the language of the user's text. "
            "Reply with ONLY the ISO 639-1 two-letter language code (e.g., en, th, ru, zh, ja)."
        )
        result = _chat(system, text[:500])  # Limit text length
        if result and len(result) == 2 and result.lower() in SUPPORTED_LANGUAGES:
            return result.lower()
    
    # Fallback: Unicode-based detection
    detected = _detect_by_unicode(text)
    if detected:
        return detected
    
    # Default to English
    return "en"


def resolve_language_code(lang_input: str) -> Optional[str]:
    """
    Resolve a language input (code or name) to a standard language code.
    
    Examples:
        'en' -> 'en'
        'English' -> 'en'
        'thai' -> 'th'
        'ไทย' -> 'th'
    """
    lang_lower = lang_input.lower().strip()
    
    # Direct code match
    if lang_lower in SUPPORTED_LANGUAGES:
        return lang_lower
    
    # Alias match
    if lang_lower in LANGUAGE_ALIASES:
        return LANGUAGE_ALIASES[lang_lower]
    
    return None


# ── Translation ───────────────────────────────────────────────────────────────

def translate(
    text: str,
    target_lang: str,
    source_lang: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Translate text to the target language.
    
    Args:
        text: The text to translate
        target_lang: Target language code or name (e.g., 'th', 'Thai')
        source_lang: Optional source language (auto-detected if not provided)
    
    Returns:
        Dict with keys: success, translated_text, source_lang, target_lang, error
    """
    # Resolve target language
    target_code = resolve_language_code(target_lang)
    if not target_code:
        return {
            "success": False,
            "error": f"Unsupported target language: {target_lang}",
            "translated_text": None,
            "source_lang": None,
            "target_lang": target_lang,
        }
    
    # Detect or resolve source language
    if source_lang:
        source_code = resolve_language_code(source_lang)
        if not source_code:
            source_code = detect_language(text)
    else:
        source_code = detect_language(text)
    
    # Skip if same language
    if source_code == target_code:
        return {
            "success": True,
            "translated_text": text,
            "source_lang": source_code,
            "target_lang": target_code,
            "note": "Source and target languages are the same",
        }
    
    # Try OpenAI translation
    client = _get_openai()
    if client:
        target_name = SUPPORTED_LANGUAGES[target_code]
        source_name = SUPPORTED_LANGUAGES.get(source_code, "Unknown")
        
        system = (
            f"You are a professional translator. "
            f"Translate the following text from {source_name} to {target_name}. "
            f"Provide ONLY the translation, no explanations or notes. "
            f"Preserve the original tone, style, and formatting."
        )
        
        translated = _chat(system, text)
        if translated:
            return {
                "success": True,
                "translated_text": translated,
                "source_lang": source_code,
                "target_lang": target_code,
            }
    
    # Fallback: no translation available
    return {
        "success": False,
        "error": "Translation service unavailable. Please set OPENAI_API_KEY.",
        "translated_text": None,
        "source_lang": source_code,
        "target_lang": target_code,
    }


def translate_multi(
    text: str,
    target_langs: List[str],
    source_lang: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Translate text to multiple target languages.
    
    Returns a dict mapping language codes to translation results.
    """
    results = {}
    for lang in target_langs:
        results[lang] = translate(text, lang, source_lang)
    return results


# ── Formatting helpers ────────────────────────────────────────────────────────

def format_translation_result(result: Dict[str, Any]) -> str:
    """Format a translation result as a human-readable string."""
    if not result.get("success"):
        return f"❌ Translation failed: {result.get('error', 'Unknown error')}"
    
    source_code = result.get("source_lang", "??")
    target_code = result.get("target_lang", "??")
    source_flag = LANGUAGE_FLAGS.get(source_code, "🌐")
    target_flag = LANGUAGE_FLAGS.get(target_code, "🌐")
    source_name = SUPPORTED_LANGUAGES.get(source_code, source_code)
    target_name = SUPPORTED_LANGUAGES.get(target_code, target_code)
    
    lines = [
        f"🌐 *Translation*",
        f"{source_flag} {source_name} → {target_flag} {target_name}",
        "",
        result.get("translated_text", ""),
    ]
    
    if result.get("note"):
        lines.append(f"\n_({result['note']})_")
    
    return "\n".join(lines)


def format_language_list() -> str:
    """Format the list of supported languages."""
    lines = ["🌍 *Supported Languages*\n"]
    for code, name in sorted(SUPPORTED_LANGUAGES.items(), key=lambda x: x[1]):
        flag = LANGUAGE_FLAGS.get(code, "🌐")
        lines.append(f"  {flag} `{code}` — {name}")
    return "\n".join(lines)


def format_detected_language(text: str) -> str:
    """Detect and format the language of the given text."""
    code = detect_language(text)
    name = SUPPORTED_LANGUAGES.get(code, code)
    flag = LANGUAGE_FLAGS.get(code, "🌐")
    return f"{flag} Detected: *{name}* (`{code}`)"
