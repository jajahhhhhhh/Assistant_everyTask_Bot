"""Tests for the pure text helpers in bot.py."""

import pytest


class TestEscapeMd:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("fix user_id", "fix user\\_id"),
            ("*bold*", "\\*bold\\*"),
            ("a `code` b", "a \\`code\\` b"),
            ("[link]", "\\[link]"),
            ("plain text", "plain text"),
        ],
    )
    def test_escapes_entity_openers(self, bot, raw, expected):
        assert bot.escape_md(raw) == expected

    def test_none_becomes_empty(self, bot):
        assert bot.escape_md(None) == ""

    def test_non_string_is_coerced(self, bot):
        assert bot.escape_md(7) == "7"

    def test_no_special_chars_survive_unescaped(self, bot):
        """The regression: an odd number of stray markers made Telegram reject
        the whole message, so the user never saw their confirmation."""
        escaped = bot.escape_md("read file_one and file_two_")
        for marker in ("_", "*", "`", "["):
            # every occurrence must be preceded by a backslash
            for i, ch in enumerate(escaped):
                if ch == marker:
                    assert i > 0 and escaped[i - 1] == "\\"


class TestEscapeCode:
    def test_backtick_cannot_close_the_span(self, bot):
        assert "`" not in bot.escape_code("app`id")

    def test_other_markers_are_left_alone(self, bot):
        # inside a code span Telegram does not interpret _ or *
        assert bot.escape_code("app_id*") == "app_id*"

    def test_none_becomes_empty(self, bot):
        assert bot.escape_code(None) == ""


class TestDetectPriority:
    @pytest.mark.parametrize(
        "title",
        ["Buy flowers", "Highlight the report", "Buy a slow cooker",
         "Follow up with Ann", "Water the plants below deck"],
    )
    def test_substrings_do_not_trigger_a_priority(self, bot, title):
        """Regression: "flowers" contains "low", "highlight" contains "high"."""
        assert bot.detect_priority(title) == "medium"

    @pytest.mark.parametrize(
        "title,expected",
        [
            ("Urgent: call the bank", "urgent"),
            ("this is ASAP", "urgent"),
            ("Call mom!", "urgent"),
            ("important review", "high"),
            ("high priority ticket", "high"),
            ("low priority cleanup", "low"),
            ("do this later", "low"),
            ("Book flights", "medium"),
        ],
    )
    def test_real_keywords_still_match(self, bot, title, expected):
        assert bot.detect_priority(title) == expected

    @pytest.mark.parametrize(
        "title,expected",
        [("งานด่วนมาก", "urgent"), ("เรื่องสำคัญ", "high"), ("ความสำคัญต่ำ", "high")],
    )
    def test_thai_keywords_match_as_substrings(self, bot, title, expected):
        """Thai is written without spaces, so word boundaries cannot apply."""
        assert bot.detect_priority(title) == expected

    def test_urgent_wins_over_low(self, bot):
        assert bot.detect_priority("urgent but low effort") == "urgent"

    def test_empty_title(self, bot):
        assert bot.detect_priority("") == "medium"
        assert bot.detect_priority(None) == "medium"
