# skills/ — ปลั๊กอิน reno-* ที่ต่อกับ LINE webhook แล้ว

สกิลชุดนี้คือของเดิมจาก Reno Dashboard plugin ที่แก้สองเรื่อง

## 1. ชื่อ array ให้ตรงกับ dashboard จริง

สกิลเดิมอ้างถึง `SAMPLE_TASKS` และ `SAMPLE_PAYMENTS` แต่ไฟล์ `dashboard-final.html`
จริงใช้ `T0` (งาน) กับ `P0` (การเงิน) — สกิลจึงหา array ไม่เจอ และฟิลด์การเงินก็คนละชื่อ

| เอกสารเดิม | ไฟล์จริง |
|---|---|
| `SAMPLE_TASKS` | `T0` |
| `SAMPLE_PAYMENTS` | `P0` |
| `SAMPLE_ITEMS` | `SAMPLE_ITEMS` (ตรงอยู่แล้ว) |
| payment `vendor` / `note` / ISO date | `who` / `desc` / `DD/MM/YY` พ.ศ. |

## 2. ต่อกับ `reno_bridge.py`

ทุกสกิลมีหัวข้อใหม่บอกว่าจะดึงข้อมูลสดจาก LINE ได้ยังไง — ข้อความจากพี่ปอเข้ามาที่
`line_webhook.py` แล้วถูกแยกเป็นงาน/เงิน/สต็อกรอไว้ในคิว ไม่ต้อง export `.txt` อีก

| สกิล | คำสั่งที่เพิ่ม |
|---|---|
| `reno-setup` | ชี้ `RENO_CONFIG` มาที่ `CONFIG.md` เดียวกัน + ตรวจด้วย `status --json` |
| `reno-status` | `status --json` → คิวค้าง + งานที่ติดใคร + คำถามที่ยังไม่ตอบ |
| `reno-ingest-chat` | `pending --json --schema skills` → ได้ schema เดิมของสกิลเลย |
| `reno-update-tasks` | `approve` + `apply` (เขียน `T0` และสร้าง task ใน Life OS) |
| `reno-log-money` | `pending --schema skills` → payment ที่ยังเป็น pending จนกว่าจะโอนจริง |
| `reno-stock-move` | `pending --schema skills` → มี `operation: in\|out` มาให้แล้ว |
| `reno-weekly-brief` | `status --json` → ชั่วโมงที่เสียไปกับการรอ แยกตามคน |

รายละเอียดทั้งหมดอยู่ใน [../RENO_BRIDGE.md](../RENO_BRIDGE.md)

## ติดตั้ง

คัดลอกทั้งโฟลเดอร์กลับเข้าที่เดิมของปลั๊กอิน (ที่ที่มี `PLUGIN.md` กับ `CONNECTORS.md`)
หรือวางไว้ที่ `~/.claude/skills/` ถ้าใช้เป็นสกิลส่วนตัว โครงไฟล์ไม่เปลี่ยนจากเดิม
