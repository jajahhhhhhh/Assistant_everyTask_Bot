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
| `GOOGLE_CLIENT_ID` | ❌ | เปิดตัวเลือก Google Drive ใน `/settings` |
| `GOOGLE_CLIENT_SECRET` | ❌ | คู่กับ `GOOGLE_CLIENT_ID` |
| `PUBLIC_BASE_URL` | ❌ | URL สาธารณะของบริการ ใช้ประกอบ redirect ของ OAuth |

## นำเข้าประวัติแชทจาก LINE

webhook รับได้เฉพาะข้อความที่เข้ามา *หลังจาก* ต่อ webhook แล้ว บทสนทนาก่อนหน้านั้น
เข้าระบบได้ทางเดียวคือไฟล์ export

**ในแอป LINE:** เปิดห้องแชท → เมนู (☰) → การตั้งค่า → บันทึกประวัติแชท → ได้ไฟล์ `.txt`

**ส่งไฟล์นั้นเข้า Telegram bot** บอทจะ parse แล้วเขียนลง `chat_threads` /
`chat_messages` ตารางเดียวกับที่ webhook ใช้ พร้อมคัดแยก intent ให้ข้อความขาเข้า

ถ้าเป็นแชทกลุ่ม หรือบอทเดาไม่ได้ว่าชื่อไหนคือคุณ ให้ใส่ caption มาด้วย:

```
ฉันคือ <ชื่อที่คุณใช้ในแชทนั้น>
```

คำใบ้อยู่ตรงไหนของ caption ก็ได้ ไม่ต้องขึ้นต้น และใส่อัญประกาศครอบชื่อได้ ชื่อที่
พิมพ์มาจะถูกจับคู่กับผู้ส่งจริงในไฟล์แบบไม่สนตัวพิมพ์ใหญ่เล็กและช่องว่างเกิน
ถ้าไม่มีชื่อนั้นในไฟล์ บอทจะบอกว่าไม่พบ พร้อมรายชื่อที่มีจริง ไม่นำเข้าผิดเงียบ ๆ

ไม่ใส่แล้วเดาไม่ได้ ทุกข้อความจะถูกนับเป็นขาเข้า และตัวเลข "รอเราตอบ" จะผิด —
บอทจะเตือนเมื่อเกิดกรณีนี้

**บอกชื่อทีหลังได้** ถ้านำเข้าไปแล้วโดยยังไม่รู้ว่าใครคือคุณ ให้ส่งไฟล์เดิมซ้ำ
พร้อม caption ที่ถูก บอทจะ **แก้ทิศทางของแถวเดิม** ไม่เพิ่มแถวใหม่ และจะล้าง
intent กับ contact ของข้อความที่กลายเป็นของคุณเองทิ้ง (ข้อความของเราเองไม่ใช่
สิ่งที่รอเราตอบ) ในข้อความตอบจะมีบรรทัด "แก้ทิศทางของเดิม: N"

**สิ่งที่ควรรู้**

- ส่งไฟล์เดิมซ้ำไม่เพิ่มแถว และ export ห้องเดิมอีกรอบตอนมีข้อความใหม่ก็ต่อท้าย
  ห้องเดิม เพิ่มเฉพาะข้อความใหม่ — คีย์ห้องผูกกับ *ห้อง* ไม่ใช่ *ไฟล์*
- กันซ้ำเทียบที่ (เวลา, เนื้อความ) **ไม่รวมทิศทาง** ไม่งั้นการส่งซ้ำพร้อมชื่อ
  เจ้าของที่ถูก จะมองข้อความของคุณเองเป็นของใหม่แล้วเพิ่มเข้าไปอีกชุด
- ห้องที่นำเข้าใช้ `external_chat_id` ขึ้นต้นด้วย `import:` จึงไม่ปนกับห้องที่รับสด
- ผู้ส่งจากไฟล์ export ได้ contact คนละแถวกับที่ webhook สร้าง เพราะไฟล์มีแค่ชื่อ
  ที่แสดง ไม่มี LINE user id — พิสูจน์ไม่ได้ว่าเป็นคนเดียวกัน
- บรรทัดที่อ่านไม่ออกจะถูกนับและรายงานกลับมาเสมอ ไม่ทิ้งเงียบ ๆ
- ชื่อห้องมาจากบรรทัดหัวไฟล์ ถ้าไฟล์ไม่มี ใช้ชื่อไฟล์แทน

**รูปแบบไฟล์ที่อ่านได้**

| เรื่อง | รองรับ |
|---|---|
| ตัวคั่น | tab, ช่องว่างตั้งแต่สองตัว, และ **ช่องว่างเดียว** |
| บรรทัดวันที่ | `2026/08/25`, `25/08/2026`, `2026.08.25 วันจันทร์`, `2026/08/25 (Mon)` |
| ปี | ค.ศ. และ พ.ศ. (แอปภาษาไทย export มาเป็น 2569) |
| เวลา | 24 ชม. และ AM/PM |
| ข้อความ | หลายบรรทัดต่อหนึ่งข้อความ, ข้อความที่เหมือนกันเป๊ะในนาทีเดียวกัน |
| หัวไฟล์ | มีหรือไม่มีก็ได้ |

รูปแบบช่องว่างเดียว (`10:37 MR.HOME KOH SAMUI ครับผม`) ตัดด้วยตำแหน่งไม่ได้ เพราะ
ชื่อคนมีช่องว่างข้างในได้ ตัวอ่านจึงกวาดทั้งไฟล์ก่อนหนึ่งรอบแล้วเดาชุดชื่อผู้ส่ง
จากความถี่ — ชื่อซ้ำทุกบรรทัดของคนนั้น คำแรกของเนื้อความไม่ซ้ำแบบนั้น

ข้อจำกัดที่รู้ตัว: ชื่อที่โผล่ครั้งเดียวในไฟล์แบบช่องว่างเดียวเดาไม่ได้ (ไม่มีอะไร
ให้เทียบความถี่) และถ้ามีสองคนที่ชื่อหนึ่งเป็นคำขึ้นต้นของอีกชื่อ ("Ann" กับ
"Ann Lee") จะแยกไม่ออก ทั้งสองกรณีบรรทัดนั้นจะถูกรายงานว่าอ่านไม่ออก ไม่ถูกเดาผิด

## คัดแยกเจตนาด้วยโมเดล

ตัวคัดแยกพื้นฐานเป็น regex — มันจับคำ ไม่ได้เข้าใจประโยค ผลจริงจากประวัติแชท
งานรีโนเวต: 80 จาก 99 ข้อความขาเข้าถูกจัดเป็น smalltalk ทั้งที่เนื้อหาเป็นราคางาน
ใบเสนอราคา และงวดเงิน ประโยคอย่าง "ยอดงาน 30,000 แจ้งเบิก 15,000" ไม่มีคำว่า
"ขอ" หรือ "ช่วย" จึงไม่เข้ากฎไหนเลย

ตั้ง `OPENAI_API_KEY` แล้ว webhook จะใช้โมเดลคัดแยกให้อัตโนมัติ

```
/reclassify     คัดแยกข้อความที่นำเข้าไปแล้วใหม่ด้วยโมเดล
```

ทำเฉพาะข้อความ **ขาเข้า** ที่ความมั่นใจต่ำกว่า 0.80 (เส้นที่ view ใช้อยู่แล้ว) —
ข้อความที่กฎมั่นใจอยู่แล้วไม่ต้องเสียค่าเรียกโมเดลซ้ำ ส่งทีละ 25 ข้อความต่อการ
เรียกหนึ่งครั้ง และจำกัดที่ 500 ข้อความต่อการกดหนึ่งครั้ง (`RECLASSIFY_LIMIT`)

**กฎคือพื้น โมเดลคือส่วนเพิ่ม** ทุกทางที่โมเดลล่ม ตอบช้า ตอบผิดรูป หรือตอบค่าที่
`chat_messages` เก็บไม่ได้ จะตกกลับไปใช้ผลของ regex เสมอ ไม่มีทางที่ข้อความจะไม่
ถูกคัดแยกเพราะโมเดลมีปัญหา

| ตัวแปร | ค่าเริ่มต้น |
|---|---|
| `CLASSIFIER_MODEL` | ตามด้วย `OPENAI_MODEL` แล้ว `gpt-4o-mini` |
| `CLASSIFIER_BATCH` | 25 |
| `RECLASSIFY_LIMIT` | 500 |

## ผูกห้องแชตเข้ากับไซต์งาน

ผู้รับเหมาเจ้าเดียวทำงานให้หลายไซต์ แยกงานด้วยคำในข้อความไม่แม่นเพราะเขาพูดถึง
หลายที่ปนกัน แต่แยกด้วย **ห้อง** แม่นเสมอ — LINE ให้ `groupId` ต่างกันมาอยู่แล้ว
และ `chat_threads.project_id` มีคอลัมน์รออยู่ตั้งแต่แรก

```
/rooms                      ดูห้องทั้งหมด พร้อมจำนวนข้อความและไซต์ที่ผูกไว้
/rooms <เลขห้อง> <ชื่อไซต์>   ผูกห้องเข้ากับไซต์ (ไม่มีไซต์ก็สร้างให้)
```

ตัวอย่าง `/rooms 1 ลิปะน้อย` — ชื่อไซต์มีช่องว่างได้

การผูกจะอัปเดต **ข้อความที่นำเข้าไปแล้วด้วย** ไม่ใช่เฉพาะข้อความใหม่ เพราะ
`chat_messages` ถือ `project_id` ของตัวเอง (คัดลอกจากห้องตอนบันทึก) ถ้าอัปเดต
แค่ห้อง ประวัติเก่าทั้งกองจะไม่มีไซต์

ไซต์ที่สร้างใหม่จะได้ `projects.type = 'personal'` เพราะคอลัมน์นั้นมี CHECK
อยู่ห้าค่า และงานรีโนเวตไม่เข้าพวกไหนเลย

## Google Drive (ไม่บังคับ)

ผู้ใช้เลือก Drive ได้จาก `/settings` แล้วงาน การเตือน และโน้ตของคนนั้นจะถูกส่ง
สำเนาขึ้นไปเป็นไฟล์ JSON ไฟล์เดียวในโฟลเดอร์ `Assistant everyTask Bot`

**SQLite ยังเป็นต้นฉบับเสมอ Drive เป็นสำเนา** — ตัวส่งการเตือนกับ `/done` อ้าง
rowid ของ SQLite ถ้าย้ายต้นฉบับไป Drive สองอย่างนั้นพังทันที และการเขียนที่ล้ม
กลางทางจะกลายเป็นข้อมูลหาย ไม่ใช่แค่สำเนาไม่ตรง ส่งขึ้นไม่สำเร็จบอทจะบอกในแชต
ไม่เงียบ

ตั้งค่าฝั่งเจ้าของบอทครั้งเดียว:

1. [Google Cloud Console](https://console.cloud.google.com/apis/credentials) →
   Create Credentials → OAuth client ID → Web application
2. Authorized redirect URI ใส่ `https://<โดเมน>/oauth/google/callback`
3. เอา Client ID / Client Secret ไปตั้งเป็น `GOOGLE_CLIENT_ID` /
   `GOOGLE_CLIENT_SECRET` และตั้ง `PUBLIC_BASE_URL=https://<โดเมน>`

ผู้ใช้แต่ละคนแค่กด `/settings` → 📁 Google Drive → กดปุ่มอนุญาต **ไม่มี
credential ตัวไหนต้องพิมพ์ลงแชต** ลิงก์ยินยอมหมดอายุใน 15 นาที และขอสิทธิ์แค่
`drive.file` คือเห็นเฉพาะไฟล์ที่บอทสร้างเอง ไม่ใช่ทั้งไดรฟ์

## Deploy to Railway

ทั้ง Telegram bot และ LINE webhook รันในโปรเซสเดียว (`app.py`) — ทั้งคู่เขียน
SQLite ไฟล์เดียวกัน และ volume ของ Railway ผูกกับ service เดียว ถ้าแยกสอง service
ฝั่งหนึ่งจะเขียนลงดิสก์ที่หายทุก deploy

1. **New Project → Deploy from GitHub repo** เลือกรีโปนี้ (branch `main`)
2. **Variables** — ใส่อย่างน้อยหนึ่งฝั่ง จะเปิดทั้งสองฝั่งพร้อมกันก็ได้

   | ตัวแปร | ใช้ทำอะไร |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | เปิด Telegram bot (ไม่ใส่ = ข้ามส่วนนี้) |
   | `LINE_CHANNEL_SECRET` + `LINE_CHANNEL_ACCESS_TOKEN` | เปิดการรับ webhook ของ LINE |
   | `LINE_OWNER_USER_ID` | LINE user id ของเจ้าของ — คำสั่งในแชทรับจากคนนี้เท่านั้น |
   | `OPENAI_API_KEY` | แปลภาษา + ถอดเสียงใน Telegram bot |
   | `DATA_DIR` | ตั้งเป็น `/data` ให้ตรงกับ volume (ดูข้อ 3) |
   | `RENO_BRIDGE` | `1` ถ้าจะต่อกับ Reno Dashboard (ดู [RENO_BRIDGE.md](RENO_BRIDGE.md)) |
   | `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` + `PUBLIC_BASE_URL` | เปิด Google Drive ใน `/settings` |

   `PORT` Railway ใส่ให้เอง ไม่ต้องตั้ง

3. **Volume** — Settings → Volumes → Add, mount path `/data` แล้วตั้ง `DATA_DIR=/data`
   ⚠️ ข้ามข้อนี้แล้วงาน แชท และประวัติการรอทั้งหมดจะหายทุกครั้งที่ deploy
   เพราะดิสก์ของคอนเทนเนอร์ไม่ถาวร

4. **Deploy** — schema (`sql/01_schema.sql` + `02_views.sql`) ถูกลงให้อัตโนมัติ
   ตอนบูตครั้งแรก ไม่ต้องรันเอง

5. **Generate Domain** (Settings → Networking) แล้วเอา URL ไปตั้งใน
   LINE Developers → Messaging API → Webhook URL เป็น `https://<โดเมน>/webhook/line`
   กด **Verify** แล้วเปิด **Use webhook** (ปิด Auto-reply messages)

### เช็คว่าขึ้นแล้ว

```bash
curl https://<โดเมน>/healthz              # {"status":"ok","block_pointer_drift":0}
curl https://<โดเมน>/healthz/invariants   # 500 ถ้าตัวชี้ tasks/task_blocks เพี้ยน (E3)
```

`/healthz` คือ health check ของ Railway จึงตอบ 200 ตลอดตราบใดที่ยังเปิดฐานข้อมูลได้
ส่วนความสอดคล้องของข้อมูลดูที่ `/healthz/invariants` — แยกกันเพื่อไม่ให้ข้อมูลเพี้ยน
ทำให้คอนเทนเนอร์ถูกรีสตาร์ตวน

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
