"""
Receipt capture — turn a photo of a receipt into a ledger entry.

Uses an OpenAI vision-capable model to read merchant, date, total, currency and
line items, then maps the result onto the finance module's expense shape.
Degrades gracefully: without an API key (or without a vision-capable model) the
caller gets ``None`` and can fall back to asking the user to type the amount.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

import config
from assistant import finance

logger = logging.getLogger(__name__)

#: Models known to accept image input.  Anything else falls back to this default.
_VISION_FALLBACK_MODEL = "gpt-4o-mini"
_VISION_CAPABLE = ("gpt-4o", "gpt-4.1", "gpt-4-turbo", "gpt-5", "o4", "chatgpt-4o")

_openai_client = None


def _get_openai():
    global _openai_client
    if _openai_client is None and getattr(config, "OPENAI_API_KEY", ""):
        try:
            from openai import OpenAI
            _openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
        except Exception as exc:  # pragma: no cover
            logger.warning("Could not initialise OpenAI client: %s", exc)
    return _openai_client


def is_available() -> bool:
    """True when receipt parsing can run."""
    return bool(getattr(config, "OPENAI_API_KEY", ""))


def _vision_model() -> str:
    """Pick a vision-capable model, preferring the configured one when suitable."""
    configured = (getattr(config, "OPENAI_VISION_MODEL", "") or "").strip()
    if configured:
        return configured
    current = (getattr(config, "OPENAI_MODEL", "") or "").lower()
    if any(current.startswith(prefix) for prefix in _VISION_CAPABLE):
        return config.OPENAI_MODEL
    return _VISION_FALLBACK_MODEL


_RECEIPT_PROMPT = """You are reading a photo of a receipt for a two-person \
household in Thailand (members: "J" and "Farid"). Receipts may be in Thai, \
English or Russian.

Return STRICT JSON only:

{
  "merchant": "shop or restaurant name, or null",
  "spent_on": "YYYY-MM-DD from the receipt, or null if not printed",
  "total": 0.0,
  "currency": "THB|USD|EUR|RUB",
  "category": "rent|food|household|utilities|transport|shared|other",
  "items": ["short line item", "..."],
  "confidence": "high|medium|low",
  "unreadable": false
}

Rules:
- "total" is the final amount actually paid, after discounts and service charge.
- Thai receipts default to THB. Never guess a currency the receipt does not show
  or imply.
- Choose the category from what was bought: a supermarket or restaurant is
  "food", cleaning or hardware is "household", electric/water/internet bills are
  "utilities", fuel or taxi is "transport", a rental payment is "rent".
- If you cannot read the total, set "unreadable": true and "total": 0.
- JSON only. No prose, no markdown fences."""


async def download_photo(bot, file_id: str, suffix: str = ".jpg") -> Optional[Path]:
    """Download a Telegram photo to a temp file and return its path."""
    import tempfile
    try:
        file = await bot.get_file(file_id)
        path = Path(tempfile.gettempdir()) / f"receipt_{file_id}{suffix}"
        await file.download_to_drive(path)
        return path
    except Exception as exc:
        logger.error("Failed to download receipt photo: %s", exc)
        return None


def _encode(image_path: Path) -> Optional[str]:
    """Read an image into a base64 data URL."""
    try:
        mime = mimetypes.guess_type(str(image_path))[0] or "image/jpeg"
        payload = base64.b64encode(image_path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{payload}"
    except Exception as exc:
        logger.error("Could not encode receipt image: %s", exc)
        return None


def _strip_fences(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def parse_receipt(image_path: Path, today: Optional[date] = None) -> Optional[Dict[str, Any]]:
    """
    Read a receipt image and return its parsed fields, or None on failure.

    The temp file is deleted afterwards regardless of outcome.
    """
    today = today or date.today()
    client = _get_openai()
    if client is None:
        logger.warning("Receipt parsing unavailable — no OpenAI key")
        return None

    data_url = _encode(image_path)
    if data_url is None:
        return None

    try:
        response = client.chat.completions.create(
            model=_vision_model(),
            messages=[
                {"role": "system", "content": _RECEIPT_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text",
                         "text": f"Today is {today.isoformat()}. Read this receipt."},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            temperature=0.0,
        )
        parsed = json.loads(_strip_fences(response.choices[0].message.content))
    except json.JSONDecodeError as exc:
        logger.warning("Receipt parse returned invalid JSON: %s", exc)
        return None
    except Exception as exc:  # pragma: no cover - network dependent
        logger.error("Receipt parsing failed: %s", exc)
        return None
    finally:
        try:
            if image_path.exists():
                image_path.unlink()
        except Exception:
            pass

    if not isinstance(parsed, dict) or parsed.get("unreadable"):
        return None

    try:
        total = float(parsed.get("total") or 0)
    except (TypeError, ValueError):
        total = 0.0
    if total <= 0:
        return None

    category = (parsed.get("category") or "").lower()
    if category not in finance.CATEGORIES:
        category = finance.guess_category(
            " ".join([str(parsed.get("merchant") or "")] +
                     [str(i) for i in parsed.get("items", [])])
        )

    return {
        "merchant": parsed.get("merchant") or "",
        "spent_on": parsed.get("spent_on") or today.isoformat(),
        "total": round(total, 2),
        "currency": (parsed.get("currency") or finance.DEFAULT_CURRENCY).upper(),
        "category": category,
        "items": [str(i) for i in parsed.get("items", []) if i][:12],
        "confidence": parsed.get("confidence") or "medium",
    }


def to_expense_payload(
    parsed: Dict[str, Any],
    payer: str = "J",
    split: str = "equal",
) -> Dict[str, Any]:
    """Map a parsed receipt onto the extraction schema's expense shape."""
    description = parsed.get("merchant") or ""
    items = parsed.get("items") or []
    if items and len(description) < 60:
        description = f"{description} ({', '.join(items[:3])})".strip()
    return {
        "amount": parsed["total"],
        "currency": parsed["currency"],
        "category": parsed["category"],
        "description": description[:160] or parsed["category"],
        "payer": payer,
        "split": split,
        "spent_on": parsed["spent_on"],
    }


def format_receipt(parsed: Dict[str, Any]) -> str:
    """Human-readable confirmation of what was read off the receipt."""
    emoji = finance.CATEGORY_EMOJI.get(parsed["category"], "🧾")
    lines = [
        "🧾 *Receipt read*",
        "",
        f"{emoji} *{finance.format_money(parsed['total'], parsed['currency'])}* "
        f"— {parsed.get('merchant') or parsed['category']}",
        f"  {parsed['spent_on']} · {parsed['category']}",
    ]
    if parsed.get("items"):
        lines.append("  _" + ", ".join(parsed["items"][:5]) + "_")
    if parsed.get("confidence") == "low":
        lines.append("\n⚠️ _Low confidence — check the amount._")
    return "\n".join(lines)
