# Assistant EveryTask Bot 🤖

AI-powered Telegram bot for productivity!

## Features

- 📋 **Task Management** - Add, track, complete tasks
- ⏰ **Smart Reminders** - Natural language scheduling
- 📝 **Quick Notes** - Save ideas instantly
- 🌐 **Translation** - 20+ languages (Thai, English, Chinese, Japanese, etc.)
- 🎤 **Voice Transcription** - Send voice message, get text!
- 📊 **Storage Options** - Local, Airtable, Google Sheets

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/help` | Full guide |
| `/task <title>` | Add task |
| `/tasks` | List tasks |
| `/done <id>` | Complete task |
| `/remind <time> <text>` | Set reminder |
| `/reminders` | List reminders |
| `/note <content>` | Save note |
| `/notes` | View notes |
| `/tr <lang> <text>` | Translate |
| `/settings` | Storage settings |
| `/mystorage` | View settings |
| `/language` | Set language |

## Translation Examples

```
/tr th Hello world → สวัสดีโลก
/tr en สวัสดี → Hello
/tr ja Good morning → おはようございます
```

## Voice Messages

Just send any voice message → Auto-transcribed by AI!

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | ✅ | From @BotFather |
| `OPENAI_API_KEY` | ✅ | For translation & transcription |
| `DATA_DIR` | ❌ | Default: `data` |

## Deploy to Railway

1. Fork this repo
2. Connect to Railway
3. Set environment variables
4. Deploy!

## Tech Stack

- Python 3.11
- python-telegram-bot
- OpenAI GPT & Whisper
- SQLite
- APScheduler
- aiohttp

---

Made with ❤️
