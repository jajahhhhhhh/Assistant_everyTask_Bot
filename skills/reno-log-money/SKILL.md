---
name: reno-log-money
description: Log a payment (paid or pending) into the renovation dashboard's payment ledger, or ingest a vendor bill (HomePro, Global House, contractor invoice) and add it as line items. Writes to the P0 payment array in dashboard-final.html. Trigger when the user says "log payment", "บันทึกจ่าย", "จ่าย MR.HOME", "เพิ่มบิล", "log a bill", "add payment", "I paid X", "got a quote for", "received an invoice".
---

# Reno Log Money

Adds or updates entries in the payment ledger (`P0` in `dashboard-final.html`). Also handles ingesting full vendor bills into multiple payment line items.

## Payment schema

```js
{
  id: number,         // unique number
  date: string,       // 'DD/MM/YY' in พ.ศ. — e.g. '10/06/69' (NOT ISO; that's what the file uses)
  desc: string,       // description: bill # / draw # / purpose
  proj: string,       // 'Lipa' | 'Chaweng' | 'Both' — matches ~~site-a/b
  who: string,        // vendor: 'MR.HOME KOH SAMUI', 'HomePro', 'Global House', etc.
  amount: number,     // in ~~currency (positive)
  status: string,     // 'paid' | 'pending' | 'approved' (awaiting transfer)
}
```

> `reno-ingest-chat` emits `vendor`/`note`/ISO-`date`; map them when writing:
> `vendor → who`, `note → desc`, `2026-06-10 → 10/06/69`.

## Steps

### 1. Load context

Read `./CONFIG.md` for `~~dashboard-path`, sites, `~~currency-symbol`. If missing, ask the user to run `reno-setup`.

Read `~~dashboard-path/dashboard-final.html` and find the `const P0=[` block.

### 2. Determine the operation

| Operation | User phrasing |
|---|---|
| **Single payment** | "จ่าย MR.HOME 50000 งวด 2", "I paid HomePro 1250 today" |
| **Mark pending → paid** | "Approved the X payment", "โอนแล้ว MR.HOME งวด 3" |
| **Bill ingest** | User uploads/pastes a multi-line bill (HomePro receipt, Global House invoice) |
| **Add pending/quote** | "MR.HOME quoted 80000 for งวด 4", "got a quote for..." |

### 3. For SINGLE PAYMENT

Parse the amount, vendor, status, project, date, note. If any are missing:
- **Vendor**: required — ask if not stated. Common: MR.HOME, HomePro, Global House, Lazada, Shopee, local hardware store.
- **Amount**: required — ask if not stated. Strip commas, currency symbols.
- **Status**: default to `'paid'` if the user phrased it past-tense ("จ่ายแล้ว", "I paid"); `'pending'` if future-tense ("ต้องจ่าย", "owe", "รอจ่าย"); `'approved'` if the user said they approved a quote but haven't transferred.
- **Project**: default to whichever site was last discussed; if unclear, ask. Use `'Both'` only if the user explicitly says it spans sites.
- **Date**: today (`currentDate` from session) if not given.
- **Note**: leave empty if unknown — don't invent details.

### 4. For BILL INGEST

If the input is a full bill (multiple line items, store header, totals), treat the whole bill as ONE payment entry — not many. The dashboard tracks payments at the bill level, not item level. Item-level detail goes to inventory via `reno-stock-move`.

Extract:
- Vendor = store name (HomePro, Global House)
- Amount = grand total after discounts/VAT
- Date = receipt date
- Note = bill number + brief content summary (e.g., "#6938330 — electrical fittings × 10")

After logging the payment, **offer to also call `reno-stock-move`** to add the bill's line items to inventory. Do NOT do both in one shot without confirming — the user may only want the payment side.

### 5. For MARK PAID

Find the matching pending entry (by vendor + amount + note). If multiple candidates, list and ask. Update `status` to `'paid'` and update `date` to the paid date (today by default).

### 6. Write the change

Use `Edit` on `dashboard-final.html` targeting the `P0` array. Same surgical approach as `reno-update-tasks`:
- Add: insert as last element before the closing `];`
- Update: replace the specific field
- Match the existing formatting style of the file

### 7. Confirm

In `~~language`:

```
✓ บันทึก: {~~currency-symbol}{amount} → {vendor} ({status}, {site})
   ยอดจ่ายแล้ว {site}: {~~currency-symbol}{site_total_paid}
   ค้างจ่าย {site}: {~~currency-symbol}{site_total_pending}
```

Compute the running totals after the edit so the user gets immediate context.

## Edge cases

- **Refunds / negative amounts**: store as a separate entry with `note: 'คืนเงิน'` and a negative `amount` — most rendering code handles this by subtracting from totals.
- **Split bills** (one bill, multiple sites): if the user says "this bill is half for each", create two entries with `proj: '~~site-a'` and `proj: '~~site-b'`, each at half the amount. Note "(split)" in the note field.
- **Currency conversion**: if a user pastes a bill in a different currency than `~~currency`, ask the conversion rate rather than guessing. Don't pull live FX.
- **localStorage drift**: same warning as `reno-update-tasks` — if the user has been actively editing in-browser, our edit to `P0` won't appear unless localStorage is cleared or the user re-imports.

## Payments that came in from LINE (reno_bridge)

When the contractor asks for a draw in LINE ("ขอเบิกค่าแรง 15,000"), the webhook has
already parsed it into the queue as a **pending** payment. Check there before writing
by hand:

```bash
python reno_bridge.py pending --json --schema skills   # payments[] with vendor/amount/status
python reno_bridge.py approve --ids 8
python reno_bridge.py apply --dashboard-dir ~~dashboard-path
```

The bridge sets `status: 'paid'` only when the message actually says the money moved
("โอนแล้ว", "จ่ายแล้ว"); a request to draw stays `pending`. When the transfer happens
later, that's a MARK PAID edit on the existing `P0` row (step 5) — not a second entry.

Bills that arrive as photos or PDFs still go through the manual BILL INGEST path; the
bridge only reads text messages.
