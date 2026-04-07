"""
Tests for assistant/translator.py — translation functionality.
Tests run without OpenAI API key using fallback detection.
"""

import pytest

from assistant.translator import (
    detect_language,
    resolve_language_code,
    translate,
    translate_multi,
    format_translation_result,
    format_language_list,
    format_detected_language,
    SUPPORTED_LANGUAGES,
    LANGUAGE_ALIASES,
    LANGUAGE_FLAGS,
)


class TestResolveLanguageCode:
    """Test language code resolution."""

    def test_direct_code(self):
        assert resolve_language_code("en") == "en"
        assert resolve_language_code("th") == "th"
        assert resolve_language_code("ru") == "ru"

    def test_english_name(self):
        assert resolve_language_code("English") == "en"
        assert resolve_language_code("thai") == "th"
        assert resolve_language_code("Russian") == "ru"
        assert resolve_language_code("CHINESE") == "zh"

    def test_native_name(self):
        assert resolve_language_code("ไทย") == "th"
        assert resolve_language_code("русский") == "ru"
        assert resolve_language_code("中文") == "zh"

    def test_case_insensitive(self):
        assert resolve_language_code("ENGLISH") == "en"
        assert resolve_language_code("Thai") == "th"
        assert resolve_language_code("RUSSIAN") == "ru"

    def test_unknown_returns_none(self):
        assert resolve_language_code("klingon") is None
        assert resolve_language_code("xyz") is None
        assert resolve_language_code("") is None


class TestDetectLanguage:
    """Test language detection (fallback Unicode-based)."""

    def test_detect_thai(self):
        assert detect_language("สวัสดีครับ") == "th"
        assert detect_language("ภาษาไทย") == "th"

    def test_detect_russian(self):
        assert detect_language("Привет мир") == "ru"
        assert detect_language("Доброе утро") == "ru"

    def test_detect_chinese(self):
        assert detect_language("你好世界") == "zh"
        assert detect_language("中文测试") == "zh"

    def test_detect_japanese(self):
        assert detect_language("こんにちは") == "ja"
        assert detect_language("ありがとう") == "ja"

    def test_detect_korean(self):
        assert detect_language("안녕하세요") == "ko"
        assert detect_language("감사합니다") == "ko"

    def test_detect_arabic(self):
        assert detect_language("مرحبا بالعالم") == "ar"

    def test_english_default(self):
        # ASCII text defaults to English
        assert detect_language("Hello world") == "en"
        assert detect_language("Good morning") == "en"

    def test_empty_returns_english(self):
        assert detect_language("") == "en"
        assert detect_language("   ") == "en"


class TestTranslate:
    """Test translation function (without OpenAI, returns error)."""

    def test_invalid_target_language(self):
        result = translate("Hello", "klingon")
        assert result["success"] is False
        assert "Unsupported" in result["error"]

    def test_same_language_skipped(self):
        # When source and target are same, returns original
        result = translate("Hello", "en", source_lang="en")
        assert result["success"] is True
        assert result["translated_text"] == "Hello"
        assert "same" in result.get("note", "").lower()

    def test_returns_source_and_target(self):
        result = translate("สวัสดี", "en")
        assert result["target_lang"] == "en"
        # Source should be detected as Thai
        assert result["source_lang"] == "th"

    def test_no_api_key_error(self):
        # Without OpenAI key, translation fails gracefully
        result = translate("Hello", "th")
        # Either succeeds (if API key) or fails with helpful error
        if not result["success"]:
            assert "unavailable" in result["error"].lower() or "OPENAI" in result["error"]


class TestTranslateMulti:
    """Test multi-language translation."""

    def test_returns_dict_of_results(self):
        results = translate_multi("Hello", ["th", "ru"])
        assert "th" in results
        assert "ru" in results
        assert isinstance(results["th"], dict)
        assert isinstance(results["ru"], dict)

    def test_each_result_has_required_keys(self):
        results = translate_multi("Hello", ["th"])
        result = results["th"]
        assert "success" in result
        assert "target_lang" in result


class TestFormatFunctions:
    """Test formatting helper functions."""

    def test_format_translation_result_success(self):
        result = {
            "success": True,
            "translated_text": "สวัสดี",
            "source_lang": "en",
            "target_lang": "th",
        }
        formatted = format_translation_result(result)
        assert "Translation" in formatted
        assert "สวัสดี" in formatted
        assert "English" in formatted or "🇬🇧" in formatted
        assert "Thai" in formatted or "🇹🇭" in formatted

    def test_format_translation_result_failure(self):
        result = {
            "success": False,
            "error": "API unavailable",
        }
        formatted = format_translation_result(result)
        assert "❌" in formatted
        assert "failed" in formatted.lower()

    def test_format_language_list(self):
        formatted = format_language_list()
        assert "Supported Languages" in formatted
        assert "en" in formatted
        assert "English" in formatted
        assert "th" in formatted
        assert "Thai" in formatted

    def test_format_detected_language(self):
        formatted = format_detected_language("สวัสดี")
        assert "Thai" in formatted
        assert "th" in formatted
        assert "Detected" in formatted


class TestLanguageData:
    """Test language data consistency."""

    def test_all_languages_have_flags(self):
        for code in SUPPORTED_LANGUAGES:
            assert code in LANGUAGE_FLAGS, f"Missing flag for {code}"

    def test_aliases_map_to_valid_codes(self):
        for alias, code in LANGUAGE_ALIASES.items():
            assert code in SUPPORTED_LANGUAGES, f"Alias {alias} maps to invalid code {code}"

    def test_minimum_languages_supported(self):
        # Should support at least these common languages
        required = ["en", "th", "ru", "zh", "ja", "ko", "es", "fr", "de"]
        for lang in required:
            assert lang in SUPPORTED_LANGUAGES, f"Missing required language: {lang}"
