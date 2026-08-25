---
name: reno-ingest-chat
description: Parse a chunk of LINE chat, email, or pasted text from the contractor or vendors into structured tasks, payments, and stock items ready to import into the renovation dashboard. Outputs JSON the user can paste into the dashboard's AI-ingest UI. Trigger when the user says "อ่าน LINE", "parse this", "ingest chat", "what's in this message", "อ่านบิล", or pastes/uploads a block of chat or a bill image/text.
---

# Reno Ingest Chat

This skill is the parser. It does NOT mutate the dashboard files directly — instead it produces structured JSON that the user can review and import via the dashboard's existing AI-ingest UI (`📁 LINE .txt` / `📋 วางข้อความ` buttons). This keeps the user in control of what gets added.

## When to use vs other skills

| Situation | Use |
|---|---|
| Single clear request — "log payment to X" | `reno-log-money` directly |
| Single clear task — "เพิ่มงาน Y" | `reno-update-tasks` directly |
| Full LINE conversation paste | **`reno-ingest-chat`** |
| Vendor bill (HomePro receipt) | **`reno-ingest-chat`** for payment+items; then offer to call `reno-log-money` and `reno-stock-move` |
| Photo of handwritten note | **`reno-ingest-chat`** — OCR happens in the model, then structure |

## Steps

### 1. Load context

Read `./CONFIG.md` to know the site names, contractor name, currency. These help map mentions to the right project.

### 2. Read the input

The input arrives in one of these forms:
- Inline pasted text in the user's message
- A file path the user provided (read via `Read`)
- An image (Read renders it; describe what's visible if it's a handwritten/photo bill)
- A LINE `.txt` export with timestamps and sender names

### 3. Classify each line/segment

Walk through the input and tag each segment as one of:
- **TASK** — work to be done ("ต้องเดินสายใหม่ห้อง 201", "need to redo the floor in living room")
- **PAYMENT** — money transferred or owed ("จ่าย MR งวด 2 50000", "HomePro bill 1250")
- **STOCK** — items received/used ("ของมาแล้ว 10 หลอด LED", "ใช้สายไฟไป 1 ม้วน")
- **STATUS UPDATE** — progress on existing work ("งานปูกระเบื้องห้องครัวเสร็จแล้ว") — emits an update to an existing task, not a new task
- **NOISE** — greetings, thanks, photos with no actionable content — discard

For each TASK/PAYMENT/STOCK, extract the fields needed by the respective schema (see `references/schemas.md`). Use `~~contractor` for assignee defaults.

### 4. Resolve site assignment

For each item, infer the project:
- Explicit mention ("ที่ลิปะ", "Chaweng house") → use that
- Vendor on bill matches a previous bill → use that bill's site
- LINE thread context (sender/group naming) → use that
- **Unclear** → mark as `proj: null` in output and flag it for the user

### 5. Output structured JSON

Format the result as:

```json
{
  "summary": {
    "tasks": 4,
    "payments": 2,
    "stock_items": 3,
    "status_updates": 1,
    "unclassified": 2
  },
  "tasks": [
    {
      "title": "...",
      "note": "...",
      "status": "Todo",
      "project": "Lipa",
      "type": "Electrical",
      "cost": 0,
      "date": "",
      "priority": "Medium"
    }
  ],
  "payments": [
    {
      "vendor": "MR.HOME",
      "amount": 50000,
      "status": "paid",
      "project": "Lipa",
      "date": "2026-06-12",
      "note": "งวด 2"
    }
  ],
  "stock": [
    {
      "name": "หลอด LED 9W",
      "sku": "LED-001",
      "operation": "in",
      "qty": 10,
      "unit": "หลอด",
      "project": "Chaweng",
      "price": 89
    }
  ],
  "status_updates": [
    {
      "match_task_title_fuzzy": "ปูกระเบื้องห้องครัว",
      "new_status": "Done",
      "site": "Chaweng"
    }
  ],
  "unclassified": [
    "raw text segments that need human review"
  ]
}
```

### 6. Show the user a digest

In `~~language`, summarize what you found BEFORE writing or proposing imports:

```
📥 อ่านแล้ว ({n} บรรทัด)
   ✅ {tasks} งาน · 💰 {payments} รายการเงิน · 📦 {stock_items} สต็อก
   ⚠️  {unclassified} บรรทัดต้องดูเพิ่ม

ดูตัวอย่าง 3 อัน:
   1. [งาน · Lipa] เดินสายไฟใหม่ห้อง 201 (Electrical, Medium)
   2. [จ่าย · Chaweng] MR.HOME 50,000 บาท (งวด 2)
   3. [สต็อก · Chaweng] +10 หลอด LED 9W

ทำต่อ?
   (1) ก๊อปปี้ JSON ไปวางใน dashboard
   (2) ให้ผมเรียก reno-log-money และ reno-stock-move บันทึกให้เลย
   (3) แก้ข้อมูลก่อน
```

### 7. Hand off

Based on the user's choice:
- **(1)** Output the full JSON block in a fenced code block. Done.
- **(2)** Sequentially call `reno-update-tasks`, `reno-log-money`, `reno-stock-move` with the parsed data. Skip items the user already chose to drop.
- **(3)** Ask which item to edit. Loop until the user is happy, then go back to step 6.

## Live LINE input (reno_bridge)

When `line_webhook.py` is running, LINE messages are already on the server and
pre-parsed into a review queue — the user does not need to export a `.txt` at all.
**Check this first** before asking for a paste:

```bash
python reno_bridge.py pending --json --schema skills
```

The output is already in this skill's schema (`tasks` / `payments` / `stock` with the
field names in `references/schemas.md`), plus two extra keys:

- `inbox_ids` — the queue ids, needed to approve or skip
- `needs_site` — ids the parser could not assign to a site; these are the ones to ask about

Steps 3–4 (classify, resolve site) are already done for these items, so go straight to
step 6 (digest) and offer:

```
(1) วาง JSON ใน dashboard          → python reno_bridge.py export --out reno-import.json
(2) ให้ผมบันทึกให้เลย               → python reno_bridge.py approve --ids <ids>
                                      python reno_bridge.py apply
(3) แก้ก่อน                         → python reno_bridge.py skip --ids <ids> แล้วแก้มือ
```

Set the site on anything in `needs_site` before approving — `apply` refuses items with no
site (that is the plugin's first hard rule, enforced in code):

```bash
python reno_bridge.py approve --ids 7,8      # หลังตั้งไซต์แล้วเท่านั้น
```

Still use the manual paste path from step 2 for anything that did **not** arrive through
LINE: photos of bills, forwarded email, a `.txt` export from before the webhook existed.

> The dashboard's own **📁 LINE .txt / 📋 วางข้อความ** buttons call the Anthropic API
> directly from the browser with no API key and no auth header, so that button cannot
> work as shipped. The `export` file above is meant for the same paste box, which
> accepts the JSON without going through that call.

## What NOT to do

- Don't invent costs, dates, or quantities not present in the source. If a task has no cost, output `cost: 0`, not a guess.
- Don't mix tasks and status updates — if MR.HOME says "ปูกระเบื้องเสร็จแล้ว", that's a STATUS UPDATE on an existing task, NOT a new task to add.
- Don't dedupe across the same input — if the chat mentions the same payment twice in different messages, output it twice and let the user merge.
- Don't translate the source content into the output language. Keep `name`, `note`, `title` fields verbatim from the source. The summary/digest can be translated.

## Additional reference

See `references/schemas.md` for the full schema details and field-by-field validation rules.
