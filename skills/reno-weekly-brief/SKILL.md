---
name: reno-weekly-brief
description: Generates a one-page weekly recap of the renovation project — tasks completed, money spent, what's slipping, and the top 3 things to focus on next week. Splits per site if multiple are configured. Trigger when the user says "สรุปสัปดาห์", "weekly brief", "weekly summary", "Friday recap", "ปิดสัปดาห์", "Monday brief", "end of week", or asks "how did this week go".
---

# Reno Weekly Brief

Produce a structured weekly recap. Heavier than `reno-status` — this is narrative, looks at trends, and proposes priorities. Use on Mondays (forward-looking) and Fridays (backward-looking) by default.

## Inputs

Read `./CONFIG.md` for sites, contractor, currency, language.

Read all three data sources:
- `~~dashboard-path/dashboard-final.html` → `T0` (tasks), `P0` (payments)
- `~~dashboard-path/inventory-system.html` → `SAMPLE_ITEMS`

## Steps

### 1. Determine the window

From the user's request and `currentDate`:
- "Friday recap" / "end of week" → window is **Mon → today**
- "Monday brief" → window is **last Mon → Sun (the week just finished)**, also surface upcoming week
- "this week" → Mon → Sun of current week
- "last week" → previous Mon → Sun
- Default if ambiguous: previous 7 days ending on `currentDate`

Compute the absolute date range and state it in the output header.

### 2. Compute per-site sections

For each configured site (`~~site-a`, `~~site-b`):

**Done this week** — tasks with `s: 'Done'`. (Note: we don't have completion timestamps in the schema, so use the `date` field if set, otherwise count by current status snapshot. Caveat in the output if so.)

**Money paid this week** — payments with `date` in window AND `status: 'paid'`. Sum, list top 3 by amount.

**Money committed (approved/pending)** — payments not paid in window. Sum, list top 1–2.

**Slipping** — tasks with `pri: 'High'` AND `s: 'Todo'` for more than 7 days (or no obvious progress). Tasks with `date < currentDate` and `s != 'Done'`.

**In flight** — count of `s: 'In Progress'` and `s: 'Review'`.

### 3. Cross-site section

- Total paid this week (both sites combined)
- Largest pending payment overall
- Any stock now out / critically low

### 4. Top 3 for next week

Rank candidates for "next week's focus" by:
1. High priority + Todo for 7+ days → must do
2. Pending payments where work is blocked → must act on
3. In Progress for 14+ days → likely stuck, needs unblock

Pick 3, no more. Be specific — "decide on tile pattern for Chaweng kitchen" not "make decisions".

### 5. Format the output

In `~~language`. For Thai (default), use this shape:

```
📊 สรุปสัปดาห์ {Mon DD — Sun DD}

🏠 {~~site-a}
   ✅ เสร็จ {n} งาน: {list 2-3 highlights}
   💰 จ่ายไป {~~currency-symbol}{paid_this_week}: {top 2-3 line items}
   🔧 กำลังทำ {n} · รอเริ่ม {n}
   ⚠️ ค้าง: {1-2 specific items, or "ไม่มีงานค้าง"}

🏢 {~~site-b}
   {same structure}

🌐 ภาพรวม
   จ่ายรวมสัปดาห์: {~~currency-symbol}{total}
   ค้างจ่าย: {~~currency-symbol}{total} — {largest single item}
   สต็อก: {n} หมด, {n} ใกล้หมด

🎯 อาทิตย์หน้า ต้องลุย 3 เรื่องนี้
   1. {specific actionable item, site-labeled}
   2. {specific actionable item}
   3. {specific actionable item}
```

For English, equivalent labels: Done / Paid / In flight / Slipping / Big picture / Next week.

Keep total length under 400 words. Bulleted/structured, never paragraphs.

### 6. Offer follow-ups

Add a one-line suggestion at the bottom:

> "Want me to log next week's plan as tasks? Just say so."

Or:

> "Big pending payment to {vendor} — `reno-log-money` if you've approved it."

## Wait time and response time (reno_bridge)

`T0`/`P0` say what was done, not what the week was spent waiting on. If the LINE
webhook is running, pull that in:

```bash
python reno_bridge.py status --json
```

Add one section per site, before "top 3 next week":

```
⏳ เวลาที่เสียไปกับการรอ
   รอ {person} {hours} ชม. · {task title}
   คำถามที่ยังไม่ตอบ {n} ข้อ (นานสุด {hours} ชม.)
```

This is usually the most actionable part of the brief: a week where nothing moved often
looks identical to a busy week in the Kanban, and only the wait hours show the difference.

If the command isn't available, omit the section rather than guessing.

## What NOT to do

- Don't show data the user can derive at a glance (e.g., "total tasks: 47") — focus on what changed and what to do.
- Don't praise progress with empty phrases ("great work this week!"). The user wants signal, not pep.
- Don't reuse `reno-status` output. This is narrative; `reno-status` is a snapshot.
- Don't pretend to know completion dates if the schema doesn't track them. Be honest: "based on current status (no completion timestamp in data)".
- If `~~site-b` is single-site mode (blank or "(single-site mode)" in CONFIG.md), produce a single combined section, not two.
