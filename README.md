# Assistant_everyTask_Bot

An **AI-powered personal assistant** Telegram bot that autonomously manages information, tasks, and workflows — acting as a persistent, context-aware digital secretary.

---

## Features (MVP)

| Feature | Description |
|---------|-------------|
| 🤖 **Auto-processing** | Every message is analysed — tasks are extracted and saved automatically |
| 📝 **Task Management** | Create, categorise, prioritise, and track tasks |
| ⏰ **Reminders** | Natural-language reminder scheduling ("in 30 minutes", "tomorrow at 9am") |
| 📅 **Calendar Integration** | Add events; export as `.ics` (compatible with Google Calendar, iCal, Outlook) |
| 🚀 **Project Analysis** | Deep AI-powered project analysis with scores, revenue estimates, and recommendations |
| 🔍 **Smart Extraction** | Auto-scan conversations every 10 messages for actionable items |
| 🔗 **Link Summarization** | Auto-fetch and summarize shared URLs with multilingual output |
| 🎤 **Voice Transcription** | Transcribe voice messages using OpenAI Whisper |
| 🗂 **Secretary** | Every voice note is transcribed, summarised, and filed: events → calendar, money → ledger, promises → tasks |
| 💰 **Household Ledger** | Two-track shared money: a common food fund plus split personal spending, with a running who-owes-who balance and monthly trends |
| 🍜 **Food Fund** | Monthly contributions, drawdown, overdraft and equal top-ups — modelled the way the household actually settles |
| 🧾 **Receipt Capture** | Photograph a receipt and it is read into the ledger — merchant, total, currency, date, category |
| 🔗 **Google Sync** | Push events to a shared Google Calendar and ledger rows to a shared Google Sheet |
| 📋 **Smart Summaries** | Summarise conversations (short / detailed / executive) |
| 🌅 **Daily Digest** | AI-generated morning digest with tasks, appointments, and recommendations |
| 📊 **Progress Reports** | Completion rates, overdue items, weekly summaries |
| 💡 **Recommendations** | Rule-based insights on workload and priorities |
| 📄 **File Export** | Export tasks as PDF; export calendar as `.ics` |
| 🌐 **Translation** | Translate text between 20+ languages with auto-detection |
| 🧠 **OpenAI Integration** | GPT-powered intent detection, task extraction, translation, voice transcription, and summaries |

---

## Project Structure

```
Assistant_everyTask_Bot/
├── bot.py                        # Telegram bot entry point
├── config.py                     # Environment / configuration
├── requirements.txt
├── .env.example                  # Copy to .env and fill in your keys
├── assistant/
│   ├── storage.py                # SQLite persistence (messages, tasks, reminders, events)
│   ├── processor.py              # NLP: intent detection, summarisation, task extraction
│   ├── tasks.py                  # Task management helpers
│   ├── reminders.py              # APScheduler-based reminder service
│   ├── calendar_integration.py   # Calendar events + iCal export
│   ├── files.py                  # PDF / text file generation
│   ├── analytics.py              # Progress reports and recommendations
│   ├── context.py                # Persistent conversational memory
│   ├── translator.py             # Multi-language translation (20+ languages)
│   ├── voice.py                  # Voice transcription (Whisper)
│   ├── links.py                  # Link fetching and summarization
│   ├── projects.py               # Project management and AI analysis
│   ├── digest.py                 # Daily digest generation
│   ├── autoscan.py               # Auto-extraction of actionable items
│   ├── secretary.py              # Note → summary + events + expenses + to-dos
│   ├── finance.py                # Shared household ledger, balance, rollups
│   ├── receipts.py               # Receipt photo → ledger entry (vision)
│   └── gsync.py                  # Google Calendar / Sheets / Drive sync
└── tests/
    ├── test_storage.py
    ├── test_processor.py
    ├── test_tasks.py
    ├── test_reminders.py
    ├── test_analytics.py
    ├── test_calendar.py
    └── test_translator.py
```

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/jajahhhhhhh/Assistant_everyTask_Bot.git
cd Assistant_everyTask_Bot
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env and set:
#   TELEGRAM_BOT_TOKEN  — get from @BotFather on Telegram
#   OPENAI_API_KEY      — optional, enables GPT-powered processing, voice transcription
```

### 3. Run

```bash
python bot.py
```

---

## Bot Commands

### Task Management
| Command | Description |
|---------|-------------|
| `/tasks` | List all your tasks |
| `/addtask <description>` | Add a task manually |
| `/done <id>` | Mark a task as done |
| `/exporttasks` | Export tasks as a PDF file |

### Smart Extraction 🔍
| Command | Description |
|---------|-------------|
| `/scan` | Scan conversation for actionable items |
| `/save` | Save pending extracted items |
| `/skip` | Skip/discard pending items |

*Auto-scan runs every 10 messages automatically!*

### Projects 🚀
| Command | Description |
|---------|-------------|
| `/projects` | List all projects |
| `/analyze <project>` | Deep AI analysis with scores & recommendations |

**Example:** `/analyze Villa rental business on Koh Samui`

### Reminders
| Command | Description |
|---------|-------------|
| `/remind <time> \| <message>` | Set a reminder |
| `/reminders` | List upcoming reminders |

**Example:** `/remind tomorrow at 9am | Call Alice about the project`

### Calendar
| Command | Description |
|---------|-------------|
| `/calendar` | Show upcoming events (next 7 days) |
| `/addevent <title> at <datetime>` | Add a calendar event |
| `/exportcal` | Export calendar as `.ics` file |

**Example:** `/addevent Team standup at tomorrow 10am`

### Summaries & Insights
| Command | Description |
|---------|-------------|
| `/summary [short\|detailed\|executive]` | Summarise recent conversation |
| `/brief` | Quick 3-5 bullet point summary |
| `/digest` | Generate AI-powered daily digest |
| `/status` | Task progress report |
| `/weekly` | Weekly activity summary |
| `/insights` | Recommendations based on your task list |

### Translation 🌐
| Command | Description |
|---------|-------------|
| `/translate <lang> <text>` | Translate text to target language |
| `/tr <lang> <text>` | Shorthand for translate |
| `/trmulti <lang1,lang2> <text>` | Translate to multiple languages |
| `/detect <text>` | Detect language of text |
| `/langs` | List all supported languages |

**Quick Translation Shortcuts:**
| Command | Description |
|---------|-------------|
| `/ruth <text>` | Russian → Thai |
| `/thru <text>` | Thai → Russian |
| `/ruen <text>` | Russian → English |
| `/enru <text>` | English → Russian |
| `/then <text>` | Thai → English |
| `/enth <text>` | English → Thai |

**Supported Languages:** English, Thai, Russian, Chinese, Japanese, Korean, Spanish, French, German, Italian, Portuguese, Vietnamese, Arabic, Hindi, Indonesian, Malay, Ukrainian, Dutch, Polish, Turkish

**Examples:**
- `/tr th Hello, how are you?` → Translates to Thai
- `/ruth Доброе утро` → Russian to Thai
- `/trmulti th,ru,zh Hello world` → Multi-translate
- `/detect Bonjour` → Detects French

### Other
| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/help` | Full command reference |

### Auto-Features 🤖

**Free-form messages:** Send any message and the assistant will automatically:
- Detect your intent (task, reminder, calendar, summary…)
- Extract and save actionable tasks
- Schedule reminders or create calendar events if detected
- Confirm what was captured

**Link Summarization:** Share any URL and the bot will:
- Fetch and parse the page
- Generate multilingual summaries (TH/RU/EN)
- Auto-detect property listings
- Save listings as tasks

**Voice Transcription:** Send a voice message and the bot will:
- Transcribe using OpenAI Whisper
- Auto-detect language
- Extract tasks from transcribed text
- Detect property listings

**Auto-Scan:** Every 10 messages, the bot will:
- Scan conversation for actionable items
- Present found items for review
- Let you `/save` or `/skip` them

---

## System Architecture

```
Input Layer        →  Telegram (multi-channel ready)
Processing Layer   →  NLP processor (OpenAI GPT / keyword fallback)
                       Intent detection · Summarisation · Task extraction
Memory Layer       →  SQLite (messages, tasks, reminders, events)
                       Short-term cache + long-term structured KB
Integration Layer  →  iCal export · APScheduler reminders · PDF generation
Output Layer       →  Structured Telegram replies · PDF · .ics files
```

---

## Running Tests

```bash
pip install pytest
python -m pytest tests/ -v
```

All tests run without an OpenAI API key using the keyword-based fallback.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | — | **Required.** From @BotFather |
| `OPENAI_API_KEY` | — | Optional. Enables GPT features |
| `OPENAI_MODEL` | `gpt-3.5-turbo` | OpenAI model to use |
| `DATABASE_PATH` | `data/assistant.db` | SQLite database file path |
| `EXPORTS_DIR` | `exports` | Directory for PDF / .ics exports |
| `TIMEZONE` | `UTC` | Timezone for reminders / events |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

---

## Future Enhancements

- Predictive task creation
- Voice interaction
- Multi-user collaboration
- Advanced analytics dashboard
- AI-driven business insights
- Google Calendar / Outlook sync

---

## 🗂 Secretary mode

The secretary layer turns a raw note into a filed record. One pipeline, four
outputs, whether the note arrived as a voice message, a typed line or a photo of
a receipt:

```
voice note / message / receipt photo
        │
        ├─► Whisper transcript (th / en / ru)
        ├─► structured extraction  {summary, events, expenses, commitments, notes, questions}
        │
        ├─► 🗂 secretary log      — the searchable record of what was said
        ├─► 📅 calendar events    — local, plus the shared Google calendar
        ├─► 💸 ledger rows        — local, plus the shared Google sheet
        └─► ✅ tasks              — with owner, due date and priority
```

You get one reply confirming exactly what was filed, so nothing lands silently.

### Secretary commands

| Command | Description |
|---------|-------------|
| `/note <text>` | File a typed note through the pipeline |
| `/log [n]` | Recent log entries, grouped by day |
| `/week` | Week in review: notes, spending and outstanding balance |

Voice notes and receipt photos need no command — they are filed on arrival.
Set `SECRETARY_AUTO=false` to fall back to plain transcription.

### Household finance commands

| Command | Description |
|---------|-------------|
| `/spend <amount> <what>` | Log an expense. Flags: `#category` `@payer` `!mine` `!theirs` `!fund` `!personal` |
| `/balance` | Who owes who right now, per currency |
| `/fund [YYYY-MM]` | Shared food fund — paid in, spent, what is left |
| `/topup <amount> [@member]` | Pay into the shared food fund |
| `/month [YYYY-MM]` | Category rollup with month-over-month trend |
| `/ledger [YYYY-MM]` | Line-item ledger with source provenance |
| `/settle <amount> [note]` | Record a repayment that clears the balance |
| `/closemonth [YYYY-MM]` | Month-end settlement across both tracks |

**Example:** `/spend 1250 Makro run #food @Farid`

### The two tracks

The household does not split everything. It runs two separate arrangements, and
the ledger models both.

**Food → the shared fund.** Both members pay an equal amount into a common fund
each month. Food purchases draw it down. Nobody owes anybody for fund spending —
at month end, if the fund is overdrawn, both top up equally.

**Everything else → split.** Rent, utilities, transport and the rest are paid by
one person and split, creating a debt between them.

`/spend` routes by category: anything in `food` defaults to the fund, everything
else to personal. Override either way with `!fund` or `!personal` — a meal you
bought only for yourself is `!personal !mine`.

### How splits work

Personal expenses store `payer_share` — the fraction the payer owes themselves:

| Flag | Share | Meaning |
|------|-------|---------|
| *(default)* / `!equal` | 0.5 | Split down the middle |
| `!mine` | 1.0 | The payer was only covering themselves — no debt created |
| `!theirs` | 0.0 | The payer fronted the whole thing for the other person |

Fund expenses ignore the split entirely — the fund paid, not a person.

Balances are computed per currency and never mixed. Amounts are stored as
integer minor units (satang), so repeated splits never drift.

### Accounting period vs. spend date

An expense carries a `period` (`YYYY-MM`) separate from the date it was spent.
A grocery run on 30 July settled out of the August fund belongs to August. Left
unset, the period is the spend month. Every month-scoped report — `/fund`,
`/month`, `/ledger`, `/closemonth` — filters on the period, not the date.

### Month-end

`/closemonth` closes both tracks at once: the fund top-up each member owes, the
net of personal spending, and any imbalance in what each paid into the fund. It
deliberately keeps the top-up out of the person-to-person transfer — that money
goes into the fund, not to each other.

### Categories

`rent` · `food` · `household` · `utilities` · `transport` · `shared` · `other`

Uncategorised expenses are classified by keyword across Thai, English and
Russian before falling back to `other`.

---

## 🔗 Google sync setup

Sync is entirely optional — everything works locally without it. To turn it on:

**1. Pick an auth mode.**

*Service account (recommended for Railway):* create one in Google Cloud
Console, download the JSON key, and set `GOOGLE_SERVICE_ACCOUNT_JSON` to the
whole JSON on one line. Then **share** the calendar, the sheet and the Drive
folder with the service account's `...iam.gserviceaccount.com` email address —
a service account sees nothing until you share with it.

*OAuth refresh token:* set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and
`GOOGLE_REFRESH_TOKEN` for the Google account that owns the calendar.

**2. Point it at the targets.**

```
JF_CALENDAR_ID=...@group.calendar.google.com
LEDGER_SHEET_ID=<the long id in the spreadsheet URL>
DRIVE_FOLDER_ID=<the long id in the folder URL>
```

**3. Check it.** Run `/gsync` in Telegram — every configured target shows ✅.
Run `/sync` to push any ledger rows recorded before sync was switched on.

Sync is idempotent: a `gsync_map` table records what has already been pushed, so
re-running never duplicates an event or a row.

---

## 🧪 Tests

```bash
python -m pytest tests/ -q
```

`tests/test_finance.py` covers split arithmetic, balance netting, settlements,
multi-currency isolation and monthly trends. `tests/test_fund.py` covers the
food fund — contributions, drawdown, overdraft, contribution imbalance, schema
migration of an older database — and reproduces the household's real August 2026
figures end to end against the MASTER sheet. `tests/test_secretary.py` covers
the offline extraction fallback, filing, malformed-payload resilience and
formatting. All run without network access.
