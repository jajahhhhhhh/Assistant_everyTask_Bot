"""
AI Personal Assistant — Telegram bot entry point.

Commands:
    /start          — Welcome message
    /help           — Command reference
    /tasks          — List all your tasks
    /addtask <text> — Create a task manually
    /done <id>      — Mark a task as done
    /summary        — Summarise recent conversation
    /remind <time> <message> — Set a reminder
    /reminders      — List upcoming reminders
    /calendar       — List upcoming calendar events
    /addevent <title> at <datetime> — Add a calendar event
    /exporttasks    — Export tasks as PDF
    /exportcal      — Export calendar as .ics file
    /status         — Progress report
    /insights       — Recommendations
    /weekly         — Weekly summary

Any free-form message is processed by the AI:
  • Tasks are extracted and stored automatically.
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
    calendar_integration,
    context as ctx_module,
    files as file_module,
    processor,
    reminders as reminder_module,
    storage,
    tasks as task_module,
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
        "I can help you:\n"
        "• 📝 Track and manage tasks\n"
        "• 📅 Schedule calendar events\n"
        "• ⏰ Set reminders\n"
        "• 📊 Summarise conversations\n"
        "• 💡 Provide insights and recommendations\n\n"
        "Just send me a message and I'll extract tasks and actions automatically, "
        "or use /help for a full command list."
    )
    await update.message.reply_text(text)


# ── /help ─────────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "📖 *Command Reference*\n\n"
        "*Task Management*\n"
        "/tasks — List all tasks\n"
        "/addtask `<description>` — Add a task\n"
        "/done `<id>` — Mark task as done\n"
        "/exporttasks — Export tasks as PDF\n\n"
        "*Reminders*\n"
        "/remind `<time> | <message>` — Set a reminder\n"
        "  e.g. `/remind tomorrow 9am | Call Alice`\n"
        "/reminders — List upcoming reminders\n\n"
        "*Calendar*\n"
        "/calendar — Upcoming events (7 days)\n"
        "/addevent `<title> at <datetime>` — Add an event\n"
        "/exportcal — Export calendar as .ics\n\n"
        "*Insights*\n"
        "/summary — Summarise recent conversation\n"
        "/status — Progress report\n"
        "/weekly — Weekly summary\n"
        "/insights — Recommendations\n\n"
        "💬 *Free-form message* — I'll extract tasks and actions automatically!"
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


# ── Free-form message handler ─────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Auto-process any free-form message:
      1. Save to conversation history
      2. Detect intent
      3. Extract and save tasks
      4. Detect and save reminders / calendar events
      5. Reply with a structured confirmation
    """
    conn = get_conn(context)
    user_id = update.effective_user.id
    text = update.message.text

    # 1. Persist
    ctx_module.record_user_message(conn, user_id, text)

    # 2. Intent
    intent = processor.detect_intent(text)
    ctx_module.set_last_intent(user_id, intent)

    reply_parts: list[str] = []

    # 3. Task extraction
    new_tasks = task_module.extract_and_save_tasks(conn, user_id, text)
    if new_tasks:
        ctx_module.set_last_tasks(user_id, new_tasks)
        task_lines = "\n".join(f"  • {t['title']}" for t in new_tasks)
        reply_parts.append(f"📝 Extracted {len(new_tasks)} task(s):\n{task_lines}")

    # 4. Reminder detection
    if intent == "reminder":
        dt_str = processor.extract_datetime(text)
        if dt_str:
            reminder_id = reminder_module.schedule_reminder(
                conn, user_id, message=text, time_expression=text
            )
            if reminder_id:
                reply_parts.append(f"⏰ Reminder set for {dt_str}")

    # 5. Calendar event detection
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

    # Fallback reply
    if not reply_parts:
        reply_parts.append(
            "Got it! Send /tasks to see your task list or /help for all commands."
        )

    reply = "\n\n".join(reply_parts)
    ctx_module.record_assistant_reply(conn, user_id, reply)
    await update.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)


# ── Bot bootstrap ─────────────────────────────────────────────────────────────

def build_app() -> Application:
    """Build and configure the Telegram Application."""
    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("addtask", cmd_addtask))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("remind", cmd_remind))
    app.add_handler(CommandHandler("reminders", cmd_reminders))
    app.add_handler(CommandHandler("calendar", cmd_calendar))
    app.add_handler(CommandHandler("addevent", cmd_addevent))
    app.add_handler(CommandHandler("exporttasks", cmd_exporttasks))
    app.add_handler(CommandHandler("exportcal", cmd_exportcal))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("insights", cmd_insights))
    app.add_handler(CommandHandler("weekly", cmd_weekly))

    # Free-form messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

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
