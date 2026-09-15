"""
Finance module — shared household ledger for J & Farid.

Provides four things the household actually needs:

  1. Line-item ledger    — every expense with date, amount, currency, category,
                           payer, split rule and provenance (which voice note or
                           receipt it came from).
  2. Who-owes-who        — running net balance per currency, after settlements.
  3. Monthly rollup      — category totals per month with month-over-month trend.
  4. Settlements         — record a payment that clears part of the balance.

Design notes
------------
* Amounts are stored as integers in minor units (satang / cents) to avoid
  floating point drift.  The public API accepts and returns floats.
* Every expense records ``payer_share`` — the fraction of the expense the payer
  is personally responsible for.  0.5 is an even split; 1.0 means the payer was
  covering only themselves (no debt created); 0.0 means the payer fronted the
  whole thing for the other person.
* Balances are computed per currency and never mixed.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────────────────

#: Canonical expense categories.  ``other`` is the catch-all.
CATEGORIES: Tuple[str, ...] = (
    "rent",
    "food",
    "household",
    "utilities",
    "transport",
    "shared",
    "other",
)

CATEGORY_EMOJI: Dict[str, str] = {
    "rent": "🏠",
    "food": "🍜",
    "household": "🧺",
    "utilities": "💡",
    "transport": "🛵",
    "shared": "🤝",
    "other": "📦",
}

DEFAULT_CURRENCY = "THB"

CURRENCY_SYMBOL: Dict[str, str] = {
    "THB": "฿",
    "USD": "$",
    "EUR": "€",
    "RUB": "₽",
    "GBP": "£",
}

#: Keyword hints used when no LLM is available to classify an expense.
_CATEGORY_HINTS: Dict[str, Tuple[str, ...]] = {
    "rent": ("rent", "lease", "deposit", "landlord", "ค่าเช่า", "เช่าบ้าน", "аренда"),
    "food": (
        "food", "grocer", "groceries", "restaurant", "lunch", "dinner",
        "breakfast", "coffee", "market", "makro", "lotus", "big c", "7-eleven",
        "seven", "อาหาร", "ข้าว", "กาแฟ", "ตลาด", "еда", "продукты",
    ),
    "household": (
        "cleaning", "soap", "detergent", "laundry", "repair", "furniture",
        "maid", "gas", "หมอนอิง", "ของใช้", "ซักผ้า", "ทำความสะอาด", "быт",
    ),
    "utilities": (
        "electric", "electricity", "water bill", "internet", "wifi", "phone bill",
        "ค่าไฟ", "ค่าน้ำ", "ค่าเน็ต", "коммуналк",
    ),
    "transport": (
        "petrol", "gasoline", "fuel", "taxi", "grab", "bolt", "ferry", "bike",
        "scooter", "น้ำมัน", "แท็กซี่", "เรือ", "бензин", "такси",
    ),
}


# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS expenses (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    spent_on     TEXT    NOT NULL,              -- ISO date (YYYY-MM-DD)
    amount_minor INTEGER NOT NULL,              -- satang / cents
    currency     TEXT    NOT NULL DEFAULT 'THB',
    category     TEXT    NOT NULL DEFAULT 'other',
    description  TEXT    NOT NULL DEFAULT '',
    payer        TEXT    NOT NULL,              -- normalised name, e.g. 'J'
    payer_share  REAL    NOT NULL DEFAULT 0.5,  -- 0..1 fraction payer owes themselves
    paid_from    TEXT    NOT NULL DEFAULT 'personal', -- 'fund' | 'personal'
    period       TEXT,                          -- YYYY-MM accounting period; NULL = spend month
    source       TEXT    NOT NULL DEFAULT 'manual', -- voice | receipt | text | manual
    source_ref   TEXT,                          -- transcript id / file id
    created_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS settlements (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    settled_on   TEXT    NOT NULL,
    amount_minor INTEGER NOT NULL,
    currency     TEXT    NOT NULL DEFAULT 'THB',
    payer        TEXT    NOT NULL,              -- who handed over the money
    payee        TEXT    NOT NULL,              -- who received it
    note         TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS fund_contributions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    month        TEXT    NOT NULL,              -- YYYY-MM the contribution funds
    member       TEXT    NOT NULL,              -- normalised name
    amount_minor INTEGER NOT NULL,
    currency     TEXT    NOT NULL DEFAULT 'THB',
    kind         TEXT    NOT NULL DEFAULT 'contribution', -- 'contribution' | 'topup'
    note         TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL
);

"""


#: Indexes are created after migrations, because they reference columns that an
#: older database only gains during the migration step.
_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_expenses_period
    ON expenses (COALESCE(period, substr(spent_on, 1, 7)));

CREATE INDEX IF NOT EXISTS idx_fund_month
    ON fund_contributions (month);
"""

#: Columns added after the first release, applied to existing databases on open.
_MIGRATIONS = (
    ("expenses", "paid_from", "TEXT NOT NULL DEFAULT 'personal'"),
    ("expenses", "period", "TEXT"),
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """
    Create the finance tables if absent and bring an older database up to date.

    Safe to call repeatedly.  The migration step matters because a household may
    already have months of expenses recorded under the original schema; those
    rows default to ``paid_from = 'personal'``, which is exactly what they were.
    """
    conn.executescript(_SCHEMA)
    for table, column, decl in _MIGRATIONS:
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            logger.info("Migrated %s: added column %s", table, column)
    conn.executescript(_INDEXES)
    conn.commit()


# ── Helpers ───────────────────────────────────────────────────────────────────

def normalise_member(name: Optional[str]) -> str:
    """
    Normalise a person reference to a canonical household member name.

    Recognises the ways J and Farid actually get written in voice notes,
    in English, Thai and Russian.  Anything unrecognised is title-cased and
    returned as-is so an unexpected third party is never silently merged.
    """
    if not name:
        return "J"
    raw = name.strip()
    low = raw.lower()
    if low in {"j", "я", "me", "myself", "i", "sujit", "jasujitta", "เจ"}:
        return "J"
    if low in {"farid", "фарид", "ฟาริด", "f"}:
        return "Farid"
    return raw[:1].upper() + raw[1:]


def other_member(name: str, members: Iterable[str] = ("J", "Farid")) -> str:
    """Return the counterparty for *name* within a two-person household."""
    members = list(members)
    for m in members:
        if m != name:
            return m
    return members[0] if members else "Farid"


def to_minor(amount: float) -> int:
    """Convert a major-unit amount to integer minor units, rounding half up."""
    return int(round(float(amount) * 100))


def to_major(amount_minor: int) -> float:
    """Convert integer minor units back to a major-unit float."""
    return round(amount_minor / 100.0, 2)


def format_money(amount: float, currency: str = DEFAULT_CURRENCY) -> str:
    """Render an amount with its currency symbol and thousands separators."""
    symbol = CURRENCY_SYMBOL.get(currency.upper(), "")
    formatted = f"{abs(amount):,.2f}"
    sign = "-" if amount < 0 else ""
    if symbol:
        return f"{sign}{symbol}{formatted}"
    return f"{sign}{formatted} {currency.upper()}"


def guess_category(text: str) -> str:
    """Keyword-based category inference — the offline fallback for the LLM."""
    if not text:
        return "other"
    low = text.lower()
    for category, hints in _CATEGORY_HINTS.items():
        if any(hint in low for hint in hints):
            return category
    return "other"


def _normalise_share(payer_share: Any) -> float:
    """Coerce a split rule into a 0..1 payer share."""
    if payer_share is None:
        return 0.5
    if isinstance(payer_share, str):
        key = payer_share.strip().lower()
        if key in {"equal", "50/50", "half", "split", "shared"}:
            return 0.5
        if key in {"mine", "own", "personal", "self"}:
            return 1.0
        if key in {"theirs", "other", "covered", "fronted"}:
            return 0.0
        try:
            payer_share = float(key)
        except ValueError:
            return 0.5
    try:
        value = float(payer_share)
    except (TypeError, ValueError):
        return 0.5
    # A value in (1, 100] is read as a percentage, so "50" means an even split.
    # Anything above 100 is nonsense and clamps to "the payer owes it all".
    if 1.0 < value <= 100.0:
        value = value / 100.0
    return min(max(value, 0.0), 1.0)


def _coerce_date(value: Any) -> str:
    """Coerce a date-ish value to an ISO ``YYYY-MM-DD`` string."""
    if value is None or value == "":
        return date.today().isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date().isoformat()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return date.today().isoformat()


# ── Ledger writes ─────────────────────────────────────────────────────────────

def add_expense(
    conn: sqlite3.Connection,
    user_id: int,
    amount: float,
    description: str = "",
    category: Optional[str] = None,
    payer: str = "J",
    payer_share: Any = 0.5,
    currency: str = DEFAULT_CURRENCY,
    spent_on: Any = None,
    source: str = "manual",
    source_ref: Optional[str] = None,
    paid_from: str = "personal",
    period: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Record one expense and return it as a dict.

    ``payer_share`` accepts a float (0..1), a percentage (>1), or one of the
    words ``equal`` / ``mine`` / ``theirs``.

    ``period`` is the ``YYYY-MM`` the expense is *accounted* to, which is not
    always the month it was spent in — a late-July grocery run settled with the
    August fund belongs to August. Leave it None and the spend month is used.

    ``paid_from`` selects which of the household's two tracks the expense sits
    on.  ``"personal"`` means someone paid out of their own pocket and the split
    creates a debt between the two of them.  ``"fund"`` means it came out of the
    shared food fund that both members top up equally — no inter-person debt is
    created, the fund balance simply falls.  ``payer_share`` is ignored for fund
    spending and stored as 0.5 for consistency.
    """
    ensure_schema(conn)

    if amount is None:
        raise ValueError("amount is required")
    amount = abs(float(amount))
    category = (category or guess_category(description) or "other").lower()
    if category not in CATEGORIES:
        category = "other"

    payer = normalise_member(payer)
    paid_from = "fund" if str(paid_from).strip().lower() == "fund" else "personal"
    share = 0.5 if paid_from == "fund" else _normalise_share(payer_share)
    day = _coerce_date(spent_on)
    if period is not None and not re.fullmatch(r"\d{4}-\d{2}", str(period)):
        raise ValueError(f"period must be YYYY-MM, got {period!r}")
    now = datetime.now(timezone.utc).isoformat()

    cur = conn.execute(
        """INSERT INTO expenses
               (user_id, spent_on, amount_minor, currency, category, description,
                payer, payer_share, paid_from, period, source, source_ref, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id, day, to_minor(amount), currency.upper(), category,
            description.strip(), payer, share, paid_from, period, source,
            source_ref, now,
        ),
    )
    conn.commit()

    return {
        "id": cur.lastrowid,
        "spent_on": day,
        "amount": round(amount, 2),
        "currency": currency.upper(),
        "category": category,
        "description": description.strip(),
        "payer": payer,
        "payer_share": share,
        "paid_from": paid_from,
        "period": period or day[:7],
        "source": source,
        "source_ref": source_ref,
    }


def add_settlement(
    conn: sqlite3.Connection,
    user_id: int,
    amount: float,
    payer: str,
    payee: Optional[str] = None,
    currency: str = DEFAULT_CURRENCY,
    note: str = "",
    settled_on: Any = None,
) -> Dict[str, Any]:
    """Record a repayment from *payer* to *payee*, reducing the outstanding balance."""
    ensure_schema(conn)

    payer = normalise_member(payer)
    payee = normalise_member(payee) if payee else other_member(payer)
    amount = abs(float(amount))
    day = _coerce_date(settled_on)
    now = datetime.now(timezone.utc).isoformat()

    cur = conn.execute(
        """INSERT INTO settlements
               (user_id, settled_on, amount_minor, currency, payer, payee, note, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, day, to_minor(amount), currency.upper(), payer, payee, note.strip(), now),
    )
    conn.commit()

    return {
        "id": cur.lastrowid,
        "settled_on": day,
        "amount": round(amount, 2),
        "currency": currency.upper(),
        "payer": payer,
        "payee": payee,
        "note": note.strip(),
    }


def delete_expense(conn: sqlite3.Connection, expense_id: int) -> bool:
    """Remove an expense.  Returns True when a row was deleted."""
    ensure_schema(conn)
    cur = conn.execute("DELETE FROM expenses WHERE id=?", (expense_id,))
    conn.commit()
    return cur.rowcount > 0


# ── Ledger reads ──────────────────────────────────────────────────────────────

def _row_to_expense(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "spent_on": row["spent_on"],
        "amount": to_major(row["amount_minor"]),
        "currency": row["currency"],
        "category": row["category"],
        "description": row["description"],
        "payer": row["payer"],
        "payer_share": row["payer_share"],
        "paid_from": (row["paid_from"] if "paid_from" in row.keys() else "personal"),
        "period": ((row["period"] if "period" in row.keys() else None)
                   or row["spent_on"][:7]),
        "source": row["source"],
        "source_ref": row["source_ref"],
    }


def list_expenses(
    conn: sqlite3.Connection,
    user_id: Optional[int] = None,
    month: Optional[str] = None,
    category: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Return ledger rows newest-first.

    ``month`` filters on the accounting period — the expense's ``period`` when
    set, otherwise its spend month.  ``user_id`` of None returns the whole
    household ledger, which is the normal case for a shared household.
    """
    ensure_schema(conn)
    clauses: List[str] = []
    params: List[Any] = []

    if user_id is not None:
        clauses.append("user_id = ?")
        params.append(user_id)
    if month:
        clauses.append("COALESCE(period, substr(spent_on, 1, 7)) = ?")
        params.append(month)
    if category:
        clauses.append("category = ?")
        params.append(category.lower())

    sql = "SELECT * FROM expenses"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY spent_on DESC, id DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)

    return [_row_to_expense(r) for r in conn.execute(sql, params).fetchall()]


def list_settlements(
    conn: sqlite3.Connection,
    user_id: Optional[int] = None,
    month: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return settlement rows newest-first."""
    ensure_schema(conn)
    clauses: List[str] = []
    params: List[Any] = []
    if user_id is not None:
        clauses.append("user_id = ?")
        params.append(user_id)
    if month:
        clauses.append("substr(settled_on, 1, 7) = ?")
        params.append(month)

    sql = "SELECT * FROM settlements"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY settled_on DESC, id DESC"

    return [
        {
            "id": r["id"],
            "settled_on": r["settled_on"],
            "amount": to_major(r["amount_minor"]),
            "currency": r["currency"],
            "payer": r["payer"],
            "payee": r["payee"],
            "note": r["note"],
        }
        for r in conn.execute(sql, params).fetchall()
    ]


# ── Balance ───────────────────────────────────────────────────────────────────

def compute_balance(
    conn: sqlite3.Connection,
    user_id: Optional[int] = None,
    members: Tuple[str, str] = ("J", "Farid"),
    month: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Compute the net who-owes-who position, keyed by currency.

    For each expense the payer fronted the full amount but is only responsible
    for ``payer_share`` of it; the remainder is a claim against the other member.
    Settlements move money the other way.  The result for each currency is::

        {"net": {"J": +x, "Farid": -x}, "owed_by": ..., "owed_to": ..., "amount": x}

    where a positive ``net`` means that member is owed money.

    Expenses paid from the shared fund are skipped — the fund is not a person and
    spending it moves nothing between the two members.  Pass ``month`` to scope
    the answer to one ``YYYY-MM``, which is what month-end settlement needs.
    """
    ensure_schema(conn)
    a, b = members

    net_minor: Dict[str, Dict[str, int]] = defaultdict(lambda: {a: 0, b: 0})

    for exp in list_expenses(conn, user_id=user_id, month=month):
        currency = exp["currency"]
        payer = exp["payer"]
        counterparty = other_member(payer, members)
        total_minor = to_minor(exp["amount"])
        if exp.get("paid_from") == "fund":
            # The shared fund paid, not a person. It draws the fund down (see
            # fund_status) but moves nothing between the two members. Touch the
            # currency bucket so the currency still reports, then move on.
            bucket = net_minor[currency]
            bucket.setdefault(payer, 0)
            bucket.setdefault(counterparty, 0)
            continue
        # Portion of the bill the payer covered on the other person's behalf.
        claim = int(round(total_minor * (1.0 - float(exp["payer_share"]))))
        bucket = net_minor[currency]          # touch the currency even for a 0 claim
        bucket.setdefault(payer, 0)
        bucket.setdefault(counterparty, 0)
        if claim:
            bucket[payer] += claim
            bucket[counterparty] -= claim

    for stl in list_settlements(conn, user_id=user_id, month=month):
        currency = stl["currency"]
        bucket = net_minor[currency]
        bucket.setdefault(stl["payer"], 0)
        bucket.setdefault(stl["payee"], 0)
        amount = to_minor(stl["amount"])
        # Handing money over settles the payer's debt: their net rises toward
        # zero and the payee is owed that much less.
        bucket[stl["payer"]] += amount
        bucket[stl["payee"]] -= amount

    result: Dict[str, Dict[str, Any]] = {}
    for currency, bucket in net_minor.items():
        net = {name: to_major(value) for name, value in bucket.items()}
        creditor = max(bucket, key=lambda k: bucket[k])
        debtor = min(bucket, key=lambda k: bucket[k])
        amount = to_major(max(bucket[creditor], 0))
        result[currency] = {
            "net": net,
            "owed_to": creditor if amount > 0 else None,
            "owed_by": debtor if amount > 0 else None,
            "amount": amount,
        }
    return result


def format_balance(balances: Dict[str, Dict[str, Any]]) -> str:
    """Render :func:`compute_balance` output as Telegram-friendly Markdown."""
    if not balances:
        return "💰 *Balance*\n\nNo expenses recorded yet."

    lines = ["💰 *Who owes who*"]
    for currency in sorted(balances):
        info = balances[currency]
        if not info["amount"]:
            lines.append(f"\n{currency}: ✅ all square")
            continue
        lines.append(
            f"\n*{info['owed_by']} owes {info['owed_to']} "
            f"{format_money(info['amount'], currency)}*"
        )
    return "\n".join(lines)


# ── Monthly rollup ────────────────────────────────────────────────────────────

def month_key(value: Any = None) -> str:
    """Return a ``YYYY-MM`` key for *value* (defaults to today)."""
    return _coerce_date(value)[:7]


def previous_month(month: str) -> str:
    """Return the ``YYYY-MM`` key immediately before *month*."""
    year, mon = int(month[:4]), int(month[5:7])
    if mon == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{mon - 1:02d}"


def monthly_rollup(
    conn: sqlite3.Connection,
    month: Optional[str] = None,
    user_id: Optional[int] = None,
    currency: str = DEFAULT_CURRENCY,
) -> Dict[str, Any]:
    """
    Category totals for *month* with month-over-month deltas.

    Returns ``{"month", "currency", "total", "previous_total", "delta",
    "delta_pct", "categories": {cat: {"total", "previous", "delta", "delta_pct",
    "count"}}, "by_payer": {...}}``.
    """
    ensure_schema(conn)
    month = month or month_key()
    prev = previous_month(month)
    currency = currency.upper()

    def totals_for(key: str) -> Tuple[Dict[str, int], Dict[str, int], Dict[str, int]]:
        by_cat: Dict[str, int] = defaultdict(int)
        counts: Dict[str, int] = defaultdict(int)
        by_payer: Dict[str, int] = defaultdict(int)
        for exp in list_expenses(conn, user_id=user_id, month=key):
            if exp["currency"] != currency:
                continue
            minor = to_minor(exp["amount"])
            by_cat[exp["category"]] += minor
            counts[exp["category"]] += 1
            by_payer[exp["payer"]] += minor
        return by_cat, counts, by_payer

    current, counts, by_payer = totals_for(month)
    previous, _, _ = totals_for(prev)

    categories: Dict[str, Dict[str, Any]] = {}
    for cat in sorted(set(current) | set(previous)):
        cur_minor = current.get(cat, 0)
        prev_minor = previous.get(cat, 0)
        delta_minor = cur_minor - prev_minor
        categories[cat] = {
            "total": to_major(cur_minor),
            "previous": to_major(prev_minor),
            "delta": to_major(delta_minor),
            "delta_pct": round(delta_minor / prev_minor * 100, 1) if prev_minor else None,
            "count": counts.get(cat, 0),
        }

    total_minor = sum(current.values())
    prev_total_minor = sum(previous.values())
    delta_minor = total_minor - prev_total_minor

    return {
        "month": month,
        "previous_month": prev,
        "currency": currency,
        "total": to_major(total_minor),
        "previous_total": to_major(prev_total_minor),
        "delta": to_major(delta_minor),
        "delta_pct": round(delta_minor / prev_total_minor * 100, 1) if prev_total_minor else None,
        "categories": categories,
        "by_payer": {name: to_major(v) for name, v in sorted(by_payer.items())},
    }


def _trend_marker(delta: float, delta_pct: Optional[float]) -> str:
    if not delta:
        return "→ flat"
    arrow = "▲" if delta > 0 else "▼"
    if delta_pct is None:
        return f"{arrow} new"
    return f"{arrow} {abs(delta_pct):.0f}%"


def format_rollup(rollup: Dict[str, Any]) -> str:
    """Render :func:`monthly_rollup` output as Telegram-friendly Markdown."""
    currency = rollup["currency"]
    lines = [f"📊 *{rollup['month']} spending* ({currency})", ""]

    if not rollup["categories"]:
        lines.append("_No expenses recorded this month._")
        return "\n".join(lines)

    ordered = sorted(
        rollup["categories"].items(), key=lambda kv: kv[1]["total"], reverse=True
    )
    for cat, data in ordered:
        if not data["total"] and not data["previous"]:
            continue
        emoji = CATEGORY_EMOJI.get(cat, "•")
        marker = _trend_marker(data["delta"], data["delta_pct"])
        lines.append(
            f"{emoji} {cat.title():<10} {format_money(data['total'], currency):>12}"
            f"   {marker}"
        )

    lines.append("")
    lines.append(
        f"*Total {format_money(rollup['total'], currency)}*  "
        f"({_trend_marker(rollup['delta'], rollup['delta_pct'])} vs {rollup['previous_month']})"
    )

    if rollup["by_payer"]:
        lines.append("")
        lines.append("_Paid by:_ " + ", ".join(
            f"{name} {format_money(total, currency)}"
            for name, total in rollup["by_payer"].items()
        ))
    return "\n".join(lines)


def format_ledger(expenses: List[Dict[str, Any]], limit: int = 20) -> str:
    """Render the line-item ledger as Telegram-friendly Markdown."""
    if not expenses:
        return "🧾 *Ledger*\n\nNothing recorded yet."

    lines = ["🧾 *Ledger* (most recent first)", ""]
    for exp in expenses[:limit]:
        emoji = CATEGORY_EMOJI.get(exp["category"], "•")
        share = float(exp["payer_share"])
        if share == 0.5:
            split = "split"
        elif share >= 1.0:
            split = "own"
        elif share <= 0.0:
            split = "covered"
        else:
            split = f"{share:.0%}/{1 - share:.0%}"
        desc = exp["description"] or exp["category"]
        lines.append(
            f"`#{exp['id']}` {exp['spent_on'][5:]} {emoji} "
            f"{format_money(exp['amount'], exp['currency'])} — {desc}"
        )
        lines.append(f"      _{exp['payer']} paid · {split} · via {exp['source']}_")

    if len(expenses) > limit:
        lines.append(f"\n_…and {len(expenses) - limit} more._")
    return "\n".join(lines)


# ── The shared food fund ──────────────────────────────────────────────────────
#
# The household runs two tracks. Personal spending is split and creates a debt
# between the two members (everything above). Food comes out of a common fund
# both members top up equally each month; spending it draws the fund down and
# creates no debt at all. At month end the fund is reconciled: if it is
# overdrawn, both top up equally; if one member contributed less than an even
# share, they owe the difference.

def add_contribution(
    conn: sqlite3.Connection,
    user_id: int,
    amount: float,
    member: str = "J",
    month: Optional[str] = None,
    currency: str = DEFAULT_CURRENCY,
    kind: str = "contribution",
    note: str = "",
) -> Dict[str, Any]:
    """
    Record money paid into the shared fund for *month*.

    ``kind`` is ``contribution`` for the regular monthly amount, or ``topup``
    for money added to cover an overdraft. Both count identically toward the
    fund total; the distinction is for reporting.
    """
    ensure_schema(conn)
    member = normalise_member(member)
    month = month or month_key()
    amount = abs(float(amount))
    kind = "topup" if str(kind).strip().lower() == "topup" else "contribution"
    now = datetime.now(timezone.utc).isoformat()

    cur = conn.execute(
        """INSERT INTO fund_contributions
               (user_id, month, member, amount_minor, currency, kind, note, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, month, member, to_minor(amount), currency.upper(), kind,
         note.strip(), now),
    )
    conn.commit()

    return {
        "id": cur.lastrowid,
        "month": month,
        "member": member,
        "amount": round(amount, 2),
        "currency": currency.upper(),
        "kind": kind,
        "note": note.strip(),
    }


def list_contributions(
    conn: sqlite3.Connection,
    month: Optional[str] = None,
    user_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return fund contributions, newest first."""
    ensure_schema(conn)
    clauses: List[str] = []
    params: List[Any] = []
    if user_id is not None:
        clauses.append("user_id = ?")
        params.append(user_id)
    if month:
        clauses.append("month = ?")
        params.append(month)

    sql = "SELECT * FROM fund_contributions"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY month DESC, id DESC"

    return [
        {
            "id": r["id"],
            "month": r["month"],
            "member": r["member"],
            "amount": to_major(r["amount_minor"]),
            "currency": r["currency"],
            "kind": r["kind"],
            "note": r["note"],
        }
        for r in conn.execute(sql, params).fetchall()
    ]


def fund_status(
    conn: sqlite3.Connection,
    month: Optional[str] = None,
    user_id: Optional[int] = None,
    currency: str = DEFAULT_CURRENCY,
    members: Tuple[str, str] = ("J", "Farid"),
) -> Dict[str, Any]:
    """
    Report the shared fund for *month*.

    Returns::

        {"month", "currency", "contributions": {member: paid_in},
         "total_in", "spent", "balance", "overdrawn", "overdraft",
         "topup_each", "equalisation": {"owed_by", "owed_to", "amount"},
         "spend_count"}

    ``balance`` is positive when money remains and negative when the fund is
    overdrawn. ``topup_each`` is what each member adds to bring it back to zero.
    ``equalisation`` covers the separate case of one member having put in less
    than an even share of what is already there.
    """
    ensure_schema(conn)
    month = month or month_key()
    currency = currency.upper()

    contributions: Dict[str, int] = {m: 0 for m in members}
    for row in list_contributions(conn, month=month, user_id=user_id):
        if row["currency"] != currency:
            continue
        contributions.setdefault(row["member"], 0)
        contributions[row["member"]] += to_minor(row["amount"])

    spent_minor = 0
    spend_count = 0
    for exp in list_expenses(conn, user_id=user_id, month=month):
        if exp["currency"] != currency or exp.get("paid_from") != "fund":
            continue
        spent_minor += to_minor(exp["amount"])
        spend_count += 1

    total_in = sum(contributions.values())
    balance_minor = total_in - spent_minor
    overdraft_minor = max(-balance_minor, 0)
    n = max(len(contributions), 1)

    # Contribution imbalance: who put in less than an even share of the total.
    even = total_in / n
    creditor = max(contributions, key=lambda k: contributions[k])
    debtor = min(contributions, key=lambda k: contributions[k])
    gap_minor = int(round(contributions[creditor] - even))
    equalisation = {
        "owed_by": debtor if gap_minor > 0 else None,
        "owed_to": creditor if gap_minor > 0 else None,
        "amount": to_major(max(gap_minor, 0)),
    }

    return {
        "month": month,
        "currency": currency,
        "contributions": {m: to_major(v) for m, v in sorted(contributions.items())},
        "total_in": to_major(total_in),
        "spent": to_major(spent_minor),
        "spend_count": spend_count,
        "balance": to_major(balance_minor),
        "overdrawn": balance_minor < 0,
        "overdraft": to_major(overdraft_minor),
        "topup_each": to_major(int(round(overdraft_minor / n))) if overdraft_minor else 0.0,
        "equalisation": equalisation,
    }


def format_fund(status: Dict[str, Any]) -> str:
    """Render :func:`fund_status` for Telegram."""
    cur = status["currency"]
    lines = [f"🍜 *Food fund — {status['month']}*", ""]

    for member, paid in status["contributions"].items():
        lines.append(f"  {member} put in {format_money(paid, cur)}")
    lines.append(f"  *Fund {format_money(status['total_in'], cur)}*")
    lines.append("")
    lines.append(f"  Spent {format_money(status['spent'], cur)} "
                 f"over {status['spend_count']} purchase(s)")

    if status["overdrawn"]:
        lines.append(f"  *Overdrawn {format_money(status['overdraft'], cur)}*")
        lines.append("")
        lines.append(f"→ Each tops up {format_money(status['topup_each'], cur)}")
    else:
        lines.append(f"  *{format_money(status['balance'], cur)} left*")

    eq = status["equalisation"]
    if eq["amount"]:
        lines.append(f"→ {eq['owed_by']} is {format_money(eq['amount'], cur)} "
                     f"behind {eq['owed_to']} on contributions")

    return "\n".join(lines)


# ── Month-end settlement ──────────────────────────────────────────────────────

def month_settlement(
    conn: sqlite3.Connection,
    month: Optional[str] = None,
    user_id: Optional[int] = None,
    currency: str = DEFAULT_CURRENCY,
    members: Tuple[str, str] = ("J", "Farid"),
) -> Dict[str, Any]:
    """
    Close out a month across both tracks.

    Combines the fund reconciliation (top-up each, plus any contribution
    imbalance) with the personal-spending net for the same month, and states the
    single transfer that squares the two members up.

    Returns ``{"month", "currency", "fund", "personal", "transfer"}`` where
    ``transfer`` is ``{"owed_by", "owed_to", "amount"}`` — the net of the
    equalisation and the personal balance. The fund top-up is deliberately kept
    out of ``transfer``: both members pay it into the fund, not to each other.
    """
    month = month or month_key()
    currency = currency.upper()

    fund = fund_status(conn, month=month, user_id=user_id, currency=currency,
                       members=members)
    balances = compute_balance(conn, user_id=user_id, members=members, month=month)
    personal = balances.get(currency, {"net": {m: 0.0 for m in members},
                                       "owed_by": None, "owed_to": None,
                                       "amount": 0.0})

    # Net the two person-to-person amounts, which may point opposite ways.
    net: Dict[str, int] = {m: 0 for m in members}
    for member, value in personal["net"].items():
        net.setdefault(member, 0)
        net[member] += to_minor(value)

    eq = fund["equalisation"]
    if eq["amount"] and eq["owed_by"] and eq["owed_to"]:
        amount = to_minor(eq["amount"])
        net.setdefault(eq["owed_to"], 0)
        net.setdefault(eq["owed_by"], 0)
        net[eq["owed_to"]] += amount
        net[eq["owed_by"]] -= amount

    creditor = max(net, key=lambda k: net[k])
    debtor = min(net, key=lambda k: net[k])
    amount = to_major(max(net[creditor], 0))

    return {
        "month": month,
        "currency": currency,
        "fund": fund,
        "personal": personal,
        "transfer": {
            "owed_by": debtor if amount else None,
            "owed_to": creditor if amount else None,
            "amount": amount,
        },
    }


def format_settlement(settlement: Dict[str, Any]) -> str:
    """Render :func:`month_settlement` as the month-end instruction."""
    cur = settlement["currency"]
    fund = settlement["fund"]
    transfer = settlement["transfer"]

    lines = [f"🧮 *Settling {settlement['month']}*", ""]

    lines.append("*Food fund*")
    lines.append(f"  {format_money(fund['spent'], cur)} spent against "
                 f"{format_money(fund['total_in'], cur)} in")
    if fund["overdrawn"]:
        lines.append(f"  → each tops up *{format_money(fund['topup_each'], cur)}*")
    else:
        lines.append(f"  → {format_money(fund['balance'], cur)} carries over")

    lines.append("")
    lines.append("*Personal spending*")
    personal = settlement["personal"]
    if personal["amount"]:
        lines.append(f"  {personal['owed_by']} owes {personal['owed_to']} "
                     f"{format_money(personal['amount'], cur)}")
    else:
        lines.append("  nothing outstanding")

    lines.append("")
    if transfer["amount"]:
        lines.append(f"*→ {transfer['owed_by']} pays {transfer['owed_to']} "
                     f"{format_money(transfer['amount'], cur)}*")
    else:
        lines.append("*→ All square between you*")

    if fund["overdrawn"]:
        lines.append(f"_Plus {format_money(fund['topup_each'], cur)} each into the fund._")

    return "\n".join(lines)
