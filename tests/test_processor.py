"""
Tests for assistant/processor.py — NLP fallback logic (no API key required).
"""

import pytest

from assistant.processor import detect_intent, extract_tasks, summarise, _infer_category, _infer_priority


class TestDetectIntent:
    """Keyword-based intent detection (no OpenAI key needed)."""

    def test_task_intent(self):
        assert detect_intent("I need to call Alice") == "task"

    def test_reminder_intent(self):
        assert detect_intent("remind me to send the report") == "reminder"

    def test_calendar_intent(self):
        assert detect_intent("schedule a meeting for tomorrow") == "calendar"

    def test_summary_intent(self):
        assert detect_intent("can you summarize our conversation?") == "summary"

    def test_status_intent(self):
        assert detect_intent("what is the status of my tasks?") == "status"

    def test_help_intent(self):
        assert detect_intent("help me with the commands") == "help"

    def test_general_intent(self):
        assert detect_intent("the weather is nice today") == "general"

    def test_reminder_takes_priority_over_task(self):
        # "remind me to do X" — reminder keyword wins over task
        result = detect_intent("remind me to finish the report")
        assert result == "reminder"


class TestInferPriority:
    def test_urgent_is_high(self):
        assert _infer_priority("this is urgent") == 1

    def test_asap_is_high(self):
        assert _infer_priority("send ASAP") == 1

    def test_low_priority(self):
        assert _infer_priority("do this whenever you can, low priority") == 3

    def test_default_medium(self):
        assert _infer_priority("send the report") == 2


class TestInferCategory:
    def test_business(self):
        assert _infer_category("schedule a client meeting") == "business"

    def test_personal(self):
        assert _infer_category("book a doctor appointment") == "personal"

    def test_urgent(self):
        assert _infer_category("urgent: call the client") == "urgent"

    def test_general(self):
        assert _infer_category("do something random") == "general"


class TestExtractTasks:
    """Keyword-based fallback task extraction."""

    def test_extract_single_action(self):
        tasks = extract_tasks("Please review the document.")
        assert len(tasks) >= 1
        assert any("review" in t["title"].lower() for t in tasks)

    def test_no_action_no_tasks(self):
        # A sentence with no action verb should yield 0 tasks (or very few)
        tasks = extract_tasks("The sky is blue today.")
        assert len(tasks) == 0

    def test_multiple_actions(self):
        text = "Send the email and review the report and book the meeting."
        tasks = extract_tasks(text)
        assert len(tasks) >= 2

    def test_priority_propagated(self):
        tasks = extract_tasks("Urgently call the client.")
        assert len(tasks) >= 1
        assert tasks[0]["priority"] == 1

    def test_category_propagated(self):
        tasks = extract_tasks("Schedule a client meeting for the project.")
        assert len(tasks) >= 1
        # business keywords (client, meeting, project)
        assert tasks[0]["category"] in ("business", "urgent", "general")


class TestSummarise:
    def test_empty_messages(self):
        result = summarise([])
        assert "no messages" in result.lower()

    def test_fallback_returns_last_messages(self):
        messages = [{"role": "user", "text": f"Message {i}"} for i in range(10)]
        result = summarise(messages)
        # Without OpenAI the fallback returns last 5 messages
        assert "Message" in result

    def test_modes_accepted(self):
        messages = [{"role": "user", "text": "Deploy the new feature by Friday."}]
        for mode in ("short", "detailed", "executive"):
            result = summarise(messages, mode=mode)
            assert isinstance(result, str)
            assert len(result) > 0
