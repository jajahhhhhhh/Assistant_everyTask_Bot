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

---

## LINE Webhook (`line_webhook.py`)

ตัวรับ webhook ของ LINE Messaging API ที่เติม `chat_messages` และ `task_blocks`
ตาม schema ใน `sql/01_schema.sql` (Life OS) — ฝั่ง "คนเขียน" ของ view ทั้งหมดใน
`sql/02_views.sql`

### จังหวะการเขียน

| จังหวะ | เกิดที่ไหน | เขียนอะไร | ทำไมต้องอยู่ตรงนี้ |
|--------|-----------|-----------|--------------------|
| 0 | ในคำขอ | — | ตรวจ `X-Line-Signature` ก่อนแตะฐานข้อมูล |
| 1 | ในคำขอ | `line_webhook_deliveries` | จอง `webhookEventId` — LINE ยิงซ้ำแล้วกลายเป็น no-op |
| 2 | ในคำขอ | `chat_threads`, `contacts`, `chat_messages` (`direction='in'`) | commit ให้เสร็จ **ก่อน** ตอบ 200 ข้อความจึงไม่หายเมื่อ ack แล้วโปรเซสตาย |
| 3 | ในคำขอ | — | ตอบ 200 ทันที ไม่ให้ LINE timeout แล้วยิงซ้ำ |
| 4 | เบื้องหลัง | `chat_messages.intent/urgency/confidence` | ตัวคัดแยกช้ากว่างบของ 200 จึงเติมทีหลัง และเขียน `intent` คู่กับ `confidence` เสมอ |
| 5 | เบื้องหลัง | `task_blocks.unblocked_at` + `tasks` + `task_events` | คนที่เรารอตอบกลับมา = การรอจบ ปิดด้วยเวลาที่ **เขาส่ง** ไม่ใช่เวลาที่เราประมวลผลเสร็จ |
| 6 | เบื้องหลัง | `tasks`, `task_blocks` | คำสั่งของเจ้าของ (สร้างงาน / ประกาศว่าติด / เคลียร์) |
| 7 | เบื้องหลัง | `chat_messages` (`direction='out'`), `responded_at` | ส่งผ่าน reply token ถ้ายังไม่หมดอายุ ไม่งั้น push — และเขียนแถว **หลัง** LINE รับเรื่องแล้ว |

### สามจุดที่ตั้งใจให้ไม่สมมาตร

1. **ขาเข้าเขียนก่อนตอบ 200 ขาออกส่งก่อนเขียน** — ข้อความเข้าหายไม่ได้ ส่วนแถว
   `direction='out'` ที่ไม่มีข้อความจริงจะไปปิดนาฬิกาของ `v_reply_latency`
   ให้คำถามที่ยังไม่มีใครตอบ
2. **ข้อความตอบรับของบอทไม่ถูกบันทึกเป็นแถว `out`** (`LINE_LOG_BOT_ACKS=0`)
   เพราะ `v_reply_latency` ถือว่าข้อความ `out` ถัดไปในเธรดคือ "คุณตอบแล้ว"
   ถ้าบอทตอบรับทุกข้อความ `v_unanswered_now` จะว่างตลอดกาล
   `responded_at` เติมได้ทางเดียวคือ `record_owner_reply()` ซึ่งใช้ตอนคนตอบจริง
3. **`รอคนตอบ` ปิดด้วยข้อความอะไรก็ได้ แต่ `รอเอกสาร/รอเงิน/รอของ` ต้องมีหลักฐาน**
   (ไฟล์แนบ รูปสลิป หรือคำว่าโอนแล้ว/ส่งของแล้ว) ไม่งั้นเวลารอจะถูกปิดทั้งที่ของยังไม่มา

### invariant ที่ยึดไว้

ทุกครั้งที่แตะ `task_blocks` จะอัปเดตตัวชี้บน `tasks` ในทรานแซกชันเดียวกัน และ
บังคับว่า "หนึ่งงาน = การรอที่เปิดอยู่ไม่เกินหนึ่งรายการ" ตามข้อ E1/E2/E3 ใน
`sql/04_queries.sql` — `GET /healthz` รันคิวรี E3 ให้ ถ้าตัวชี้เพี้ยนจะได้ HTTP 500
พร้อมรายการงานที่เพี้ยน

### คำสั่งในแชท (เฉพาะ `LINE_OWNER_USER_ID`)

| พิมพ์ | ผล |
|-------|-----|
| `งาน: ตามใบเสนอราคา` | สร้าง task ใหม่ `source='line'`, `source_ref` ชี้กลับไปที่ข้อความ |
| `ติด #12 รอเอกสาร @Farid` | เปิด `task_blocks` ใหม่ (ปิดอันเก่าให้ก่อน) + mirror ลง `tasks` |
| `เคลียร์ #12` | ปิดการรอที่เปิดอยู่ แล้วดันงานกลับไป `doing` |

### เริ่มใช้งาน

```bash
pip install -r requirements.txt
sqlite3 data/assistant.db < sql/01_schema.sql
sqlite3 data/assistant.db < sql/02_views.sql
cp .env.example .env      # ใส่ LINE_CHANNEL_SECRET / LINE_CHANNEL_ACCESS_TOKEN
python line_webhook.py    # ฟังที่ $PORT พาธ $LINE_WEBHOOK_PATH
```

ตั้ง Webhook URL ใน LINE Developers เป็น `https://<โดเมน>/webhook/line`
แล้วเปิด Use webhook (ปิด Auto-reply messages ไว้)

### ต่อกับ Reno Dashboard

`reno_bridge.py` แปลงข้อความ LINE ที่ webhook เก็บไว้ เป็นงาน/รายการเงิน/สต็อก
ของ Dashboard J แล้วพักไว้ในคิวให้เจ้าของยืนยันก่อนเขียนจริง — สกิล `reno-*`
เรียกผ่าน CLI ของไฟล์นี้ รายละเอียดทั้งหมดอยู่ใน [RENO_BRIDGE.md](RENO_BRIDGE.md)

```bash
export RENO_BRIDGE=1
python reno_bridge.py pending --json
python reno_bridge.py approve --ids 3,4
python reno_bridge.py apply --dashboard-dir ~/reno-dashboard
```

### เทสต์

```bash
python -m pytest tests/          # 130 เทสต์
python -m unittest discover -s tests -t .   # ไม่มี pytest ก็รันได้
```

เทสต์ทั้งหมดเขียนด้วย `unittest` ล้วน ไม่ต้องมี plugin เพิ่ม และไม่มีตัวไหนต่อเน็ตจริง
— OpenAI กับ LINE ถูกสลับเป็นตัวปลอมในเทสต์

เทสต์รัน `sql/01_schema.sql` และ `sql/02_views.sql` จริงบนไฟล์ชั่วคราว แล้วอ่านผล
ผ่าน `v_wait_spans` / `v_unanswered_now` เพื่อยืนยันว่าสิ่งที่ webhook เขียน
รายงานอ่านได้จริง
