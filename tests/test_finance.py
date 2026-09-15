"""Tests for the shared household ledger."""

import os
import sqlite3
import tempfile
from datetime import date

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


# ── Helpers ───────────────────────────────────────────────────────────────────

def test_normalise_member_recognises_aliases():
    assert finance.normalise_member("я") == "J"
    assert finance.normalise_member("me") == "J"
    assert finance.normalise_member("Фарид") == "Farid"
    assert finance.normalise_member("ฟาริด") == "Farid"
    assert finance.normalise_member(None) == "J"
    # An unknown third party is preserved, not merged into a household member.
    assert finance.normalise_member("pkorn") == "Pkorn"


def test_other_member():
    assert finance.other_member("J") == "Farid"
    assert finance.other_member("Farid") == "J"


def test_minor_unit_roundtrip_avoids_float_drift():
    assert finance.to_minor(0.1) + finance.to_minor(0.2) == finance.to_minor(0.3)
    assert finance.to_major(finance.to_minor(1234.56)) == 1234.56


def test_format_money_uses_symbols_and_separators():
    assert finance.format_money(18000, "THB") == "฿18,000.00"
    assert finance.format_money(-250.5, "THB") == "-฿250.50"
    assert "JPY" in finance.format_money(100, "JPY")


def test_guess_category_across_languages():
    assert finance.guess_category("Makro grocery run") == "food"
    assert finance.guess_category("ค่าเช่าบ้านเดือนนี้") == "rent"
    assert finance.guess_category("бензин для байка") == "transport"
    assert finance.guess_category("ค่าไฟ") == "utilities"
    assert finance.guess_category("something inscrutable") == "other"


@pytest.mark.parametrize("given,expected", [
    ("equal", 0.5), ("mine", 1.0), ("theirs", 0.0),
    (0.25, 0.25), (50, 0.5), (100, 1.0), (150, 1.0),
    (None, 0.5), ("nonsense", 0.5),
])
def test_share_normalisation(given, expected):
    assert finance._normalise_share(given) == expected


# ── Ledger ────────────────────────────────────────────────────────────────────

def test_add_expense_infers_category_and_returns_row(conn):
    exp = finance.add_expense(
        conn, 1, amount=1250.50, description="Makro grocery run", payer="Farid"
    )
    assert exp["category"] == "food"
    assert exp["amount"] == 1250.50
    assert exp["payer"] == "Farid"
    assert exp["currency"] == "THB"
    assert exp["spent_on"] == date.today().isoformat()


def test_unknown_category_falls_back_to_other(conn):
    exp = finance.add_expense(conn, 1, amount=100, category="yacht", description="x")
    assert exp["category"] == "other"


def test_list_expenses_filters_by_month_and_category(conn):
    finance.add_expense(conn, 1, 100, "old rent", category="rent", spent_on="2026-06-01")
    finance.add_expense(conn, 1, 200, "new rent", category="rent", spent_on="2026-07-01")
    finance.add_expense(conn, 1, 50, "noodles", category="food", spent_on="2026-07-02")

    assert len(finance.list_expenses(conn, month="2026-07")) == 2
    assert len(finance.list_expenses(conn, month="2026-07", category="rent")) == 1
    assert len(finance.list_expenses(conn)) == 3


def test_delete_expense(conn):
    exp = finance.add_expense(conn, 1, 100, "typo")
    assert finance.delete_expense(conn, exp["id"]) is True
    assert finance.delete_expense(conn, exp["id"]) is False
    assert finance.list_expenses(conn) == []


# ── Balance ───────────────────────────────────────────────────────────────────

def test_equal_split_creates_half_the_debt(conn):
    finance.add_expense(conn, 1, 1000, "dinner", payer="J", payer_share="equal")
    balance = finance.compute_balance(conn)["THB"]
    assert balance["owed_by"] == "Farid"
    assert balance["owed_to"] == "J"
    assert balance["amount"] == 500.0


def test_personal_expense_creates_no_debt(conn):
    finance.add_expense(conn, 1, 800, "my haircut", payer="J", payer_share="mine")
    balance = finance.compute_balance(conn)["THB"]
    assert balance["amount"] == 0.0
    assert balance["owed_by"] is None


def test_fronting_the_whole_bill_creates_full_debt(conn):
    finance.add_expense(conn, 1, 600, "Farid's visa run", payer="J", payer_share="theirs")
    balance = finance.compute_balance(conn)["THB"]
    assert balance["owed_by"] == "Farid"
    assert balance["amount"] == 600.0


def test_opposing_expenses_net_out(conn):
    finance.add_expense(conn, 1, 1000, "rent", payer="J", payer_share="equal")
    finance.add_expense(conn, 1, 600, "groceries", payer="Farid", payer_share="equal")
    balance = finance.compute_balance(conn)["THB"]
    # J is owed 500, Farid is owed 300 → J is owed 200 net.
    assert balance["owed_to"] == "J"
    assert balance["amount"] == 200.0
    assert balance["net"]["J"] == 200.0
    assert balance["net"]["Farid"] == -200.0


def test_settlement_reduces_the_balance(conn):
    finance.add_expense(conn, 1, 1000, "rent", payer="J", payer_share="equal")
    finance.add_settlement(conn, 1, 300, payer="Farid")
    balance = finance.compute_balance(conn)["THB"]
    assert balance["owed_by"] == "Farid"
    assert balance["amount"] == 200.0


def test_full_settlement_clears_the_balance(conn):
    finance.add_expense(conn, 1, 1000, "rent", payer="J", payer_share="equal")
    finance.add_settlement(conn, 1, 500, payer="Farid")
    balance = finance.compute_balance(conn)["THB"]
    assert balance["amount"] == 0.0


def test_currencies_never_mix(conn):
    finance.add_expense(conn, 1, 1000, "rent", payer="J", currency="THB")
    finance.add_expense(conn, 1, 100, "flight", payer="Farid", currency="USD")
    balances = finance.compute_balance(conn)
    assert set(balances) == {"THB", "USD"}
    assert balances["THB"]["owed_to"] == "J"
    assert balances["USD"]["owed_to"] == "Farid"


def test_settlement_defaults_payee_to_the_counterparty(conn):
    stl = finance.add_settlement(conn, 1, 250, payer="Farid")
    assert stl["payee"] == "J"


# ── Rollup ────────────────────────────────────────────────────────────────────

def test_previous_month_wraps_the_year():
    assert finance.previous_month("2026-01") == "2025-12"
    assert finance.previous_month("2026-08") == "2026-07"


def test_monthly_rollup_computes_trend(conn):
    finance.add_expense(conn, 1, 1000, "rent", category="rent", spent_on="2026-07-01")
    finance.add_expense(conn, 1, 1500, "rent", category="rent", spent_on="2026-08-01")
    finance.add_expense(conn, 1, 200, "noodles", category="food", spent_on="2026-08-03")

    rollup = finance.monthly_rollup(conn, month="2026-08")
    assert rollup["total"] == 1700.0
    assert rollup["previous_total"] == 1000.0
    assert rollup["delta"] == 700.0
    assert rollup["delta_pct"] == 70.0

    rent = rollup["categories"]["rent"]
    assert rent["total"] == 1500.0
    assert rent["delta"] == 500.0
    assert rent["delta_pct"] == 50.0
    assert rent["count"] == 1

    # A brand-new category has no percentage to compare against.
    assert rollup["categories"]["food"]["delta_pct"] is None


def test_rollup_splits_by_payer(conn):
    finance.add_expense(conn, 1, 900, "rent", payer="J", spent_on="2026-08-01")
    finance.add_expense(conn, 1, 100, "food", payer="Farid", spent_on="2026-08-02")
    rollup = finance.monthly_rollup(conn, month="2026-08")
    assert rollup["by_payer"] == {"Farid": 100.0, "J": 900.0}


def test_rollup_ignores_other_currencies(conn):
    finance.add_expense(conn, 1, 1000, "rent", currency="THB", spent_on="2026-08-01")
    finance.add_expense(conn, 1, 50, "flight", currency="USD", spent_on="2026-08-01")
    assert finance.monthly_rollup(conn, month="2026-08", currency="THB")["total"] == 1000.0
    assert finance.monthly_rollup(conn, month="2026-08", currency="USD")["total"] == 50.0


# ── Formatting ────────────────────────────────────────────────────────────────

def test_format_balance_states_direction(conn):
    finance.add_expense(conn, 1, 1000, "rent", payer="J")
    out = finance.format_balance(finance.compute_balance(conn))
    assert "Farid owes J" in out
    assert "฿500.00" in out


def test_format_balance_empty():
    assert "No expenses" in finance.format_balance({})


def test_format_rollup_and_ledger_render(conn):
    finance.add_expense(conn, 1, 1000, "rent", category="rent", spent_on="2026-08-01")
    rollup_text = finance.format_rollup(finance.monthly_rollup(conn, month="2026-08"))
    assert "2026-08" in rollup_text and "Rent" in rollup_text

    ledger_text = finance.format_ledger(finance.list_expenses(conn))
    assert "rent" in ledger_text and "฿1,000.00" in ledger_text
