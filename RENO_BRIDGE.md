# Reno Bridge — ต่อ LINE webhook เข้ากับสกิล reno-*

สกิล `reno-*` ทำงานกับ array ในไฟล์ HTML ของ Dashboard J ส่วน `line_webhook.py`
รับข้อความ LINE เข้ามาเก็บใน SQLite (Life OS schema) `reno_bridge.py` คือสะพาน
ระหว่างสองฝั่ง — แปลงข้อความเป็นรายการ **แล้วพักไว้ในคิวให้เจ้าของยืนยันก่อน**

```
LINE ─► line_webhook ─► chat_messages ─► reno_inbox ─► ยืนยัน ─┬─► dashboard-final.html (T0 / P0)
                             │             (pending)           ├─► inventory-system.html (SAMPLE_ITEMS)
                             │                                 └─► tasks + task_events (Life OS)
                             └─► task_blocks (เวลารอ) ──────────────► v_blocked_now / v_wait_by_person
```

## ทำไมต้องมีคิวคั่น

ตัวแยกข้อความเป็นกฎภาษาไทย ไม่ใช่ความจริง — "ค่าแรงงาน" กับ "งาน" ต่างกันแค่คำเดียว
ถ้าเขียนตรงเข้า dashboard ตัวเลขงบจะเพี้ยนโดยไม่มีใครรู้ คิว `reno_inbox` ทำสามอย่าง:

1. ให้เจ้าของกดยืนยันก่อนข้อมูลจริงจะขยับ (`reno ok`)
2. ข้อความเดิม scan กี่รอบก็ไม่เกิดรายการซ้ำ (UNIQUE ต่อ message + fingerprint ของ payload)
3. บังคับกฎเหล็กข้อ 1 — รายการที่ยังไม่รู้ว่า Lipa หรือ Chaweng **apply ไม่ได้**
   จนกว่าจะระบุไซต์ (โหมดไซต์เดียวข้ามข้อนี้)

## สกิลไหนเรียกอะไร

| สกิล | คำสั่งที่เพิ่มเข้าไป | ได้อะไร |
|---|---|---|
| `reno-setup` | เขียน `CONFIG.md` ตามเดิม + ตั้ง `RENO_BRIDGE=1` ให้ webhook | bridge อ่าน `CONFIG.md` ไฟล์เดียวกับสกิลอื่น |
| `reno-status` | `python reno_bridge.py status --json` | คิวที่ค้าง + **งานที่ติดใครอยู่ตอนนี้** + คำถามที่ยังไม่ตอบ (สองอย่างหลังมีแต่ฝั่ง LINE ที่รู้) |
| `reno-ingest-chat` | `python reno_bridge.py pending --json` | JSON `{tasks, payments, stock, summary}` — รูปแบบเดียวกับที่สกิลนี้ผลิตอยู่แล้ว แต่มาจาก LINE จริงโดยไม่ต้อง export .txt |
| `reno-update-tasks` | `python reno_bridge.py approve --ids 3,4` แล้ว `apply` | การ์ดใหม่เข้า `T0` + สร้างแถวใน `tasks` ให้ view คอขวดเห็น |
| `reno-log-money` | เหมือนกัน กรองเฉพาะ `kind=payment` | แถวใหม่เข้า `P0` |
| `reno-stock-move` | เหมือนกัน กรองเฉพาะ `kind=stock` | แถวใหม่เข้า `SAMPLE_ITEMS` |
| `reno-weekly-brief` | `python reno_bridge.py status --json` | เวลารอสะสมต่อคน มาจาก `task_blocks` ที่ webhook ปิดให้อัตโนมัติ |

สกิลที่อ่าน Kanban/การเงินจากไฟล์ HTML อยู่แล้วไม่ต้องเปลี่ยนวิธีอ่าน — `status --json`
ตั้งใจไม่นับตัวเลขซ้ำกับไฟล์ HTML ให้เฉพาะส่วนที่ไฟล์ HTML ไม่มี

## คำสั่งเต็ม

```bash
python reno_bridge.py scan                       # แยกข้อความที่ยังไม่เคย scan เข้าคิว
python reno_bridge.py pending --json             # คิวที่รอยืนยัน (รูปแบบ import ของ dashboard)
python reno_bridge.py approve --ids 3,4          # เว้น --ids = ยืนยันทั้งคิว
python reno_bridge.py skip --ids 5
python reno_bridge.py apply --dashboard-dir ~/reno-dashboard
python reno_bridge.py apply --dry-run            # ดูว่าจะเขียนอะไร โดยไม่แตะไฟล์
python reno_bridge.py export --out reno-import.json
python reno_bridge.py status --json
```

`apply` สำรองไฟล์เดิมเป็น `.html.bak` ก่อนเขียนทุกครั้ง

## คำสั่งในแชท LINE (เฉพาะ `LINE_OWNER_USER_ID`)

| พิมพ์ | ผล |
|---|---|
| `reno list` | ดูคิวที่รออยู่ |
| `reno site #12 เฉวง` | ระบุไซต์ให้รายการที่เดาไม่ได้ |
| `reno ok` / `reno ok #12` | ยืนยัน (ทั้งคิว หรือเฉพาะรายการ) |
| `reno skip #12` | ทิ้ง |

บอทจะสรุปทุกครั้งที่จับรายการได้ เช่น

```
📥 จับได้ 2 รายการจากข้อความนี้
• #7 🔧 เดินสายไฟชั้น 3 [Done] · Chaweng
• #8 💰 ฿15,000 (รอจ่าย) · Chaweng
ยืนยัน: `reno ok` · ทิ้ง: `reno skip #<id>`
```

## เปิดใช้งาน

```bash
export RENO_BRIDGE=1                 # เปิดสะพานใน line_webhook.py
export RENO_CONFIG=CONFIG.md         # ไฟล์ที่ reno-setup สร้าง (ค่าเริ่มต้น)
# export RENO_AUTO_APPROVE=1         # ข้ามการยืนยัน — ไม่แนะนำ ดูเหตุผลด้านบน
python line_webhook.py
```

`CONFIG.md` อ่านได้ทั้งรูปตาราง markdown และรายการ:

```markdown
| Primary site   | `~~site-a`         | ลิปะน้อย            |
| Secondary site | `~~site-b`         | เฉวง                |
| Contractor     | `~~contractor`     | MR.HOME KOH SAMUI   |
| Dashboard      | `~~dashboard-path` | /Users/…/reno-dashboard |
```

- ปล่อย `~~site-b` ว่าง = โหมดไซต์เดียว (ตาม `CONNECTORS.md`)
- เพิ่มคำเรียกไซต์ได้ด้วย `~~site-a-aliases: บ้าน, บ้านแม่`

## เรื่องที่ต้องรู้ก่อนใช้

- **localStorage บังหน้าไฟล์** — เบราว์เซอร์ที่เคยเปิด dashboard จะอ่านจาก
  `dj_tasks`/`dj_pays`/`stock_items` ไม่ใช่ array ในไฟล์ ถ้าอยากเห็นของใหม่ทันที
  ใช้ `export` แล้ววาง JSON ในช่อง **📋 วางข้อความ** แทน (หรือล้าง localStorage)
- **ปุ่ม "AI อ่าน LINE" ในหน้าเว็บใช้ไม่ได้อยู่แล้ว** — `runAI()` ยิง
  `https://api.anthropic.com/v1/messages` จากเบราว์เซอร์ตรง ๆ โดยไม่มี API key
  และไม่มี header auth จึงติดทั้ง CORS และ 401 สะพานนี้ทำหน้าที่แทนช่องทางนั้น
  (ข้อความมาถึงฝั่งเซิร์ฟเวอร์ตั้งแต่แรก ไม่ต้อง export .txt แล้วอัปโหลด)
- **งานที่ apply แล้วจะมีสองที่** — การ์ดใน `T0` สำหรับดู และแถวใน `tasks`
  สำหรับให้ view คอขวดนับเวลา ทั้งคู่ผูกกลับไปที่ `chat_messages` ต้นทางผ่าน
  `source_ref` และ `linked_task_id`
- **สะพานพังไม่ทำให้ webhook พัง** — ถ้า `reno_bridge` โยน exception ข้อความยัง
  ถูกบันทึกครบและ event ยังนับว่าประมวลผลสำเร็จ

## เทสต์

```bash
python -m pytest tests/test_reno_bridge.py tests/test_line_webhook.py
```
