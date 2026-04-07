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
    /translate <lang> <text> — Translate text to target language
    /tr <lang> <text>        — Shorthand for /translate
    /trmulti <langs> <text>  — Translate to multiple languages
    /ruth, /thru, /ruen, /enru, /then, /enth — Quick translation shortcuts
    /detect <text>           — Detect language of text
    /langs                   — List supported languages

Any free-form message is processed by the AI:
  • Tasks are extracted and stored automatically.
  • Links are summarized.
  • Voice messages are transcribed.
  • Reminders and events are created if detected.
  • A confirmation is sent back to the user.
"""

import asyncio
import logging
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
    processor,
    projects as projects_module,
    reminders as reminder_module,
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
        "• Send a voice note — I'll transcribe it\n"
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
        
        # Process as regular text for task extraction etc.
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

    # Message handlers
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
