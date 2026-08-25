---
name: reno-status
description: Reads the current state of the renovation dashboards (tasks, payments, stock) across all configured sites and produces a quick snapshot. Read-only — does not edit anything. Trigger when the user asks "ภาพรวม", "status", "what's going on", "ตอนนี้เป็นยังไง", "snapshot", "how's the project", "where are we", "ดูสถานะ", or wants a quick read on project state without a full weekly brief.
---

# Reno Status

Produce a fast snapshot of the renovation project state. Read-only. This is the "tell me what's happening right now" skill — use a heavier skill like `reno-weekly-brief` for narrative reports.

## Inputs

Read `./CONFIG.md` from the working directory. If missing, fall back to assuming the dashboard files are in the working directory and use generic site labels ("Site A", "Site B"). Note the degraded state in the output.

## Steps

### 1. Read the dashboard files

From `~~dashboard-path`:
- Read `dashboard-final.html` — extract the `T0` array (tasks) and `P0` array (payments).
- Read `inventory-system.html` — extract the `SAMPLE_ITEMS` array.

Use `Read` directly on the HTML files. The arrays are embedded as JavaScript literals inside `<script>` tags — parse them out by locating the `const T0=[` and `const P0=[` markers (and `const SAMPLE_ITEMS = [` in inventory) and reading until the matching `];`.

Payment fields in `P0` are `{id, date, desc, proj, who, amount, status}` — `who` is the vendor and `desc` is the description; `date` is `DD/MM/YY` in พ.ศ. (e.g. `10/06/69`).

### 2. Compute the snapshot

For each site (per `~~site-a` and `~~site-b`):

- **Tasks**: count by status (Todo / In Progress / Review / Done). Compute % done.
- **Payments**:
  - Total paid (`status === 'paid'`)
  - Total pending (`status !== 'paid'`)
  - Largest 1–2 pending items (vendor + amount)
- **Stock**:
  - Total SKUs
  - Out of stock count (`qty === 0`)
  - Low stock count (`qty > 0 && qty <= minQty`)
  - Top 3 low/out items by recency or criticality

Cross-site:
- Combined paid this month (compare against `currentDate` from session)
- Anything flagged as `pri: 'High'` and not `Done` — these are the things needing attention

### 3. Format the output

Format in the user's `~~language`. For Thai (default), use this structure:

```
📊 ภาพรวมโครงการ — {YYYY-MM-DD}

🏠 {~~site-a}
   งาน: {Done}/{Total} เสร็จ ({%}) · {In Progress} กำลังทำ · {Todo} รอเริ่ม
   เงิน: จ่ายแล้ว {~~currency-symbol}{paid} · ค้าง {~~currency-symbol}{pending}
   {if pending items: "รอจ่าย: {top 1-2 vendors and amounts}"}

🏢 {~~site-b}
   {same structure}

📦 สต็อก
   {total} SKUs · {out_count} หมด · {low_count} ใกล้หมด
   {if any low/out: list top 3 with name + qty}

🚨 ต้องดู
   {bullet list of High-priority undone tasks, or "ไม่มี" if none}
```

For English, use equivalent labels: Tasks / Money / Stock / Needs attention.

Keep the entire snapshot under 200 words. No tables — bullet/line format renders better in chat.

### 4. Suggest one next action

Append a single sentence: the one thing the user should probably do next, inferred from the data. Examples:
- "Big pending payment to MR.HOME — log it once you've transferred?"
- "3 SKUs out of stock — `reno-stock-move` to add the new shipment?"
- "Nothing urgent — good time to clear those 4 Todo tasks?"

## LINE queue and open waits (reno_bridge)

If `line_webhook.py` is running with `RENO_BRIDGE=1`, the dashboard files are only
half the picture. Run this **before** formatting the snapshot:

```bash
python reno_bridge.py status --json
```

It returns three things the HTML files cannot know:

- `inbox_pending` — items parsed from LINE that are waiting for the owner's `reno ok`
- `blocked_now` — tasks currently blocked, with **who** they're waiting on and for how many hours
- `unanswered_now` — questions from the contractor or vendors that nobody has replied to yet

Add these blocks to the snapshot (skip a block when its list is empty):

```
⏳ รอคนอื่นอยู่
   {person} · {task title} · {waiting_hours} ชม.

❓ ยังไม่ได้ตอบ
   {person}: {preview} ({waiting_hours} ชม.)

📥 จาก LINE รอยืนยัน {inbox_pending} รายการ — `reno ok` ในแชท หรือ reno-ingest-chat
```

If the command fails (bridge not installed, no database), skip these blocks silently
and report the dashboard-only snapshot. Don't treat it as an error.

## What NOT to do

- Don't make edits. This skill is read-only.
- Don't try to be exhaustive. A snapshot is fast and ruthless about what matters.
- Don't show timestamps for each task or payment — that's noise.
- Don't recompute weekly trends — that's `reno-weekly-brief`.
- If `~~site-b` is "(single-site mode)" in CONFIG.md, skip its section entirely.
