"""
Tests for the two-track household model: a shared food fund alongside
personal spending that splits.

The final test reproduces the household's real August 2026 figures from the
"J & Farid — Monthly Finances (MASTER)" sheet, end to end.
"""

import os
import sqlite3
import tempfile

import pytest

from assistant import finance


@pytest.fixture()
def conn():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    finance.ensure_schema(connection)
    yield connection
    connection.close()
    os.unlink(path)


# ── Migration ─────────────────────────────────────────────────────────────────

def test_ensure_schema_migrates_a_pre_fund_database():
    """An existing ledger must gain paid_from without losing its rows."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    old = sqlite3.connect(path)
    old.row_factory = sqlite3.Row
    old.executescript("""
        CREATE TABLE expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            spent_on TEXT NOT NULL, amount_minor INTEGER NOT NULL,
            currency TEXT NOT NULL DEFAULT 'THB', category TEXT NOT NULL DEFAULT 'other',
            description TEXT NOT NULL DEFAULT '', payer TEXT NOT NULL,
            payer_share REAL NOT NULL DEFAULT 0.5,
            source TEXT NOT NULL DEFAULT 'manual', source_ref TEXT,
            created_at TEXT NOT NULL);
    """)
    old.execute(
        "INSERT INTO expenses (user_id, spent_on, amount_minor, payer, created_at) "
        "VALUES (1, '2026-07-01', 100000, 'J', '2026-07-01T00:00:00Z')"
    )
    old.commit()

    finance.ensure_schema(old)

    rows = finance.list_expenses(old)
    assert len(rows) == 1
    assert rows[0]["amount"] == 1000.0
    assert rows[0]["paid_from"] == "personal"   # pre-existing rows were personal
    old.close()
    os.unlink(path)


# ── Contributions ─────────────────────────────────────────────────────────────

def test_add_contribution_normalises_member_and_month(conn):
    c = finance.add_contribution(conn, 1, 4000, member="фарид", month="2026-08")
    assert c["member"] == "Farid"
    assert c["amount"] == 4000.0
    assert c["kind"] == "contribution"


def test_topup_counts_toward_the_fund_but_is_labelled(conn):
    finance.add_contribution(conn, 1, 4000, member="J", month="2026-08")
    finance.add_contribution(conn, 1, 440, member="J", month="2026-08", kind="topup")
    status = finance.fund_status(conn, month="2026-08")
    assert status["contributions"]["J"] == 4440.0
    kinds = {c["kind"] for c in finance.list_contributions(conn, month="2026-08")}
    assert kinds == {"contribution", "topup"}


def test_contributions_are_scoped_to_their_month(conn):
    finance.add_contribution(conn, 1, 3000, member="J", month="2026-06")
    finance.add_contribution(conn, 1, 4000, member="J", month="2026-08")
    assert finance.fund_status(conn, month="2026-06")["total_in"] == 3000.0
    assert finance.fund_status(conn, month="2026-08")["total_in"] == 4000.0


# ── Fund spending ─────────────────────────────────────────────────────────────

def test_fund_spending_draws_the_fund_down(conn):
    finance.add_contribution(conn, 1, 4000, member="J", month="2026-08")
    finance.add_contribution(conn, 1, 4000, member="Farid", month="2026-08")
    finance.add_expense(conn, 1, 2231, "GrabMart", category="food",
                        payer="J", paid_from="fund", spent_on="2026-08-04")

    status = finance.fund_status(conn, month="2026-08")
    assert status["total_in"] == 8000.0
    assert status["spent"] == 2231.0
    assert status["balance"] == 5769.0
    assert status["overdrawn"] is False
    assert status["topup_each"] == 0.0


def test_fund_spending_creates_no_debt_between_members(conn):
    """The whole point of the fund: J buying groceries with it owes nobody."""
    finance.add_contribution(conn, 1, 4000, member="J", month="2026-08")
    finance.add_contribution(conn, 1, 4000, member="Farid", month="2026-08")
    finance.add_expense(conn, 1, 2231, "GrabMart", category="food",
                        payer="J", paid_from="fund", spent_on="2026-08-04")

    balance = finance.compute_balance(conn)["THB"]
    assert balance["amount"] == 0.0
    assert balance["owed_by"] is None


def test_personal_spending_still_creates_debt_alongside_the_fund(conn):
    finance.add_contribution(conn, 1, 4000, member="J", month="2026-08")
    finance.add_contribution(conn, 1, 4000, member="Farid", month="2026-08")
    finance.add_expense(conn, 1, 2231, "GrabMart", category="food",
                        payer="J", paid_from="fund", spent_on="2026-08-04")
    finance.add_expense(conn, 1, 848.86, "3BB internet", category="utilities",
                        payer="J", paid_from="personal", spent_on="2026-08-12")

    balance = finance.compute_balance(conn)["THB"]
    assert balance["owed_by"] == "Farid"
    assert balance["amount"] == 424.43


def test_fund_expense_ignores_payer_share(conn):
    """A fund purchase cannot be turned into a debt by passing a split."""
    exp = finance.add_expense(conn, 1, 500, "coffee", paid_from="fund",
                              payer="J", payer_share="theirs")
    assert exp["payer_share"] == 0.5
    assert exp["paid_from"] == "fund"
    assert finance.compute_balance(conn)["THB"]["amount"] == 0.0


def test_overdraft_splits_evenly(conn):
    finance.add_contribution(conn, 1, 4000, member="J", month="2026-08")
    finance.add_contribution(conn, 1, 4000, member="Farid", month="2026-08")
    finance.add_expense(conn, 1, 8880, "August food", category="food",
                        paid_from="fund", spent_on="2026-08-15")

    status = finance.fund_status(conn, month="2026-08")
    assert status["overdrawn"] is True
    assert status["overdraft"] == 880.0
    assert status["topup_each"] == 440.0


def test_unequal_contributions_produce_an_equalisation(conn):
    finance.add_contribution(conn, 1, 5000, member="J", month="2026-08")
    finance.add_contribution(conn, 1, 3000, member="Farid", month="2026-08")
    eq = finance.fund_status(conn, month="2026-08")["equalisation"]
    assert eq["owed_by"] == "Farid"
    assert eq["owed_to"] == "J"
    assert eq["amount"] == 1000.0


def test_equal_contributions_need_no_equalisation(conn):
    finance.add_contribution(conn, 1, 4000, member="J", month="2026-08")
    finance.add_contribution(conn, 1, 4000, member="Farid", month="2026-08")
    assert finance.fund_status(conn, month="2026-08")["equalisation"]["amount"] == 0.0


def test_fund_ignores_other_currencies(conn):
    finance.add_contribution(conn, 1, 4000, member="J", month="2026-08")
    finance.add_contribution(conn, 1, 100, member="J", month="2026-08", currency="USD")
    assert finance.fund_status(conn, month="2026-08", currency="THB")["total_in"] == 4000.0
    assert finance.fund_status(conn, month="2026-08", currency="USD")["total_in"] == 100.0


# ── Settlement ────────────────────────────────────────────────────────────────

def test_settlement_keeps_the_topup_out_of_the_transfer(conn):
    """Both members pay the top-up into the fund, not to each other."""
    finance.add_contribution(conn, 1, 4000, member="J", month="2026-08")
    finance.add_contribution(conn, 1, 4000, member="Farid", month="2026-08")
    finance.add_expense(conn, 1, 8880, "food", category="food",
                        paid_from="fund", spent_on="2026-08-15")

    s = finance.month_settlement(conn, month="2026-08")
    assert s["fund"]["topup_each"] == 440.0
    assert s["transfer"]["amount"] == 0.0


def test_settlement_nets_equalisation_against_personal_spending(conn):
    # Farid is 1000 behind on the fund, but J owes Farid 600 personally.
    finance.add_contribution(conn, 1, 5000, member="J", month="2026-08")
    finance.add_contribution(conn, 1, 3000, member="Farid", month="2026-08")
    finance.add_expense(conn, 1, 1200, "shared gift", category="shared",
                        payer="Farid", spent_on="2026-08-10")

    s = finance.month_settlement(conn, month="2026-08")
    assert s["transfer"]["owed_by"] == "Farid"
    assert s["transfer"]["owed_to"] == "J"
    assert s["transfer"]["amount"] == 400.0   # 1000 equalisation − 600 personal


def test_settlement_is_scoped_to_its_month(conn):
    finance.add_expense(conn, 1, 1000, "July thing", payer="J", spent_on="2026-07-05")
    finance.add_expense(conn, 1, 200, "Aug thing", payer="J", spent_on="2026-08-05")
    assert finance.month_settlement(conn, month="2026-08")["transfer"]["amount"] == 100.0
    assert finance.month_settlement(conn, month="2026-07")["transfer"]["amount"] == 500.0


# ── The real August 2026 numbers ──────────────────────────────────────────────

AUGUST_FOOD = [
    ("2026-07-21", 1036.00, "GrabMart — Tops Food Hall Samui"),
    ("2026-07-30", 1069.00, "7-Eleven Chaweng Noi — food & household"),
    ("2026-08-03", 1588.00, "Uvelka buckwheat 800g x5 (Shopee COD)"),
    ("2026-08-03", 2231.00, "GrabMart — Tops Food Hall Samui"),
    ("2026-08-07",  723.00, "Cafe Amazon PTT Chaweng — drip coffee x6"),
    ("2026-08-17",  327.00, "McDonald's — Chaweng"),
    ("2026-08-17",  874.00, "Beef Garden — Bophut"),
    ("2026-08-18",  337.00, "McDonald's — Chaweng"),
    ("2026-08-18",  695.00, "GrabMart"),
]

AUGUST_NON_FOOD = [
    ("2026-07-29", 232.00, "Shopee — socks 12 pairs", "other"),
    ("2026-08-12", 848.86, "3BB Triple T Broadband (SCB bill)", "utilities"),
]


@pytest.fixture()
def august(conn):
    """The household's real August 2026, as recorded in the MASTER sheet."""
    finance.add_contribution(conn, 1, 4000, member="J", month="2026-08")
    finance.add_contribution(conn, 1, 4000, member="Farid", month="2026-08")
    # Three of these were spent in late July but the household accounts for
    # them in August — that is what `period` is for.
    for date, amount, desc in AUGUST_FOOD:
        finance.add_expense(conn, 1, amount, desc, category="food", payer="J",
                            paid_from="fund", spent_on=date, period="2026-08",
                            source="receipt")
    for date, amount, desc, cat in AUGUST_NON_FOOD:
        finance.add_expense(conn, 1, amount, desc, category=cat, payer="J",
                            paid_from="personal", payer_share="equal",
                            spent_on=date, period="2026-08")
    return conn


def test_real_august_food_fund(august):
    """MASTER: ฿8,880 spent against a ฿8,000 fund → top up ฿440 each."""
    status = finance.fund_status(august, month="2026-08")
    assert status["total_in"] == 8000.00
    assert status["spent"] == 8880.00
    assert status["balance"] == -880.00
    assert status["overdrawn"] is True
    assert status["topup_each"] == 440.00
    assert status["spend_count"] == 9


def test_real_august_non_food_balance(august):
    """MASTER: non-food ฿1,080.86, both paid by J → Farid owes J ฿540.43."""
    balance = finance.compute_balance(august, month="2026-08")["THB"]
    assert balance["owed_by"] == "Farid"
    assert balance["owed_to"] == "J"
    assert balance["amount"] == 540.43


def test_real_august_settlement(august):
    s = finance.month_settlement(august, month="2026-08")
    assert s["fund"]["topup_each"] == 440.00
    assert s["transfer"]["owed_by"] == "Farid"
    assert s["transfer"]["amount"] == 540.43

    out = finance.format_settlement(s)
    assert "฿440.00" in out
    assert "Farid pays J ฿540.43" in out


def test_real_august_rollup_separates_the_two_tracks(august):
    """Food is fund money; the ฿880 overdraft is not a debt between them."""
    rollup = finance.monthly_rollup(august, month="2026-08")
    assert rollup["total"] == 9960.86        # everything the household spent
    assert rollup["categories"]["food"]["total"] == 8880.00
    assert rollup["categories"]["utilities"]["total"] == 848.86
    assert rollup["categories"]["other"]["total"] == 232.00


def test_real_august_fund_formats(august):
    out = finance.format_fund(finance.fund_status(august, month="2026-08"))
    assert "Overdrawn ฿880.00" in out
    assert "Each tops up ฿440.00" in out
