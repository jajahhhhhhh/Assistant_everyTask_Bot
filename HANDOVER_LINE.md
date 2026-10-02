# งานค้าง: ต่อช่อง LINE เข้ากับ webhook

สถานะวันที่หยุดพัก: **โค้ดเสร็จและ deploy ขึ้นแล้ว เหลือแค่ใส่ค่าสามตัวกับตั้ง Webhook URL ในคอนโซล LINE**
ไฟล์นี้เขียนไว้เพื่อให้หยิบมาทำต่อได้โดยไม่ต้องไล่อ่านแชทเก่า

---

## 1. สิ่งที่เสร็จแล้ว

| ของ | สถานะ |
|---|---|
| `line_webhook.py` — handler ที่เขียน `chat_messages` / ปิด `task_blocks` ตามลำดับที่ถูกต้อง | merged |
| `app.py` — รวม web server + Telegram polling ใน loop เดียว, SIGTERM draining | merged |
| `reno_bridge.py` + `skills/` — ต่อ skills เข้ากับ webhook | merged |
| `scripts/import_expenses.py` — ตัวนำบิลเข้าตาราง `expenses` (อ่านจาก env `EXPENSE_IMPORT_JSON`) | merged |
| Railway service `everytask-bot` | live, `GET /healthz` = 200 |
| `DATABASE_PATH=/data/assistant.db` (ต้องเป็น path เต็ม ไม่ใช่ `data/...`) | ตั้งแล้ว |
| service เก่า `lipa-line-bot` / `chaweng-line-bot` + volume | ลบแล้ว |
| GitHub repo เก่าสองอัน | ลบแล้ว (มีไฟล์ zip สำรองไว้) |

**ช่อง LINE ที่จะใช้เป็นช่องหลัก: `chowrest-cowork`**

---

## 2. สิ่งที่ค้าง — ทำตามลำดับนี้

### ขั้นที่ 1 — หาค่าสามตัวจากคอนโซล LINE

เข้า https://developers.line.biz/console/ → เลือก Provider → คลิกช่อง `chowrest-cowork`

| ตัวแปร | แท็บ | ตำแหน่ง |
|---|---|---|
| `LINE_CHANNEL_SECRET` | Basic settings | กลางหน้า หัวข้อ **Channel secret** |
| `LINE_OWNER_USER_ID` | Basic settings | ล่างสุด หัวข้อ **Your user ID** ขึ้นต้น `U` + อักษร 32 ตัว |
| `LINE_CHANNEL_ACCESS_TOKEN` | Messaging API | ล่างสุด **Channel access token (long-lived)** → กด Issue |

ถ้าไม่เห็นแท็บ Messaging API → ไปเปิดก่อนที่ https://manager.line.biz → เลือกบัญชี → Settings → Messaging API → Enable
ถ้าไม่เห็นช่องในลิสต์เลย → กำลัง login ด้วยบัญชี LINE ผิดบัญชี

> `LINE_OWNER_USER_ID` ต้องเป็น user ID (`U` + 32 ตัว) **ไม่ใช่ Channel ID ที่เป็นเลข 10 หลัก** ใส่ Channel ID ไปจะไม่ match กับ `line_user_id` และคำสั่งเจ้าของจะไม่ทำงานแบบเงียบ ๆ

### ขั้นที่ 2 — ใส่ค่าที่ Railway

Railway → project `Every Task Bot` → service `everytask-bot` → environment `production` → แท็บ Variables

ใส่/ทับสามตัว: `LINE_CHANNEL_SECRET`, `LINE_CHANNEL_ACCESS_TOKEN`, `LINE_OWNER_USER_ID` แล้วกด **Apply**

> ตอนนี้สองตัวแรกยังเป็น reference ชี้ไปที่ service เก่า (`${{lipa-line-bot....}}`) ซึ่งถูกลบแล้ว **ต้องทับด้วยค่าจริงของ chowrest-cowork**
> อย่าเอาโทเคนจริงไปวางในแชทหรือ commit ลง repo นี้ — repo เป็น public

### ขั้นที่ 3 — ตั้ง Webhook URL ในคอนโซล LINE

แท็บ **Messaging API** → หัวข้อ Webhook settings

1. Webhook URL = `https://everytask-bot-production.up.railway.app/webhook/line`
2. กด **Update** แล้วกด **Verify** → ต้องขึ้น **Success**
3. เปิด **Use webhook**
4. ปิด **Auto-reply messages** และ **Greeting messages** (ลิงก์ไป LINE Official Account Manager) ไม่ปิดจะตอบซ้อนกับบอท

---

## 3. วิธีเช็กว่าใช้ได้จริง

1. `GET https://everytask-bot-production.up.railway.app/healthz` → 200
2. ดู log ตอนบูตใน Railway → **ต้องไม่มี** คำเตือนเรื่อง secret หาย
3. ยิง POST เปล่าไปที่ `/webhook/line` → ต้องได้ **401** (ถ้าได้ 503 แปลว่า `LINE_CHANNEL_SECRET` ยังไม่เข้า)
4. ทักข้อความจริงจาก LINE → log ต้องเห็น request และต้องมีแถวใหม่ใน `chat_messages` พร้อมค่า `intent`
5. ส่ง `งาน: ทดสอบ` จากบัญชีเจ้าของ → ถ้าบอทรับคำสั่ง แปลว่า `LINE_OWNER_USER_ID` ถูกต้อง
6. `GET /healthz/invariants` → 200 (ถ้า 500 แปลว่ามี task ที่มี open block ซ้อนกัน ผิด invariant E3)

---

## 4. กับระวังที่เคยเจอมาแล้ว อย่าพลาดซ้ำ

- **`railway.json` ทับค่าที่ตั้งในหน้า service ทั้งบล็อก `deploy`** ตั้ง `startCommand` / `preDeployCommand` ในหน้าเว็บแล้วมันเงียบหายไปเลย ต้องแก้ในไฟล์
- **คอนเทนเนอร์ pre-deploy ไม่ mount volume** ห้ามเอาสคริปต์ที่แตะ `/data` ไปไว้ใน `preDeployCommand` (เคยพังเพราะ `unable to open database file`) ย้ายไปทำตอน startup ใน `app.py` แล้ว
- **`DATABASE_PATH` ต้องเป็น `/data/assistant.db`** ถ้าเป็น relative จะกลายเป็น `/app/data` ซึ่งเป็น ephemeral และข้อมูลหายทุกครั้งที่ deploy (เคยทำข้อมูล 5 วันหายไปแล้วหนึ่งรอบ)
- **การลบของใน Railway ต้องยืนยัน 2FA บนหน้าเว็บ** ทำผ่าน API/MCP ไม่ได้ จะค้างแล้ว timeout เฉย ๆ ทางที่ได้คือ stage ไว้แล้วกด Apply ในแดชบอร์ด
- **ข้อมูลบิล/ยอดเงิน ห้าม commit** repo เป็น public ค่าพวกนี้อยู่ใน env `EXPENSE_IMPORT_JSON` เท่านั้น

---

## 5. ของเก่าที่ควรเก็บตกถ้ายังไม่ได้ทำ

ช่อง LINE เก่า (lipa / chaweng) ถ้ายังเปิด Use webhook ชี้ไป domain ที่ถูกลบไปแล้ว
(`lipa-line-bot-production.up.railway.app`, `chaweng-line-bot-production.up.railway.app`)
ให้เข้าไปปิด Use webhook ของสองช่องนั้น ไม่งั้นมันจะยิงแล้ว error เรื่อย ๆ
