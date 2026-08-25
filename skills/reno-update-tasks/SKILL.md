---
name: reno-update-tasks
description: Add, move, edit, or delete Kanban tasks in the renovation dashboard. Writes back to dashboard-final.html so the change appears when the user refreshes the browser. Trigger when the user says "add task", "เพิ่มงาน", "move to done", "ย้ายไป", "mark done", "เสร็จแล้ว", "delete task", "ลบงาน", "update task", or describes new work the contractor needs to do.
---

# Reno Update Tasks

Mutates the Kanban task list in `dashboard-final.html`. The tasks live in a JavaScript array literal called `T0` inside a `<script>` tag.

## Task schema

Each task object has these fields:

```js
{
  id: number,        // unix timestamp or unique number
  t: string,         // title (Thai or English)
  n: string,         // note / description
  s: string,         // status: 'Todo' | 'In Progress' | 'Review' | 'Done'
  p: string,         // project/site: matches ~~site-a or ~~site-b name
  type: string,      // category: 'Electrical' | 'Plumbing' | 'Demo' | 'Structure' | 'Finishing' | 'General'
  cost: number,      // estimated cost (in ~~currency, may be 0)
  date: string,      // target date YYYY-MM-DD (may be empty)
  pri: string,       // 'High' | 'Medium' | 'Low'
  by: string,        // assignee initials, default 'MR' for ~~contractor
}
```

## Steps

### 1. Load context

- Read `./CONFIG.md` for `~~dashboard-path`, `~~contractor`, site names. If missing, ask the user to run `reno-setup` first, OR proceed by assuming dashboard is in cwd and ask once which site this task is for.
- Read `~~dashboard-path/dashboard-final.html` and locate the `const T0=[` block. Note the exact byte range — you'll need to surgically Edit it later.

### 2. Determine the operation

From the user's request, decide which of these to do:

| Operation | User phrasing examples |
|---|---|
| **Add** | "เพิ่มงาน...", "add a task for...", "ต้องไปทำ..." |
| **Move status** | "ย้าย X ไป Done", "mark X as done", "X เสร็จแล้ว" |
| **Edit field** | "แก้ราคา X เป็น...", "change deadline of X to..." |
| **Delete** | "ลบงาน X", "remove the task..." |
| **Bulk add** | A list of multiple tasks (often from LINE chat — consider delegating to `reno-ingest-chat` first) |

If the operation isn't obvious, use `AskUserQuestion` with the inferred operation as first option.

### 3. For ADD

If the user didn't specify all fields, infer reasonable defaults:
- `s`: default to `'Todo'` unless the user says it's in progress
- `p`: default to whichever site was last mentioned in the conversation; if unclear, **ask** — don't guess
- `type`: infer from keywords ("เดินสายไฟ" → Electrical, "ปูกระเบื้อง" → Finishing, etc.) — see `references/task-types.md`
- `cost`: 0 if not given; don't make up numbers
- `date`: empty unless user gave one (use ISO format `YYYY-MM-DD`)
- `pri`: Medium unless user signals urgency ("ด่วน", "asap" → High)
- `by`: `~~contractor`'s initials (e.g., 'MR' for MR.HOME)
- `id`: use `Date.now()` style — generate a unix-ms number unique within the current array

Show the user the parsed task in their `~~language` and confirm with one sentence before writing.

### 4. For MOVE / EDIT / DELETE

Find the task by:
1. Exact title match
2. Fuzzy partial match — if multiple candidates, list them and ask which
3. If no match, tell the user "not found" and suggest running `reno-status` to see what exists

### 5. Write the change

Use the `Edit` tool to modify `dashboard-final.html`. **Critical**: edit the `T0` array literal directly. Do not touch the surrounding HTML, CSS, or other script logic.

For an add, insert the new object as the last element of the array (before the closing `];`). Match the existing formatting style — these arrays are typically formatted with one object per line and trailing comma for the last item.

For an edit, modify the specific field in place. Use a small `old_string` / `new_string` that uniquely matches.

For a delete, remove the entire object line including its trailing comma.

### 6. Confirm

After the Edit succeeds, tell the user what happened, in `~~language`:

```
✓ เพิ่มงาน: "{title}" ใน {site} ({type}, {pri})
   รีเฟรช dashboard-final.html เพื่อดู
```

## Edge cases

- **localStorage drift**: The user's browser stores edited tasks in `localStorage.dj_tasks`, which overrides `T0` on load. If they've been using the dashboard, edits to `T0` won't appear unless they clear localStorage OR they use the dashboard's built-in paste-import. Warn the user once and offer to output the new task as JSON instead, which they can paste into the dashboard's AI-ingest UI.
- **Bulk operations** (5+ tasks at once): batch all Edits into the smallest possible diff. Each `Edit` call is independent — don't fan out unnecessarily.
- **Date math**: when the user says relative dates ("พรุ่งนี้", "next Monday"), convert to absolute `YYYY-MM-DD` using `currentDate` from session context. Do NOT use `Date.now()` in scripts — compute the date yourself.

## Additional reference

See `references/task-types.md` for the full list of task type keywords and their Thai/English equivalents.

## Tasks that came in from LINE (reno_bridge)

Work arriving through `line_webhook.py` is already parsed and waiting in a queue —
don't retype it. List it, then approve and apply:

```bash
python reno_bridge.py pending --json          # kind: "task" entries
python reno_bridge.py approve --ids 12
python reno_bridge.py apply --dashboard-dir ~~dashboard-path
```

`apply` appends to `T0` for you, backs the file up as `dashboard-final.html.bak`, and
also creates the matching row in the Life OS `tasks` table so the task shows up in the
bottleneck views (`v_task_cycle`, `v_blocked_now`) instead of living only in HTML.

Items whose site the parser could not determine are refused by `apply`. Set the site
first — in LINE the owner can type `reno site #12 เฉวง`, or edit the queue row directly.

Use the manual `Edit` flow above for anything not in the queue.
