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
│   └── autoscan.py               # Auto-extraction of actionable items
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
