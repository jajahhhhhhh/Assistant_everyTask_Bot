"""
Link fetching and summarization module.

Fetches URLs shared in chat and provides AI-powered summaries.
Supports automatic detection of property listings.
"""

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import config

logger = logging.getLogger(__name__)

# ── URL extraction ────────────────────────────────────────────────────────────

URL_PATTERN = re.compile(
    r'https?://[^\s<>"{}|^`\[\]]+',
    re.IGNORECASE
)


def extract_urls(text: str) -> List[str]:
    """Extract all URLs from text."""
    if not text:
        return []
    
    urls = URL_PATTERN.findall(text)
    # Filter out Telegram API URLs
    return [u for u in urls if "api.telegram.org" not in u]


def classify_url(url: str) -> str:
    """Classify URL by platform type."""
    u = url.lower()
    
    if "facebook.com" in u:
        return "facebook"
    if "google.com/maps" in u or "maps.app.goo" in u or "goo.gl/maps" in u:
        return "maps"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "instagram.com" in u:
        return "instagram"
    if "lazada" in u or "shopee" in u:
        return "shop"
    if "t.me" in u or "telegram" in u:
        return "telegram"
    if "airbnb" in u:
        return "airbnb"
    if "booking.com" in u:
        return "booking"
    if "agoda" in u:
        return "agoda"
    if "twitter.com" in u or "x.com" in u:
        return "twitter"
    if "linkedin.com" in u:
        return "linkedin"
    if "tiktok.com" in u:
        return "tiktok"
    
    return "web"


URL_ICONS = {
    "facebook": "📘",
    "youtube": "🎥",
    "instagram": "📸",
    "shop": "🛒",
    "maps": "📍",
    "airbnb": "🏠",
    "booking": "🏨",
    "agoda": "🏨",
    "twitter": "🐦",
    "linkedin": "💼",
    "tiktok": "🎵",
    "web": "🌐",
}


# ── Listing detection ─────────────────────────────────────────────────────────

LISTING_PATTERNS = [
    # Price patterns
    r'\d[\d,]*\s*(k|m|,000|฿|baht|บาท|\/month|\/mo|\/year)',
    # Bedroom patterns
    r'\d\s*(bed|br|bedroom|ห้อง|спальн)',
    # Property keywords
    r'(rent|sale|lease|ขาย|เช่า|аренд|продаж)',
    # Location indicators
    r'(location|ที่ตั้ง|расположен)',
]


def looks_like_listing(text: str) -> bool:
    """Check if text looks like a property listing."""
    if not text:
        return False
    
    t = text.lower()
    matches = 0
    
    for pattern in LISTING_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE):
            matches += 1
    
    # Need at least 2 indicators to consider it a listing
    return matches >= 2


# ── Web fetching ──────────────────────────────────────────────────────────────

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    logger.warning("httpx not installed, link fetching will be limited")


async def fetch_url(url: str, timeout: float = 8.0) -> Optional[str]:
    """Fetch URL content."""
    if not HTTPX_AVAILABLE:
        return None
    
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
            }
        ) as client:
            response = await client.get(url)
            if response.status_code == 200:
                return response.text
            return None
    except Exception as exc:
        logger.warning("Failed to fetch %s: %s", url, exc)
        return None


def extract_metadata(html: str) -> Dict[str, str]:
    """Extract metadata from HTML."""
    metadata = {
        "title": "",
        "description": "",
        "og_title": "",
        "og_description": "",
        "body_text": "",
    }
    
    if not html:
        return metadata
    
    # Extract <title>
    title_match = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
    if title_match:
        metadata["title"] = title_match.group(1).strip()
    
    # Extract og:title
    og_title = re.search(r'property="og:title"[^>]*content="([^"]+)"', html, re.IGNORECASE)
    if og_title:
        metadata["og_title"] = og_title.group(1).strip()
    
    # Extract og:description
    og_desc = re.search(r'property="og:description"[^>]*content="([^"]+)"', html, re.IGNORECASE)
    if og_desc:
        metadata["og_description"] = og_desc.group(1).strip()
    
    # Extract meta description
    meta_desc = re.search(r'name="description"[^>]*content="([^"]+)"', html, re.IGNORECASE)
    if meta_desc:
        metadata["description"] = meta_desc.group(1).strip()
    
    # Extract body text (cleaned)
    body = html
    body = re.sub(r'<script[\s\S]*?</script>', '', body, flags=re.IGNORECASE)
    body = re.sub(r'<style[\s\S]*?</style>', '', body, flags=re.IGNORECASE)
    body = re.sub(r'<[^>]+>', ' ', body)
    body = re.sub(r'\s+', ' ', body)
    metadata["body_text"] = body.strip()[:2000]
    
    return metadata


# ── AI summarization ──────────────────────────────────────────────────────────

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


def _summarize_with_ai(url: str, metadata: Dict[str, str], is_listing: bool) -> Optional[Dict[str, str]]:
    """Summarize URL content using AI."""
    client = _get_openai()
    if client is None:
        return None
    
    title = metadata.get("og_title") or metadata.get("title") or "Unknown"
    description = metadata.get("og_description") or metadata.get("description") or ""
    body = metadata.get("body_text", "")[:1200]
    
    context = f"Title: {title}\nDescription: {description}\nContent: {body}"
    
    if is_listing:
        system_prompt = (
            "Extract property listing info. Return JSON only:\n"
            '{"th":"Thai summary","ru":"Russian summary","en":"English summary",'
            '"price":"","beds":"","location":""}\n'
            "No markdown, pure JSON."
        )
    else:
        system_prompt = (
            "Summarize this page briefly. Return JSON only:\n"
            '{"th":"2-3 sentences in Thai","ru":"2-3 sentences in Russian","en":"2-3 sentences in English"}\n'
            "No markdown, pure JSON."
        )
    
    try:
        response = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"URL: {url}\n\n{context}"},
            ],
            temperature=0.3,
            max_tokens=400,
        )
        
        result = response.choices[0].message.content.strip()
        # Clean markdown if present
        result = re.sub(r'```json|```', '', result).strip()
        
        import json
        return json.loads(result)
        
    except Exception as exc:
        logger.error("AI summarization failed: %s", exc)
        return None


# ── Main API ──────────────────────────────────────────────────────────────────

async def fetch_and_summarize(
    url: str,
    sender_name: str = "User",
    sender_lang: str = "en",
) -> Optional[Dict[str, Any]]:
    """
    Fetch a URL and generate a summary.
    
    Args:
        url: The URL to fetch
        sender_name: Name of the person who shared the link
        sender_lang: Preferred language of the sender (th, ru, en)
        
    Returns:
        Dict with keys: summary, is_listing, title, url, parsed_data
    """
    url_type = classify_url(url)
    
    # Skip Telegram links
    if url_type == "telegram":
        return None
    
    # Handle maps specially
    if url_type == "maps":
        return {
            "summary": f"📍 *Location shared by {sender_name}*\n🔗 [Open Maps]({url})",
            "is_listing": False,
            "title": "Location",
            "url": url,
            "parsed_data": None,
        }
    
    # Fetch the URL
    html = await fetch_url(url)
    if not html:
        return None
    
    # Extract metadata
    metadata = extract_metadata(html)
    title = metadata.get("og_title") or metadata.get("title") or "Link"
    title = title[:60]
    
    # Check if it's a listing
    full_text = " ".join([
        metadata.get("title", ""),
        metadata.get("og_title", ""),
        metadata.get("og_description", ""),
        metadata.get("body_text", ""),
    ])
    is_listing = looks_like_listing(full_text)
    
    # Get AI summary
    parsed = _summarize_with_ai(url, metadata, is_listing)
    
    # Build summary
    icon = "🏠" if is_listing else URL_ICONS.get(url_type, "🌐")
    
    lines = [f"{icon} *{title}*"]
    
    if parsed:
        if parsed.get("th"):
            lines.append(f"🇹🇭 {parsed['th']}")
        if parsed.get("ru"):
            lines.append(f"🇷🇺 {parsed['ru']}")
        if sender_lang == "en" and parsed.get("en"):
            lines.append(f"🇬🇧 {parsed['en']}")
        if is_listing:
            if parsed.get("price"):
                lines.append(f"💰 {parsed['price']}")
            if parsed.get("beds"):
                lines.append(f"🛏 {parsed['beds']}")
            if parsed.get("location"):
                lines.append(f"📍 {parsed['location']}")
    else:
        # Fallback: use meta description
        desc = metadata.get("og_description") or metadata.get("description")
        if desc:
            lines.append(desc[:200])
    
    lines.append(f"\n🔗 [Open Link]({url})")
    
    return {
        "summary": "\n".join(lines),
        "is_listing": is_listing,
        "title": title,
        "url": url,
        "parsed_data": parsed,
    }


def format_link_summary(result: Dict[str, Any]) -> str:
    """Format link summary for Telegram."""
    if not result:
        return ""
    return result.get("summary", "")
