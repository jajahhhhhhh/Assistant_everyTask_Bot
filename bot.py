"""
AI Personal Assistant — Telegram bot entry point.

Commands:
    /start          — Welcome message
    /help           — Command reference
    /tasks          — List all your tasks
    /addtask <text> — Create a task manually
    /done <id>      — Mark a task as done
    /summary        — Summarise recent conversation
    /brief          — Quick key points summary
    /digest         — Generate daily digest
    /remind <time> <message> — Set a reminder
    /reminders      — List upcoming reminders
    /calendar       — List upcoming calendar events
    /addevent <title> at <datetime> — Add a calendar event
    /exporttasks    — Export tasks as PDF
    /exportcal      — Export calendar as .ics file
    /status         — Progress report
    /insights       — Recommendations
    /weekly         — Weekly summary
    /projects       — List all projects
    /analyze <name> — Deep project analysis
    /scan           — Scan for actionable items
    /save           — Save pending items
    /skip           — Skip pending items
    /note <text>    — File a note through the secretary pipeline
    /log [n]        — Recent secretary log entries
    /week           — Week in review: notes, spending, balance
    /spend <amt> <desc> [#cat] [@payer] — Log a household expense
    /balance        — Who owes who right now
    /month [YYYY-MM]— Category rollup with month-over-month trend
    /ledger [month] — Line-item expense ledger
    /settle <amt>   — Record a repayment
    /fund [YYYY-MM] — Shared food fund status
    /topup <amt>    — Pay into the shared food fund
    /closemonth     — Month-end settlement across fund and personal spending
    /gsync          — Google sync status
    /sync           — Push pending ledger rows to the shared sheet
    /translate <lang> <text> — Translate text to target language
    /tr <lang> <text>        — Shorthand for /translate
    /trmulti <langs> <text>  — Translate to multiple languages
    /ruth, /thru, /ruen, /enru, /then, /enth — Quick translation shortcuts
    /detect <text>           — Detect language of text
    /langs                   — List supported languages

Any free-form message is processed by the AI:
  • Tasks are extracted and stored automatically.
  • Links are summarized.
  • Voice messages are transcribed, summarised and filed.
  • Photos of receipts are read into the shared ledger.
  • Reminders and events are created if detected.
  • A confirmation is sent back to the user.
"""

import asyncio
import logging
import re
import sys

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import config
from assistant import (
    analytics,
    autoscan,
    calendar_integration,
    context as ctx_module,
    digest as digest_module,
    files as file_module,
    links as links_module,
    finance as finance_module,
    gsync,
    processor,
    receipts as receipt_module,
    reminders as reminder_module,
    secretary,
    storage,
    tasks as task_module,
    translator,
    voice as voice_module,
)

logger = logging.getLogger(__name__)

# ── Database connection (shared across handlers via bot_data) ─────────────────

def get_conn(context: ContextTypes.DEFAULT_TYPE):
    if "db_conn" not in context.bot_data:
        context.bot_data["db_conn"] = storage.init_db()
    return context.bot_data["db_conn"]


# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = (
        f"👋 Hello {user.first_name}! I'm your AI Personal Assistant.\n\n"
        "*What I can do:*\n"
        "📝 Track and manage tasks\n"
        "📅 Schedule calendar events\n"
        "⏰ Set smart reminders\n"
        "🚀 Analyze projects & ideas\n"
        "🌐 Translate between 20+ languages\n"
        "🔗 Summarize links you share\n"
        "🎤 Transcribe voice messages\n"
        "📊 Provide insights & recommendations\n\n"
        "*Auto-features:*\n"
        "• Send me any message — I extract tasks automatically\n"
        "• Share a link — I'll summarize it\n"
        "• Send a voice note — I transcribe, summarise and file it\n"
        "• Photograph a receipt — I read it into the shared ledger\n"
        "• Every 10 messages — I scan for action items\n\n"
        "Use /help for all commands!"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ── /help ─────────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "📖 *Command Reference*\n\n"
        "*Task Management*\n"
        "/tasks — List all tasks\n"
        "/addtask `<description>` — Add a task\n"
        "/done `<id>` — Mark task as done\n"
        "/exporttasks — Export tasks as PDF\n\n"
        "*Secretary* 🗂\n"
        "/note `<text>` — File a note: summary, events, expenses, to-dos\n"
        "/log `[n]` — Recent log entries\n"
        "/week — Week in review\n"
        "_Send a voice note or a photo of a receipt and it is filed automatically._\n\n"
        "*Household finances* 💰\n"
        "/spend `<amount> <what>` `[#category] [@payer] [!mine|!theirs]`\n"
        "/balance — Who owes who right now\n"
        "/month `[YYYY-MM]` — Category rollup + trend\n"
        "/ledger `[YYYY-MM]` — Line-item ledger\n"
        "/settle `<amount> [note]` — Record a repayment\n"
        "/fund `[YYYY-MM]` — Shared food fund: in, spent, left\n"
        "/topup `<amount> [@member]` — Pay into the food fund\n"
        "/closemonth `[YYYY-MM]` — Settle the month across both tracks\n"
        "/gsync — Google sync status · /sync — push pending rows\n\n"
        "*Smart Extraction*\n"
        "/scan — Scan chat for actionable items\n"
        "/save — Save pending items\n"
        "/skip — Skip pending items\n\n"
        "*Projects* 🚀\n"
        "/projects — List all projects\n"
        "/analyze `<project>` — Deep project analysis\n\n"
        "*Reminders*\n"
        "/remind `<time> | <message>` — Set a reminder\n"
        "/reminders — List upcoming reminders\n\n"
        "*Calendar*\n"
        "/calendar — Upcoming events (7 days)\n"
        "/addevent `<title> at <datetime>` — Add event\n"
        "/exportcal — Export calendar as .ics\n\n"
        "*Translation* 🌐\n"
        "/translate `<lang> <text>` — Translate text\n"
        "/tr `<lang> <text>` — Shorthand\n"
        "/trmulti `<lang1,lang2> <text>` — Multi-translate\n"
        "/ruth /thru /ruen /enru /then /enth — Quick translate\n"
        "/detect `<text>` — Detect language\n"
        "/langs — List supported languages\n\n"
        "*Summaries & Insights*\n"
        "/summary — Conversation summary\n"
        "/brief — Quick key points\n"
        "/digest — Daily digest\n"
        "/status — Progress report\n"
        "/weekly — Weekly summary\n"
        "/insights — Recommendations\n\n"
        "💬 *Free-form message* — I'll extract tasks automatically!\n"
        "🔗 *Links* — I'll summarize shared URLs\n"
        "🎤 *Voice* — I'll transcribe voice messages"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ── /tasks ────────────────────────────────────────────────────────────────────

async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = get_conn(context)
    user_id = update.effective_user.id
    tasks = storage.get_tasks(conn, user_id)
    text = task_module.format_task_list(tasks)
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ── /addtask ──────────────────────────────────────────────────────────────────

async def cmd_addtask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = get_conn(context)
    user_id = update.effective_user.id
    description = " ".join(context.args) if context.args else ""
    if not description:
        await update.message.reply_text("Usage: /addtask <description>")
        return
    task = task_module.add_task(conn, user_id, title=description)
    await update.message.reply_text(
        f"✅ Task added:\n{task_module.format_task(task)}",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /done ─────────────────────────────────────────────────────────────────────

async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = get_conn(context)
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /done <task_id>")
        return
    task_id = int(context.args[0])
    updated = storage.update_task_status(conn, task_id, "done")
    if updated:
        await update.message.reply_text(f"✅ Task #{task_id} marked as done!")
    else:
        await update.message.reply_text(f"❌ Task #{task_id} not found.")


# ── /summary ──────────────────────────────────────────────────────────────────

async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = get_conn(context)
    user_id = update.effective_user.id
    messages = ctx_module.get_history(conn, user_id, limit=20)
    mode = context.args[0] if context.args else "short"
    summary = processor.summarise(messages, mode=mode)
    await update.message.reply_text(f"📋 *Summary*\n\n{summary}", parse_mode=ParseMode.MARKDOWN)


# ── /remind ───────────────────────────────────────────────────────────────────

async def cmd_remind(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = get_conn(context)
    user_id = update.effective_user.id
    full_text = " ".join(context.args) if context.args else ""
    if "|" not in full_text:
        await update.message.reply_text(
            "Usage: /remind <time expression> | <message>\n"
            "Example: /remind tomorrow at 9am | Call Alice"
        )
        return
    time_part, _, message_part = full_text.partition("|")
    reminder_id = reminder_module.schedule_reminder(
        conn, user_id, message_part.strip(), time_part.strip()
    )
    if reminder_id:
        remind_at = reminder_module.parse_reminder_time(time_part.strip())
        await update.message.reply_text(
            f"⏰ Reminder set for *{remind_at}*\nMessage: {message_part.strip()}",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await update.message.reply_text(
            "❌ Could not parse the time. Try: /remind tomorrow at 9am | Call Alice"
        )


# ── /reminders ────────────────────────────────────────────────────────────────

async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = get_conn(context)
    user_id = update.effective_user.id
    reminders = storage.get_user_reminders(conn, user_id)
    text = reminder_module.format_reminder_list(reminders)
    await update.message.reply_text(text)


# ── /calendar ─────────────────────────────────────────────────────────────────

async def cmd_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = get_conn(context)
    user_id = update.effective_user.id
    events = calendar_integration.get_upcoming_events(conn, user_id, days=7)
    text = calendar_integration.format_event_list(events)
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ── /addevent ─────────────────────────────────────────────────────────────────

async def cmd_addevent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = get_conn(context)
    user_id = update.effective_user.id
    full_text = " ".join(context.args) if context.args else ""
    if " at " not in full_text.lower():
        await update.message.reply_text(
            "Usage: /addevent <title> at <datetime>\n"
            "Example: /addevent Team standup at tomorrow 10am"
        )
        return

    idx = full_text.lower().index(" at ")
    title = full_text[:idx].strip()
    time_text = full_text[idx + 4:].strip()
    dt_str = processor.extract_datetime(time_text)
    if not dt_str:
        await update.message.reply_text("❌ Could not parse the date/time. Try: tomorrow at 10am")
        return

    from datetime import datetime
    start_dt = datetime.fromisoformat(dt_str)
    event = calendar_integration.add_event(conn, user_id, title=title, start_time=start_dt)
    await update.message.reply_text(
        f"📅 Event added:\n{calendar_integration.format_event(event)}",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /exporttasks ──────────────────────────────────────────────────────────────

async def cmd_exporttasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = get_conn(context)
    user_id = update.effective_user.id
    tasks = storage.get_tasks(conn, user_id)
    filepath = file_module.export_tasks_pdf(tasks)
    with open(filepath, "rb") as fh:
        await update.message.reply_document(document=fh, filename=filepath.name)


# ── /exportcal ────────────────────────────────────────────────────────────────

async def cmd_exportcal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = get_conn(context)
    user_id = update.effective_user.id
    events = storage.get_events(conn, user_id)
    filepath = calendar_integration.export_ical(events)
    with open(filepath, "rb") as fh:
        await update.message.reply_document(document=fh, filename=filepath.name)


# ── /status ───────────────────────────────────────────────────────────────────

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = get_conn(context)
    user_id = update.effective_user.id
    report = analytics.task_progress_report(conn, user_id)
    await update.message.reply_text(report, parse_mode=ParseMode.MARKDOWN)


# ── /insights ─────────────────────────────────────────────────────────────────

async def cmd_insights(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = get_conn(context)
    user_id = update.effective_user.id
    text = analytics.recommendations(conn, user_id)
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ── /weekly ───────────────────────────────────────────────────────────────────

async def cmd_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = get_conn(context)
    user_id = update.effective_user.id
    text = analytics.weekly_summary(conn, user_id)
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ── /translate or /tr ─────────────────────────────────────────────────────────

async def cmd_translate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Translate text to a target language.
    Usage: /translate <lang> <text>
           /tr <lang> <text>
    
    Examples:
        /translate th Hello, how are you?
        /tr russian Доброе утро
        /translate en สวัสดีครับ
    """
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "🌐 *Translation*\n\n"
            "Usage: `/translate <language> <text>`\n"
            "       `/tr <language> <text>`\n\n"
            "Examples:\n"
            "  `/tr th Hello, how are you?`\n"
            "  `/tr russian Good morning`\n"
            "  `/tr en สวัสดีครับ`\n\n"
            "Use /langs to see all supported languages.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    
    target_lang = context.args[0]
    text_to_translate = " ".join(context.args[1:])
    
    # Validate target language
    target_code = translator.resolve_language_code(target_lang)
    if not target_code:
        await update.message.reply_text(
            f"❌ Unknown language: `{target_lang}`\n\n"
            f"Use /langs to see supported languages.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    
    # Perform translation
    result = translator.translate(text_to_translate, target_code)
    response = translator.format_translation_result(result)
    await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)


# ── /langs ────────────────────────────────────────────────────────────────────

async def cmd_langs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all supported languages for translation."""
    text = translator.format_language_list()
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ── /detect ───────────────────────────────────────────────────────────────────

async def cmd_detect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Detect the language of the given text.
    Usage: /detect <text>
    """
    if not context.args:
        await update.message.reply_text(
            "Usage: `/detect <text>`\n\n"
            "Example: `/detect Bonjour, comment ça va?`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    
    text = " ".join(context.args)
    response = translator.format_detected_language(text)
    await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)


# ── /trmulti ──────────────────────────────────────────────────────────────────

async def cmd_translate_multi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Translate text to multiple languages at once.
    Usage: /trmulti <lang1,lang2,lang3> <text>
    
    Example: /trmulti th,ru,zh Hello world
    """
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "🌍 *Multi-Translation*\n\n"
            "Usage: `/trmulti <lang1,lang2,...> <text>`\n\n"
            "Example:\n"
            "  `/trmulti th,ru,zh Hello world`\n"
            "  `/trmulti en,ja,ko สวัสดี`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    
    langs_str = context.args[0]
    text_to_translate = " ".join(context.args[1:])
    
    # Parse target languages
    target_langs = [lang.strip() for lang in langs_str.split(",")]
    
    # Validate all languages
    valid_langs = []
    invalid_langs = []
    for lang in target_langs:
        code = translator.resolve_language_code(lang)
        if code:
            valid_langs.append(code)
        else:
            invalid_langs.append(lang)
    
    if invalid_langs:
        await update.message.reply_text(
            f"❌ Unknown language(s): {', '.join(invalid_langs)}\n"
            f"Use /langs to see supported languages.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    
    if not valid_langs:
        await update.message.reply_text("❌ No valid languages specified.")
        return
    
    # Translate to all languages
    results = translator.translate_multi(text_to_translate, valid_langs)
    
    # Format response
    source_lang = None
    lines = ["🌍 *Multi-Translation*\n"]
    
    for lang_code, result in results.items():
        if source_lang is None and result.get("source_lang"):
            source_lang = result["source_lang"]
        
        flag = translator.LANGUAGE_FLAGS.get(lang_code, "🌐")
        name = translator.SUPPORTED_LANGUAGES.get(lang_code, lang_code)
        
        if result.get("success"):
            lines.append(f"{flag} *{name}*: {result['translated_text']}")
        else:
            lines.append(f"{flag} *{name}*: ❌ {result.get('error', 'Failed')}")
    
    if source_lang:
        src_flag = translator.LANGUAGE_FLAGS.get(source_lang, "🌐")
        src_name = translator.SUPPORTED_LANGUAGES.get(source_lang, source_lang)
        lines.insert(1, f"_Source: {src_flag} {src_name}_\n")
    
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ── Quick translation shortcuts ───────────────────────────────────────────────

async def _quick_translate(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    from_lang: str,
    to_lang: str,
) -> None:
    """Helper for quick translation commands."""
    # Get text from args or reply
    text = " ".join(context.args) if context.args else ""
    if not text and update.message.reply_to_message:
        text = update.message.reply_to_message.text or ""
    
    if not text:
        from_flag = translator.LANGUAGE_FLAGS.get(from_lang, "")
        to_flag = translator.LANGUAGE_FLAGS.get(to_lang, "")
        await update.message.reply_text(
            f"{from_flag}→{to_flag} Usage: `/{from_lang}{to_lang} <text>`\n"
            f"Or reply to a message with `/{from_lang}{to_lang}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    
    result = translator.translate(text, to_lang, source_lang=from_lang)
    
    from_flag = translator.LANGUAGE_FLAGS.get(from_lang, "🌐")
    to_flag = translator.LANGUAGE_FLAGS.get(to_lang, "🌐")
    
    if result.get("success"):
        response = f"{from_flag} _{text[:200]}_\n\n{to_flag} {result['translated_text']}"
    else:
        response = f"❌ Translation failed: {result.get('error', 'Unknown error')}"
    
    await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)


async def cmd_ruth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Russian to Thai translation."""
    await _quick_translate(update, context, "ru", "th")


async def cmd_thru(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Thai to Russian translation."""
    await _quick_translate(update, context, "th", "ru")


async def cmd_ruen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Russian to English translation."""
    await _quick_translate(update, context, "ru", "en")


async def cmd_enru(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """English to Russian translation."""
    await _quick_translate(update, context, "en", "ru")


async def cmd_then(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Thai to English translation."""
    await _quick_translate(update, context, "th", "en")


async def cmd_enth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """English to Thai translation."""
    await _quick_translate(update, context, "en", "th")


# ── /brief ────────────────────────────────────────────────────────────────────

async def cmd_brief(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick key points summary."""
    conn = get_conn(context)
    user_id = update.effective_user.id
    
    # Detect user language
    text = update.message.text or ""
    lang = translator.detect_language(text) if text else "en"
    
    brief = digest_module.generate_brief(conn, user_id, language=lang)
    await update.message.reply_text(brief, parse_mode=ParseMode.MARKDOWN)


# ── /digest ───────────────────────────────────────────────────────────────────

async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate daily digest."""
    conn = get_conn(context)
    user_id = update.effective_user.id
    
    await update.message.reply_text("🌅 Generating daily digest...")
    
    # Detect user language
    text = update.message.text or ""
    lang = translator.detect_language(text) if text else "en"
    
    digest_text = digest_module.generate_digest(conn, user_id, language=lang)
    await update.message.reply_text(digest_text, parse_mode=ParseMode.MARKDOWN)


# ── /projects ─────────────────────────────────────────────────────────────────

async def cmd_projects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all projects."""
    conn = get_conn(context)
    user_id = update.effective_user.id
    
    projects_module.ensure_projects_table(conn)
    projects = projects_module.get_projects(conn, user_id)
    text = projects_module.format_projects_list(projects)
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ── /analyze ──────────────────────────────────────────────────────────────────

async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Deep project analysis."""
    conn = get_conn(context)
    user_id = update.effective_user.id
    
    args = " ".join(context.args) if context.args else ""
    
    if not args:
        await update.message.reply_text(
            "🚀 *Project Analysis*\n\n"
            "Usage: `/analyze <project name or idea>`\n\n"
            "Examples:\n"
            "  `/analyze Villa rental business`\n"
            "  `/analyze Property management app`\n"
            "  `/analyze Food delivery service`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    
    await update.message.reply_text(f"🔍 Analyzing: _{args}_...", parse_mode=ParseMode.MARKDOWN)
    
    # Get recent messages for context
    messages = storage.get_messages(conn, user_id, limit=20)
    conversation_context = "\n".join([m["content"][:100] for m in messages])
    
    # Detect language
    lang = projects_module.detect_language(args)
    
    # Check for existing project
    existing = projects_module.find_project_by_title(conn, user_id, args)
    existing_notes = existing.notes if existing else ""
    
    # Run analysis
    analysis = projects_module.analyze_project(
        title=args,
        existing_notes=existing_notes,
        conversation_context=conversation_context,
        language=lang,
    )
    
    if not analysis:
        await update.message.reply_text(
            "❌ Analysis unavailable. Please set OPENAI_API_KEY for AI features."
        )
        return
    
    # Extract score
    score = projects_module.extract_score_from_analysis(analysis)
    
    # Save or update project
    if existing:
        project = projects_module.update_project(
            conn, existing.id,
            analysis=analysis,
            summary=analysis[:150] + "...",
            score=score,
        )
    else:
        project = projects_module.create_project(
            conn, user_id,
            title=args,
            summary=analysis[:150] + "...",
            analysis=analysis,
            score=score,
        )
    
    response = projects_module.format_project_analysis(project, analysis)
    await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)


# ── /scan ─────────────────────────────────────────────────────────────────────

async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scan conversation for actionable items."""
    conn = get_conn(context)
    user_id = update.effective_user.id
    
    # Get recent messages
    messages = storage.get_messages(conn, user_id, limit=30)
    
    if len(messages) < 3:
        await update.message.reply_text("📝 Not enough conversation to scan yet.")
        return
    
    await update.message.reply_text("🔍 Scanning for actionable items...")
    
    # Extract items
    items = autoscan.extract_from_messages(messages)
    
    # Detect language
    text = " ".join([m.get("content", "")[:50] for m in messages[:5]])
    lang = translator.detect_language(text)
    
    if not items:
        result = autoscan.format_scan_result([], language=lang)
        await update.message.reply_text(result, parse_mode=ParseMode.MARKDOWN)
        return
    
    # Save pending items
    autoscan.save_pending_items(conn, user_id, items)
    
    result = autoscan.format_scan_result(items, language=lang)
    await update.message.reply_text(result, parse_mode=ParseMode.MARKDOWN)


# ── /save ─────────────────────────────────────────────────────────────────────

async def cmd_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save pending items from scan."""
    conn = get_conn(context)
    user_id = update.effective_user.id
    
    items = autoscan.get_pending_items(conn, user_id)
    
    if not items:
        await update.message.reply_text("Nothing pending. Run /scan first.")
        return
    
    # Save items to appropriate stores
    saved_count = 0
    for item in items:
        try:
            if item.type == "task":
                task_module.add_task(
                    conn, user_id,
                    title=item.title,
                    category="general",
                    priority=2,
                )
                saved_count += 1
            elif item.type == "reminder":
                reminder_module.schedule_reminder(
                    conn, user_id,
                    message=item.title,
                    time_expression=item.when or "tomorrow",
                )
                saved_count += 1
            elif item.type == "appointment":
                # Try to parse datetime
                dt_str = processor.extract_datetime(item.when) if item.when else None
                if dt_str:
                    from datetime import datetime
                    try:
                        start_dt = datetime.fromisoformat(dt_str)
                        calendar_integration.add_event(
                            conn, user_id,
                            title=item.title,
                            start_time=start_dt,
                        )
                        saved_count += 1
                    except Exception:
                        pass
            elif item.type == "project":
                projects_module.ensure_projects_table(conn)
                projects_module.create_project(
                    conn, user_id,
                    title=item.title,
                    notes=item.detail,
                )
                saved_count += 1
            else:
                # Default: save as task
                task_module.add_task(conn, user_id, title=item.title)
                saved_count += 1
        except Exception as exc:
            logger.warning("Failed to save item: %s", exc)
    
    # Clear pending
    autoscan.clear_pending_items(conn, user_id)
    
    result = autoscan.format_saved_items(items[:saved_count])
    await update.message.reply_text(result, parse_mode=ParseMode.MARKDOWN)


# ── /skip ─────────────────────────────────────────────────────────────────────

async def cmd_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Skip pending items from scan."""
    conn = get_conn(context)
    user_id = update.effective_user.id
    
    autoscan.clear_pending_items(conn, user_id)
    await update.message.reply_text("👍 Skipped pending items.")


# ── Free-form message handler ─────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Auto-process any free-form message:
      1. Save to conversation history
      2. Extract URLs and summarize
      3. Detect intent
      4. Extract and save tasks
      5. Detect and save reminders / calendar events
      6. Auto-scan every N messages
      7. Reply with a structured confirmation
    """
    conn = get_conn(context)
    user_id = update.effective_user.id
    text = update.message.text

    # 1. Persist
    ctx_module.record_user_message(conn, user_id, text)

    reply_parts: list[str] = []

    # 2. Extract and summarize URLs
    urls = links_module.extract_urls(text)
    for url in urls[:2]:  # Limit to 2 URLs per message
        try:
            sender_name = update.effective_user.first_name or "User"
            lang = translator.detect_language(text)
            result = await links_module.fetch_and_summarize(url, sender_name, lang)
            if result and result.get("summary"):
                reply_parts.append(result["summary"])
                
                # Auto-save if it's a listing
                if result.get("is_listing"):
                    task_module.add_task(
                        conn, user_id,
                        title=f"Check listing: {result.get('title', 'Property')[:50]}",
                        category="listing",
                    )
        except Exception as exc:
            logger.debug("Link summarization failed: %s", exc)

    # 3. Intent
    intent = processor.detect_intent(text)
    ctx_module.set_last_intent(user_id, intent)

    # 4. Task extraction
    new_tasks = task_module.extract_and_save_tasks(conn, user_id, text)
    if new_tasks:
        ctx_module.set_last_tasks(user_id, new_tasks)
        task_lines = "\n".join(f"  • {t['title']}" for t in new_tasks)
        reply_parts.append(f"📝 Extracted {len(new_tasks)} task(s):\n{task_lines}")

    # 5. Reminder detection
    if intent == "reminder":
        dt_str = processor.extract_datetime(text)
        if dt_str:
            reminder_id = reminder_module.schedule_reminder(
                conn, user_id, message=text, time_expression=text
            )
            if reminder_id:
                reply_parts.append(f"⏰ Reminder set for {dt_str}")

    # 6. Calendar event detection
    if intent == "calendar":
        dt_str = processor.extract_datetime(text)
        if dt_str:
            from datetime import datetime
            try:
                start_dt = datetime.fromisoformat(dt_str)
                event = calendar_integration.add_event(
                    conn, user_id, title=text[:60], start_time=start_dt
                )
                reply_parts.append(f"📅 Calendar event created: {event['title']}")
            except Exception as exc:
                logger.debug("Could not create calendar event: %s", exc)

    # 7. Auto-scan every N messages
    count, should_scan = autoscan.increment_message_count(user_id)
    if should_scan and not urls:  # Don't auto-scan if we just processed links
        messages = storage.get_messages(conn, user_id, limit=30)
        items = autoscan.extract_from_messages(messages)
        if items:
            autoscan.save_pending_items(conn, user_id, items)
            lang = translator.detect_language(text)
            preview = autoscan.format_extracted_items(items)
            reply_parts.append(f"🧠 *Found {len(items)} item(s):*\n{preview}\n\n/save · /skip")

    # Fallback reply
    if not reply_parts:
        reply_parts.append(
            "Got it! Send /tasks to see your task list or /help for all commands."
        )

    reply = "\n\n".join(reply_parts)
    ctx_module.record_assistant_reply(conn, user_id, reply)
    await update.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)


# ── Voice message handler ─────────────────────────────────────────────────────

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle voice messages:
      1. Download voice file
      2. Transcribe with Whisper
      3. Process as regular message
    """
    if not voice_module.is_available():
        await update.message.reply_text(
            "🎤 Voice transcription unavailable. Please set OPENAI_API_KEY."
        )
        return
    
    conn = get_conn(context)
    user_id = update.effective_user.id
    
    # Get voice file
    voice = update.message.voice
    if not voice:
        return
    
    await update.message.reply_text("🎤 Transcribing...")
    
    try:
        # Download voice file
        file_path = await voice_module.download_voice_file(
            context.bot, voice.file_id
        )
        
        if not file_path:
            await update.message.reply_text("❌ Could not download voice message.")
            return
        
        # Transcribe
        transcribed_text, detected_lang = voice_module.transcribe(file_path)
        
        if not transcribed_text:
            await update.message.reply_text("❌ Could not transcribe voice message.")
            return
        
        # Format and send transcription
        lang = detected_lang or voice_module.detect_language_from_text(transcribed_text)
        response = voice_module.format_transcription(transcribed_text, lang)
        await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)
        
        # Save to conversation history
        ctx_module.record_user_message(conn, user_id, f"🎤 {transcribed_text}")

        # Secretary pipeline: summarise, file to the log, the shared calendar
        # and the household ledger, then confirm exactly what was recorded.
        if getattr(config, "SECRETARY_AUTO", True):
            await _file_and_reply(
                update, context, transcribed_text,
                source="voice", source_ref=voice.file_id,
            )
        else:
            new_tasks = task_module.extract_and_save_tasks(conn, user_id, transcribed_text)
            if new_tasks:
                task_lines = "\n".join(f"  • {t['title']}" for t in new_tasks)
                await update.message.reply_text(
                    f"📝 Extracted {len(new_tasks)} task(s) from voice:\n{task_lines}",
                    parse_mode=ParseMode.MARKDOWN
                )
        
        # Check for listing pattern
        if links_module.looks_like_listing(transcribed_text):
            task_module.add_task(
                conn, user_id,
                title=f"Check listing from voice: {transcribed_text[:50]}...",
                category="listing",
            )
            await update.message.reply_text("🏠 Property listing detected and saved as task!")
            
    except Exception as exc:
        logger.error("Voice handling failed: %s", exc)
        await update.message.reply_text("❌ Voice processing failed.")


# ── Secretary: shared log, calendar and household finances ────────────────────

def _speaker_for(update: Update) -> str:
    """
    Attribute a message to a household member.

    When PARTNER_TELEGRAM_ID is configured, that user's messages are filed under
    PARTNER_NAME and everyone else's under OWNER_NAME.  Without it, everything is
    filed under the owner, which is still correct for a single-account setup.
    """
    partner_id = str(getattr(config, "PARTNER_TELEGRAM_ID", "") or "").strip()
    if partner_id and str(update.effective_user.id) == partner_id:
        return getattr(config, "PARTNER_NAME", "Farid")
    return getattr(config, "OWNER_NAME", "J")


async def _push_to_google(conn, receipt: dict) -> str:
    """
    Mirror a filed note to Google Calendar and the shared ledger sheet.

    Returns a one-line status suffix for the Telegram reply, or "" when sync is
    not configured (the normal case until credentials are added).
    """
    if not gsync.is_configured():
        return ""

    bits = []
    try:
        if receipt.get("events"):
            tally = gsync.push_events(conn, receipt["events"])
            if tally.get("created"):
                bits.append(f"{tally['created']} → 📅 shared calendar")
        if receipt.get("expenses"):
            result = gsync.push_expenses(conn, receipt["expenses"])
            if result.get("appended"):
                bits.append(f"{result['appended']} → 📊 shared sheet")
    except Exception as exc:
        logger.error("Google sync failed: %s", exc)
        return "\n\n_⚠️ Google sync failed — saved locally._"

    return ("\n\n_Synced: " + ", ".join(bits) + "_") if bits else ""


async def _file_and_reply(update, context, text: str, source: str,
                          source_ref: str = None, extraction: dict = None) -> None:
    """Run one note through the secretary pipeline and report what was filed."""
    conn = get_conn(context)
    user_id = update.effective_user.id
    speaker = _speaker_for(update)

    receipt = secretary.file_note(
        conn, user_id, text,
        speaker=speaker, source=source, source_ref=source_ref,
        extraction=extraction,
    )
    suffix = await _push_to_google(conn, receipt)
    await update.message.reply_text(
        secretary.format_intake(receipt, speaker) + suffix,
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/note <text> — file a typed note through the secretary pipeline."""
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text(
            "Usage: `/note Farid is paying rent on the 1st, 18000 baht`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    await update.message.reply_text("🗂 Filing…")
    await _file_and_reply(update, context, text, source="text")


async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/log [n] — show the most recent secretary log entries."""
    conn = get_conn(context)
    limit = 15
    if context.args:
        try:
            limit = max(1, min(int(context.args[0]), 50))
        except ValueError:
            pass
    entries = secretary.get_log(conn, limit=limit)
    await update.message.reply_text(
        secretary.format_log(entries, limit=limit), parse_mode=ParseMode.MARKDOWN
    )


async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/week — the week in review: notes, spending and outstanding balance."""
    conn = get_conn(context)
    await update.message.reply_text(
        secretary.weekly_digest(conn), parse_mode=ParseMode.MARKDOWN
    )


# ── Finance commands ──────────────────────────────────────────────────────────

def _parse_spend_args(args: list, default_payer: str) -> dict:
    """
    Parse ``/spend`` arguments.

    Grammar:
        ``<amount> [description...] [#category] [@payer] [!mine|!theirs|!fund]``

    ``!fund`` marks the purchase as coming out of the shared food fund rather
    than someone's pocket, so it draws the fund down instead of creating a debt.

    Example: ``/spend 1250 Makro run #food @Farid !fund``
    """
    if not args:
        raise ValueError("amount is required")

    amount = None
    category = None
    payer = default_payer
    split = "equal"
    paid_from = None
    words = []

    for token in args:
        if token.startswith("#"):
            category = token[1:].lower()
        elif token.startswith("@"):
            payer = token[1:]
        elif token.lower() in ("!fund", "!personal", "!own", "!mine-pocket"):
            paid_from = "fund" if token.lower() == "!fund" else "personal"
        elif token.startswith("!"):
            split = token[1:].lower()
        elif amount is None:
            cleaned = token.replace(",", "").replace("฿", "")
            multiplier = 1
            if cleaned.lower().endswith("k"):
                cleaned, multiplier = cleaned[:-1], 1000
            try:
                amount = float(cleaned) * multiplier
            except ValueError:
                words.append(token)
        else:
            words.append(token)

    if amount is None:
        raise ValueError("could not read an amount")

    # Food is fund money by default; everything else comes from a pocket.
    if paid_from is None:
        paid_from = "fund" if category == "food" else "personal"

    return {
        "amount": amount,
        "description": " ".join(words).strip(),
        "category": category,
        "payer": payer,
        "split": split,
        "paid_from": paid_from,
    }


async def cmd_spend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/spend <amount> [description] [#category] [@payer] [!mine|!theirs]"""
    conn = get_conn(context)
    try:
        parsed = _parse_spend_args(context.args, _speaker_for(update))
    except ValueError as exc:
        await update.message.reply_text(
            f"❌ {exc}\n\nUsage: `/spend 1250 Makro run #food @Farid`\n"
            "`#category` one of: " + ", ".join(finance_module.CATEGORIES) + "\n"
            "`!mine` you were only covering yourself · `!theirs` you fronted it all\n"
            "`!fund` out of the shared food fund · `!personal` out of pocket\n"
            "_Food defaults to the fund; everything else to personal._",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    expense = finance_module.add_expense(
        conn, update.effective_user.id,
        amount=parsed["amount"],
        description=parsed["description"],
        category=parsed["category"],
        payer=parsed["payer"],
        payer_share=parsed["split"],
        paid_from=parsed["paid_from"],
        currency=getattr(config, "DEFAULT_CURRENCY", "THB"),
        source="manual",
    )

    suffix = ""
    if gsync.is_configured():
        result = gsync.push_expenses(conn, [expense])
        if result.get("appended"):
            suffix = "\n\n_Synced → 📊 shared sheet_"

    emoji = finance_module.CATEGORY_EMOJI.get(expense["category"], "•")
    header = (
        f"{emoji} Logged *{finance_module.format_money(expense['amount'], expense['currency'])}* "
        f"— {expense['description'] or expense['category']}\n"
    )

    if expense["paid_from"] == "fund":
        # Fund spending changes the fund, not the balance between them.
        header += f"_From the food fund · {expense['category']} · `#{expense['id']}`_\n\n"
        body = finance_module.format_fund(
            finance_module.fund_status(conn, month=expense["period"])
        )
    else:
        header += f"_{expense['payer']} paid · {expense['category']} · `#{expense['id']}`_\n\n"
        body = finance_module.format_balance(finance_module.compute_balance(conn))

    await update.message.reply_text(header + body + suffix, parse_mode=ParseMode.MARKDOWN)


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/balance — who owes who right now."""
    conn = get_conn(context)
    await update.message.reply_text(
        finance_module.format_balance(finance_module.compute_balance(conn)),
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_month(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/month [YYYY-MM] — category rollup with month-over-month trend."""
    conn = get_conn(context)
    month = context.args[0] if context.args else None
    if month and not re.fullmatch(r"\d{4}-\d{2}", month):
        await update.message.reply_text("Usage: `/month 2026-08`", parse_mode=ParseMode.MARKDOWN)
        return
    rollup = finance_module.monthly_rollup(
        conn, month=month, currency=getattr(config, "DEFAULT_CURRENCY", "THB")
    )
    await update.message.reply_text(
        finance_module.format_rollup(rollup), parse_mode=ParseMode.MARKDOWN
    )


async def cmd_ledger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ledger [YYYY-MM] — the line-item ledger."""
    conn = get_conn(context)
    month = context.args[0] if context.args and re.fullmatch(r"\d{4}-\d{2}", context.args[0]) else None
    expenses = finance_module.list_expenses(conn, month=month, limit=60)
    await update.message.reply_text(
        finance_module.format_ledger(expenses), parse_mode=ParseMode.MARKDOWN
    )


async def cmd_settle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/settle <amount> [note] — record a repayment that clears the balance."""
    conn = get_conn(context)
    if not context.args:
        await update.message.reply_text(
            "Usage: `/settle 4500 transferred via SCB`", parse_mode=ParseMode.MARKDOWN
        )
        return
    try:
        amount = float(context.args[0].replace(",", "").replace("฿", ""))
    except ValueError:
        await update.message.reply_text("❌ Could not read that amount.")
        return

    payer = _speaker_for(update)
    settlement = finance_module.add_settlement(
        conn, update.effective_user.id,
        amount=amount, payer=payer,
        currency=getattr(config, "DEFAULT_CURRENCY", "THB"),
        note=" ".join(context.args[1:]),
    )
    balances = finance_module.compute_balance(conn)
    await update.message.reply_text(
        f"🤝 Recorded *{finance_module.format_money(settlement['amount'], settlement['currency'])}* "
        f"from {settlement['payer']} to {settlement['payee']}.\n\n"
        + finance_module.format_balance(balances),
        parse_mode=ParseMode.MARKDOWN,
    )


# ── Shared food fund ──────────────────────────────────────────────────────────

async def cmd_fund(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/fund [YYYY-MM] — the shared food fund: paid in, spent, what is left."""
    conn = get_conn(context)
    month = context.args[0] if context.args else None
    if month and not re.fullmatch(r"\d{4}-\d{2}", month):
        await update.message.reply_text("Usage: `/fund 2026-08`", parse_mode=ParseMode.MARKDOWN)
        return
    status = finance_module.fund_status(
        conn, month=month, currency=getattr(config, "DEFAULT_CURRENCY", "THB")
    )
    await update.message.reply_text(
        finance_module.format_fund(status), parse_mode=ParseMode.MARKDOWN
    )


async def cmd_topup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/topup <amount> [@member] [YYYY-MM] — pay into the shared food fund."""
    conn = get_conn(context)
    if not context.args:
        await update.message.reply_text(
            "Usage: `/topup 4000` · `/topup 440 @Farid` · `/topup 4000 2026-09`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    member = _speaker_for(update)
    month = None
    amount = None
    for token in context.args:
        if token.startswith("@"):
            member = token[1:]
        elif re.fullmatch(r"\d{4}-\d{2}", token):
            month = token
        elif amount is None:
            cleaned = token.replace(",", "").replace("฿", "")
            multiplier = 1000 if cleaned.lower().endswith("k") else 1
            if multiplier == 1000:
                cleaned = cleaned[:-1]
            try:
                amount = float(cleaned) * multiplier
            except ValueError:
                pass

    if amount is None:
        await update.message.reply_text("❌ Could not read that amount.")
        return

    month = month or finance_module.month_key()
    # An amount added to a month that is already overdrawn is a top-up, not the
    # regular monthly contribution — label it so the report reads honestly.
    before = finance_module.fund_status(conn, month=month)
    kind = "topup" if before["overdrawn"] else "contribution"

    finance_module.add_contribution(
        conn, update.effective_user.id, amount=amount, member=member, month=month,
        currency=getattr(config, "DEFAULT_CURRENCY", "THB"), kind=kind,
    )
    await update.message.reply_text(
        finance_module.format_fund(finance_module.fund_status(conn, month=month)),
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_closemonth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/closemonth [YYYY-MM] — settle the month across the fund and personal spending."""
    conn = get_conn(context)
    month = context.args[0] if context.args else None
    if month and not re.fullmatch(r"\d{4}-\d{2}", month):
        await update.message.reply_text(
            "Usage: `/closemonth 2026-08`", parse_mode=ParseMode.MARKDOWN
        )
        return
    settlement = finance_module.month_settlement(
        conn, month=month, currency=getattr(config, "DEFAULT_CURRENCY", "THB")
    )
    await update.message.reply_text(
        finance_module.format_settlement(settlement), parse_mode=ParseMode.MARKDOWN
    )


# ── Google sync commands ──────────────────────────────────────────────────────

async def cmd_gsync(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/gsync — show which Google targets are wired up."""
    await update.message.reply_text(gsync.format_status(), parse_mode=ParseMode.MARKDOWN)


async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/sync — push every not-yet-synced ledger row to the shared sheet."""
    conn = get_conn(context)
    if not gsync.is_configured():
        await update.message.reply_text(
            "⚪️ Google sync is not configured yet.\n"
            "Set `GOOGLE_SERVICE_ACCOUNT_JSON` (or the OAuth trio) plus "
            "`JF_CALENDAR_ID` and `LEDGER_SHEET_ID`, then try again.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    await update.message.reply_text("🔄 Syncing…")
    result = gsync.sync_ledger(conn)
    if result["status"] == "appended":
        await update.message.reply_text(f"✅ Pushed {result['appended']} ledger row(s).")
    elif result["status"] == "duplicate":
        await update.message.reply_text("✅ Everything is already in sync.")
    else:
        await update.message.reply_text(f"⚠️ {result.get('reason', 'Sync skipped.')}")


# ── Receipt photo handler ─────────────────────────────────────────────────────

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Read a photographed receipt and file it as a ledger entry."""
    if not update.message.photo:
        return

    if not receipt_module.is_available():
        await update.message.reply_text(
            "🧾 Receipt reading needs `OPENAI_API_KEY`.\n"
            "You can still log it manually: `/spend 450 lunch #food`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await update.message.reply_text("🧾 Reading receipt…")

    # Telegram sends progressively larger sizes; the last one is the largest.
    photo = update.message.photo[-1]
    path = await receipt_module.download_photo(context.bot, photo.file_id)
    if path is None:
        await update.message.reply_text("❌ Could not download that photo.")
        return

    parsed = receipt_module.parse_receipt(path)
    if parsed is None:
        await update.message.reply_text(
            "❌ Couldn't read a total off that receipt.\n"
            "Log it manually: `/spend 450 lunch #food`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await update.message.reply_text(
        receipt_module.format_receipt(parsed), parse_mode=ParseMode.MARKDOWN
    )

    speaker = _speaker_for(update)
    caption = (update.message.caption or "").strip()
    extraction = {
        "summary": f"Receipt from {parsed.get('merchant') or parsed['category']} "
                   f"for {finance_module.format_money(parsed['total'], parsed['currency'])}"
                   + (f". {caption}" if caption else ""),
        "language": "en",
        "events": [],
        "expenses": [receipt_module.to_expense_payload(parsed, payer=speaker)],
        "commitments": [],
        "notes": [caption] if caption else [],
        "questions": [],
    }
    await _file_and_reply(
        update, context, caption or parsed.get("merchant", "receipt"),
        source="receipt", source_ref=photo.file_id, extraction=extraction,
    )


# ── Bot bootstrap ─────────────────────────────────────────────────────────────

def build_app() -> Application:
    """Build and configure the Telegram Application."""
    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .build()
    )

    # Basic commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    
    # Task management
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("addtask", cmd_addtask))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CommandHandler("exporttasks", cmd_exporttasks))
    
    # Smart extraction
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("save", cmd_save))
    app.add_handler(CommandHandler("skip", cmd_skip))
    
    # Projects
    app.add_handler(CommandHandler("projects", cmd_projects))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    
    # Reminders
    app.add_handler(CommandHandler("remind", cmd_remind))
    app.add_handler(CommandHandler("reminders", cmd_reminders))
    
    # Calendar
    app.add_handler(CommandHandler("calendar", cmd_calendar))
    app.add_handler(CommandHandler("addevent", cmd_addevent))
    app.add_handler(CommandHandler("exportcal", cmd_exportcal))
    
    # Analytics & summaries
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("brief", cmd_brief))
    app.add_handler(CommandHandler("digest", cmd_digest))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("insights", cmd_insights))
    app.add_handler(CommandHandler("weekly", cmd_weekly))
    
    # Translation commands
    app.add_handler(CommandHandler("translate", cmd_translate))
    app.add_handler(CommandHandler("tr", cmd_translate))
    app.add_handler(CommandHandler("langs", cmd_langs))
    app.add_handler(CommandHandler("detect", cmd_detect))
    app.add_handler(CommandHandler("trmulti", cmd_translate_multi))
    
    # Quick translation shortcuts
    app.add_handler(CommandHandler("ruth", cmd_ruth))
    app.add_handler(CommandHandler("thru", cmd_thru))
    app.add_handler(CommandHandler("ruen", cmd_ruen))
    app.add_handler(CommandHandler("enru", cmd_enru))
    app.add_handler(CommandHandler("then", cmd_then))
    app.add_handler(CommandHandler("enth", cmd_enth))

    # Secretary — shared log, calendar and household finances
    app.add_handler(CommandHandler("note", cmd_note))
    app.add_handler(CommandHandler("log", cmd_log))
    app.add_handler(CommandHandler("week", cmd_week))

    # Household finances
    app.add_handler(CommandHandler("spend", cmd_spend))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("month", cmd_month))
    app.add_handler(CommandHandler("ledger", cmd_ledger))
    app.add_handler(CommandHandler("settle", cmd_settle))

    # Shared food fund
    app.add_handler(CommandHandler("fund", cmd_fund))
    app.add_handler(CommandHandler("topup", cmd_topup))
    app.add_handler(CommandHandler("closemonth", cmd_closemonth))

    # Google sync
    app.add_handler(CommandHandler("gsync", cmd_gsync))
    app.add_handler(CommandHandler("sync", cmd_sync))

    # Message handlers
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    return app


def main() -> None:
    # Start health check server for Railway/Render hosting
    try:
        import keep_alive
        keep_alive.start()
        logger.info("Health check server started")
    except Exception:
        pass

    if not config.TELEGRAM_BOT_TOKEN:
        logger.error(
            "TELEGRAM_BOT_TOKEN is not set. "
            "Copy .env.example to .env and fill in your credentials."
        )
        sys.exit(1)

    app = build_app()

    # Wire up reminder service
    conn = storage.init_db()
    app.bot_data["db_conn"] = conn

    async def send_reminder(user_id: int, message: str) -> None:
        await app.bot.send_message(chat_id=user_id, text=message)

    reminder_svc = reminder_module.ReminderService(conn, send_reminder)

    async def on_startup(application: Application) -> None:
        reminder_svc.start()

    async def on_shutdown(application: Application) -> None:
        reminder_svc.stop()

    app.post_init = on_startup
    app.post_shutdown = on_shutdown

    logger.info("Starting AI Personal Assistant Bot …")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
