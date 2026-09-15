"""Tests for the secretary pipeline (offline / no-API-key path)."""

import os
import tempfile
from datetime import date

import pytest

from assistant import finance, secretary, storage


@pytest.fixture()
def conn(monkeypatch):
    # Force the offline path so tests never touch the network.
    monkeypatch.setattr(secretary.config, "OPENAI_API_KEY", "", raising=False)
    monkeypatch.setattr(secretary, "_openai_client", None, raising=False)

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    connection = storage.init_db(path)
    secretary.ensure_schema(connection)
    yield connection
    connection.close()
    os.unlink(path)


TODAY = date(2026, 8, 24)


# ── Extraction fallback ───────────────────────────────────────────────────────

def test_extract_returns_full_shape_for_empty_input():
    data = secretary.extract("", today=TODAY)
    assert set(data) == {"summary", "language", "events", "expenses",
                         "commitments", "notes", "questions"}
    assert data["events"] == [] and data["expenses"] == []


def test_fallback_extracts_a_spend_amount():
    data = secretary.extract("I paid 1,250 baht for groceries at Makro", today=TODAY)
    assert len(data["expenses"]) == 1
    expense = data["expenses"][0]
    assert expense["amount"] == 1250.0
    assert expense["currency"] == "THB"
    assert expense["category"] == "food"


def test_fallback_reads_thousands_shorthand():
    data = secretary.extract("paid 18k for rent this month", today=TODAY)
    assert data["expenses"][0]["amount"] == 18000.0
    assert data["expenses"][0]["category"] == "rent"


def test_fallback_ignores_amounts_with_no_spend_verb():
    data = secretary.extract("the villa has 3 bedrooms and 2 bathrooms", today=TODAY)
    assert data["expenses"] == []


def test_fallback_detects_language_by_script():
    assert secretary.extract("จ่ายค่าเช่า 18000 บาท", today=TODAY)["language"] == "th"
    assert secretary.extract("заплатил за аренду", today=TODAY)["language"] == "ru"
    assert secretary.extract("paid the rent", today=TODAY)["language"] == "en"


def test_fallback_picks_up_commitments_and_questions():
    data = secretary.extract(
        "Don't forget to call the landlord. Are you free on Friday?", today=TODAY
    )
    assert any("landlord" in c["what"] for c in data["commitments"])
    assert any(q.endswith("?") for q in data["questions"])


def test_fallback_files_plain_context_as_a_note():
    data = secretary.extract("The neighbours repainted their gate.", today=TODAY)
    assert data["notes"]


# ── Filing ────────────────────────────────────────────────────────────────────

def test_file_note_persists_a_log_entry(conn):
    receipt = secretary.file_note(
        conn, 1, "Just checking in about the villa.", speaker="Farid", today=TODAY
    )
    assert receipt["log_id"]
    entries = secretary.get_log(conn)
    assert len(entries) == 1
    assert entries[0]["speaker"] == "Farid"
    assert entries[0]["logged_on"] == "2026-08-24"


def test_file_note_writes_expenses_to_the_ledger(conn):
    extraction = {
        "summary": "Farid paid the rent.",
        "language": "en",
        "events": [],
        "expenses": [{
            "amount": 18000, "currency": "THB", "category": "rent",
            "description": "August rent", "payer": "Farid",
            "split": "equal", "spent_on": "2026-08-01",
        }],
        "commitments": [],
        "notes": [],
        "questions": [],
    }
    receipt = secretary.file_note(
        conn, 1, "rent paid", speaker="Farid", extraction=extraction, today=TODAY
    )
    assert len(receipt["expenses"]) == 1

    ledger = finance.list_expenses(conn)
    assert ledger[0]["amount"] == 18000.0
    assert ledger[0]["payer"] == "Farid"
    assert ledger[0]["spent_on"] == "2026-08-01"

    balance = finance.compute_balance(conn)["THB"]
    assert balance["owed_by"] == "J"
    assert balance["amount"] == 9000.0


def test_file_note_creates_calendar_events(conn):
    extraction = {
        "summary": "Meeting P'Korn at the resort.",
        "language": "en",
        "events": [{
            "title": "Site visit with P'Korn", "start": "2026-08-26T10:00",
            "end": "2026-08-26T11:30", "location": "B52 Beach Resort",
            "notes": None, "all_day": False,
        }],
        "expenses": [], "commitments": [], "notes": [], "questions": [],
    }
    receipt = secretary.file_note(
        conn, 1, "site visit", speaker="Farid", extraction=extraction, today=TODAY
    )
    assert len(receipt["events"]) == 1
    event = receipt["events"][0]
    assert event["title"] == "Site visit with P'Korn"
    assert event["location"] == "B52 Beach Resort"
    assert event["id"]


def test_event_without_a_start_time_is_skipped(conn):
    extraction = {
        "summary": "vague", "language": "en",
        "events": [{"title": "someday", "start": None}],
        "expenses": [], "commitments": [], "notes": [], "questions": [],
    }
    receipt = secretary.file_note(conn, 1, "x", extraction=extraction, today=TODAY)
    assert receipt["events"] == []


def test_commitments_become_tasks(conn):
    extraction = {
        "summary": "todo", "language": "en", "events": [], "expenses": [],
        "commitments": [{"who": "J", "what": "Renew the bike insurance",
                         "due": "2026-09-01", "priority": "high"}],
        "notes": [], "questions": [],
    }
    receipt = secretary.file_note(conn, 1, "x", extraction=extraction, today=TODAY)
    assert len(receipt["commitments"]) == 1

    tasks = storage.get_tasks(conn, 1)
    assert any(t["title"] == "Renew the bike insurance" for t in tasks)
    task = next(t for t in tasks if t["title"] == "Renew the bike insurance")
    assert task["priority"] == 1
    assert task["deadline"] == "2026-09-01"


def test_malformed_extraction_entries_do_not_crash_filing(conn):
    extraction = {
        "summary": "messy", "language": "en",
        "events": ["not a dict", {"title": "", "start": "2026-08-26T10:00"}],
        "expenses": ["nope", {"amount": None}, {"amount": "abc"}],
        "commitments": [None, {"what": "   "}],
        "notes": [], "questions": [],
    }
    receipt = secretary.file_note(conn, 1, "x", extraction=extraction, today=TODAY)
    assert receipt["events"] == []
    assert receipt["expenses"] == []
    assert receipt["commitments"] == []
    assert receipt["log_id"]


def test_get_log_filters_by_date(conn):
    secretary.file_note(conn, 1, "old", today=date(2026, 8, 1))
    secretary.file_note(conn, 1, "new", today=date(2026, 8, 24))
    assert len(secretary.get_log(conn, since=date(2026, 8, 10))) == 1
    assert len(secretary.get_log(conn)) == 2


# ── Formatting ────────────────────────────────────────────────────────────────

def test_format_intake_lists_everything_filed(conn):
    extraction = {
        "summary": "Farid paid rent and booked a viewing.",
        "language": "en",
        "events": [{"title": "Villa viewing", "start": "2026-08-26T10:00",
                    "end": "2026-08-26T11:00", "location": "Bophut"}],
        "expenses": [{"amount": 18000, "currency": "THB", "category": "rent",
                      "description": "August rent", "payer": "Farid",
                      "split": "equal", "spent_on": "2026-08-01"}],
        "commitments": [{"who": "J", "what": "Send the contract", "due": None,
                         "priority": "medium"}],
        "notes": ["Landlord prefers cash"],
        "questions": ["Can you make Thursday?"],
    }
    receipt = secretary.file_note(conn, 1, "x", extraction=extraction, today=TODAY)
    out = secretary.format_intake(receipt, "Farid")

    assert "Villa viewing" in out
    assert "฿18,000.00" in out
    assert "Send the contract" in out
    assert "Can you make Thursday?" in out
    assert "Landlord prefers cash" in out


def test_format_intake_handles_a_note_with_nothing_actionable(conn):
    extraction = {"summary": "chit chat", "language": "en", "events": [],
                  "expenses": [], "commitments": [], "notes": [], "questions": []}
    receipt = secretary.file_note(conn, 1, "hi", extraction=extraction, today=TODAY)
    assert "Nothing actionable" in secretary.format_intake(receipt)


def test_format_log_groups_by_day(conn):
    secretary.file_note(conn, 1, "first note", speaker="Farid", today=date(2026, 8, 23))
    secretary.file_note(conn, 1, "second note", speaker="J", today=date(2026, 8, 24))
    out = secretary.format_log(secretary.get_log(conn))
    assert "2026-08-24" in out and "2026-08-23" in out
    assert "Farid" in out and "J" in out


def test_format_log_empty():
    assert "Nothing logged yet" in secretary.format_log([])


def test_food_defaults_to_the_shared_fund_and_creates_no_debt(conn):
    """Groceries come out of the common fund, so nobody owes anybody."""
    extraction = {
        "summary": "Groceries.", "language": "en", "events": [],
        "expenses": [{"amount": 1200, "currency": "THB", "category": "food",
                      "description": "Makro", "payer": "Farid", "split": "equal",
                      "spent_on": "2026-08-20"}],
        "commitments": [], "notes": [], "questions": [],
    }
    receipt = secretary.file_note(conn, 1, "x", extraction=extraction,
                                  today=date(2026, 8, 20))
    assert receipt["expenses"][0]["paid_from"] == "fund"
    assert finance.compute_balance(conn)["THB"]["amount"] == 0.0
    assert "from the food fund" in secretary.format_intake(receipt)


def test_non_food_stays_personal_and_creates_debt(conn):
    extraction = {
        "summary": "Internet bill.", "language": "en", "events": [],
        "expenses": [{"amount": 848.86, "currency": "THB", "category": "utilities",
                      "description": "3BB", "payer": "J", "split": "equal",
                      "spent_on": "2026-08-12"}],
        "commitments": [], "notes": [], "questions": [],
    }
    receipt = secretary.file_note(conn, 1, "x", extraction=extraction, today=TODAY)
    assert receipt["expenses"][0]["paid_from"] == "personal"
    balance = finance.compute_balance(conn)["THB"]
    assert balance["owed_by"] == "Farid"
    assert balance["amount"] == 424.43


def test_an_explicit_personal_food_purchase_is_not_fund_money(conn):
    extraction = {
        "summary": "My own lunch.", "language": "en", "events": [],
        "expenses": [{"amount": 300, "currency": "THB", "category": "food",
                      "description": "own lunch", "payer": "J", "split": "mine",
                      "paid_from": "personal", "spent_on": "2026-08-20"}],
        "commitments": [], "notes": [], "questions": [],
    }
    receipt = secretary.file_note(conn, 1, "x", extraction=extraction, today=TODAY)
    assert receipt["expenses"][0]["paid_from"] == "personal"
    assert finance.compute_balance(conn)["THB"]["amount"] == 0.0


def test_weekly_digest_combines_log_ledger_and_balance(conn):
    extraction = {
        "summary": "Internet bill.", "language": "en", "events": [],
        "expenses": [{"amount": 848.86, "currency": "THB", "category": "utilities",
                      "description": "3BB", "payer": "Farid", "split": "equal",
                      "spent_on": "2026-08-20"}],
        "commitments": [], "notes": [], "questions": [],
    }
    secretary.file_note(conn, 1, "x", extraction=extraction, today=date(2026, 8, 20))
    digest = secretary.weekly_digest(conn, today=TODAY)

    assert "Week in review" in digest
    assert "Internet bill." in digest
    assert "Who owes who" in digest
    assert "J owes Farid" in digest


# ── Regression: sentence-scoped fallback ──────────────────────────────────────

def test_fallback_categorises_from_the_spending_sentence_only():
    """An unrelated clause must not hijack the category of an expense."""
    data = secretary.extract(
        "I paid 1,250 baht for groceries at Makro today. "
        "Don't forget to send the landlord the contract. "
        "Can you do Thursday?",
        today=TODAY,
    )
    expense = data["expenses"][0]
    # "landlord" appears in the note but not in the spending sentence.
    assert expense["category"] == "food"
    assert "landlord" not in expense["description"]
    assert expense["description"].startswith("I paid 1,250 baht")


def test_fallback_separates_questions_from_the_rest():
    data = secretary.extract(
        "I paid 500 for petrol. Can you do Thursday? Are we still on for Friday?",
        today=TODAY,
    )
    assert data["questions"] == ["Can you do Thursday?", "Are we still on for Friday?"]
    assert data["expenses"][0]["category"] == "transport"


def test_fallback_keeps_commitments_out_of_the_question_list():
    data = secretary.extract("Don't forget the water bill.", today=TODAY)
    assert data["questions"] == []
    assert len(data["commitments"]) == 1
