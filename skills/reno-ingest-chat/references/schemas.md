# Ingest Schemas

Detailed field-by-field rules for the JSON output of `reno-ingest-chat`. Match these so the downstream import skills don't need to re-validate.

`python reno_bridge.py pending --json --schema skills` emits exactly these shapes for anything that arrived through the LINE webhook, so items from that source need no re-validation either. It uses `project: null` (not a guess) when the site is unclear, and lists those ids under `needs_site`.

## Task

```ts
{
  title: string         // required, max 80 chars, source verbatim
  note: string          // optional, max 200 chars
  status: 'Todo' | 'In Progress' | 'Review' | 'Done'  // default 'Todo'
  project: string       // ~~site-a or ~~site-b name, or null if unclear
  type: 'Electrical' | 'Plumbing' | 'Demo' | 'Structure' | 'Finishing' | 'General'
  cost: number          // default 0
  date: string          // 'YYYY-MM-DD' or '' if none
  priority: 'High' | 'Medium' | 'Low'  // default 'Medium'
}
```

## Payment

```ts
{
  vendor: string        // required
  amount: number        // required, positive (use negative for refunds)
  status: 'paid' | 'pending' | 'approved'  // default by tense
  project: 'Lipa' | 'Chaweng' | 'Both'     // map site names to these enum values
  date: string          // 'YYYY-MM-DD', required (today if not stated)
  note: string          // bill #, draw #, or short description
}
```

## Stock

```ts
{
  name: string          // required, source verbatim
  sku: string           // vendor SKU if visible, else ''
  operation: 'in' | 'out' | 'adj' | 'new'  // 'new' = new SKU + initial qty
  qty: number           // required, positive
  unit: string          // 'ชิ้น', 'ชุด', 'ม้วน', 'หลอด', etc.
  project: string       // ~~site-a/b name, or null
  price: number         // per-unit, default 0
  category: string      // dashboard category (see below)
  location: string      // optional, where it's stored
}
```

### Category enum (Thai-first because dashboard is Thai)

- `โคมไฟ` — lighting
- `ไฟฟ้า` — electrical (outlets, switches, wires)
- `ชุดเครื่องนอน` — bedding
- `ห้องน้ำ` — bathroom
- `เครื่องมือ` — tools
- `เฟอร์นิเจอร์` — furniture
- `อื่นๆ` — other

## Status update

```ts
{
  match_task_title_fuzzy: string  // text to fuzzy-match an existing task
  new_status: 'Todo' | 'In Progress' | 'Review' | 'Done'
  site: 'Lipa' | 'Chaweng' | null
  note?: string  // optional context to append to the task's note field
}
```

## Validation rules

- All `date` fields: ISO `YYYY-MM-DD` only. Reject `'10/06/69'`, `'June 10'`, etc. — convert to ISO.
- All `amount` and `cost`: number, no commas, no currency symbol. `'฿1,250'` → `1250`.
- `project` / `site` strings: must exactly match a configured site name OR `'Both'`. If the source uses an alias ("the new place" → "Chaweng"), do the mapping in this skill; downstream skills assume the value is canonical.
- `title` / `name`: do NOT auto-translate. Keep Thai as Thai, English as English. The dashboard renders both fine.
- Empty arrays are OK. Don't synthesize entries to "balance" the output.
