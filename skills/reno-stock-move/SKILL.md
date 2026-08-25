---
name: reno-stock-move
description: Update inventory in the renovation dashboard — record stock in (purchases received), stock out (items used on site), or quantity adjustments. Can also ingest a full bill's line items as new SKUs. Writes to SAMPLE_ITEMS in inventory-system.html. Trigger when the user says "เบิก", "ซื้อเข้า", "ปรับสต็อก", "ของหมด", "stock in", "took out", "received shipment", "used X today", "add this bill to inventory".
---

# Reno Stock Move

Updates the inventory list and transaction log in `inventory-system.html`. There are two data structures:

- `SAMPLE_ITEMS` — the SKU master list (one entry per product)
- `TXS` — transaction history (in/out/adjust events)

A stock movement updates the item's `qty` field AND appends a transaction. Edits to `SAMPLE_ITEMS` only affect first-load defaults; once the user has interacted with the dashboard, their state lives in `localStorage.stock_items`. **Strongly prefer outputting an "import this" JSON payload** that the user pastes via the dashboard's manual flow, rather than editing the HTML, unless they explicitly want the HTML modified.

## Item schema

```js
{
  id: string,        // 'ITM001', 'ITM-HP015', etc.
  sku: string,       // vendor SKU or barcode
  name: string,      // human name (Thai or English)
  cat: string,       // 'โคมไฟ' | 'ไฟฟ้า' | 'ชุดเครื่องนอน' | 'ห้องน้ำ' | 'เครื่องมือ' | 'เฟอร์นิเจอร์' | 'อื่นๆ'
  icon: string,      // emoji
  proj: string,      // 'Lipa' | 'Chaweng' | 'Both'
  qty: number,       // current stock
  minQty: number,    // reorder threshold
  unit: string,      // 'ชิ้น', 'ชุด', 'ม้วน', 'หลอด', etc.
  price: number,     // ~~currency per unit
  loc: string,       // storage location
  note: string,      // brand/model/source
}
```

## Transaction schema

```js
{
  id: string,        // 'TX' + timestamp
  itemId: string,    // matches an item id
  itemName: string,
  type: string,      // 'in' | 'out' | 'adj'
  qty: number,
  proj: string,      // 'Lipa' | 'Chaweng'
  date: string,      // YYYY-MM-DD
  note: string,
}
```

## Steps

### 1. Load context

Read `./CONFIG.md`. Read `~~dashboard-path/inventory-system.html`. Locate `SAMPLE_ITEMS = [` block — that's the master list to read from for lookups.

### 2. Determine the operation

| Op | User phrasing |
|---|---|
| **Stock IN** | "ซื้อเข้า LED 9W 20 หลอด", "received 5 boxes of tiles" |
| **Stock OUT** | "เบิก สายไฟ 2 ม้วน ไปห้อง 201", "used 3 cans of paint" |
| **Adjust** | "นับสต็อกใหม่ ของจริง 8 ชิ้น", "actual count is 8" |
| **New SKU** | item not in list — need to add to `SAMPLE_ITEMS` first |
| **Bill ingest** | User pastes/uploads a bill — process all items at once |
| **Low stock query** | "อะไรใกล้หมด" — read-only, list items where qty ≤ minQty |

### 3. For STOCK IN/OUT/ADJUST

Find the item by SKU, name (fuzzy), or ID. If ambiguous, list candidates with current qty and ask.

Validate:
- For OUT: check `requested_qty <= current_qty`. If not, warn and ask whether to (a) cap at available, (b) proceed anyway (will go negative), or (c) cancel.
- For ADJUST: ask what the new total should be, not the delta. The dashboard renders this as a 'adj' transaction with delta = new - old.

### 4. For NEW SKU (item not found)

Build a new item object. Required fields:
- `name`, `cat`, `proj`, `unit`, `price`, `qty` (initial)
Optional:
- `minQty` (default to 2 or `Math.ceil(qty * 0.2)`)
- `loc`, `note`, `icon`, `sku` (generate `'ITM' + timestamp.slice(-4)` if absent)

Suggest a `cat` based on keywords (โคม → โคมไฟ, ปลั๊ก → ไฟฟ้า, ผ้าปู → ชุดเครื่องนอน, ก๊อก → ห้องน้ำ, สว่าน → เครื่องมือ). Auto-suggest `icon` from category.

### 5. For BILL INGEST

User pastes/uploads a multi-line bill (HomePro, Global House are common). For each line item:
- Match to existing SKU by name fuzzy match → if found, treat as Stock IN
- If not found → create new SKU + Stock IN

Show the user a summary table BEFORE writing:
```
{vendor} bill — {n} items
  [+ NEW]  EILON LED 7W × 10 @ ฿125
  [+ IN ]  หลอด LED 9W × 4 (existing, current qty: 24 → 28)
  ...
Confirm? (yes / edit / cancel)
```

### 6. Write the change

**Preferred path — JSON paste import**: Output a JSON block the user pastes into the dashboard. Format depends on what we built into the dashboard's UI; for now, surface JSON in this shape:

```json
{
  "items": [ /* new SAMPLE_ITEMS entries */ ],
  "txs":   [ /* new TXS entries */ ]
}
```

Tell the user: "Paste this into the dashboard's manual import (when available) — or say 'write to HTML' and I'll edit the file directly."

**Direct path — Edit HTML**: If the user explicitly opts in, use `Edit` on `inventory-system.html` to:
- Append new items to `SAMPLE_ITEMS`
- Update `qty` on existing items
- (Note: `TXS` in the inventory file is empty on first load — transactions live in `localStorage.stock_txs`. We can't usefully edit transaction history via the HTML; this is a localStorage-only structure.)

Warn: "Direct HTML edit only affects users who have NOT yet interacted with the dashboard (localStorage is empty). For an active user, paste-import is the right path."

### 7. Confirm

In `~~language`:

```
✓ {operation}: {item name} × {qty} {unit}
   คงเหลือ: {new_qty} {unit} {if low: '⚠️ ต่ำกว่า min (' + minQty + ')'}
```

If a low-stock threshold was crossed, append a heads-up.

## Edge cases

- **QR code scanning**: this is a browser-side feature. We can't trigger it from chat. If the user mentions wanting to scan, point them to the dashboard's `📷 สแกน QR` view.
- **Multi-site stock**: an item with `proj: 'Both'` shares a single qty. If the user splits a shipment across sites, that's two separate IN transactions with different `proj` values, but only one item.
- **Receipts on different dates**: HomePro receipts often have "รอรับสาขา" status. Treat as Stock IN on the pickup date, not the order date. If the user pastes an unfulfilled order, ask whether the goods have arrived before adding to stock.

## Stock lines that came in from LINE (reno_bridge)

Purchases and withdrawals mentioned in LINE are pre-parsed into the queue with the
direction already decided:

```bash
python reno_bridge.py pending --json --schema skills   # stock[] with operation: 'in' | 'out'
python reno_bridge.py approve --ids 9
python reno_bridge.py apply --dashboard-dir ~~dashboard-path
```

`operation` is `'out'` when the message reads like a withdrawal ("เบิกสายไฟ 2 ม้วน
ไปหน้างาน") and `'in'` otherwise. `sku` is usually empty — LINE messages rarely carry
one — so fill it from the bill before approving if you have it.

`apply` appends new rows to `SAMPLE_ITEMS`. It does **not** write `TXS`, so a movement
on an existing SKU is still a manual edit; treat the queue entry as the source and
adjust `qty` yourself.
