"""Tests for new modules: voice, links, projects, digest, autoscan."""

import sqlite3
import pytest
from datetime import datetime, timezone

from assistant import voice, links, projects, digest, autoscan


# ── Voice module tests ────────────────────────────────────────────────────────

class TestVoice:
    def test_detect_language_thai(self):
        assert voice.detect_language_from_text("สวัสดีครับ") == "th"
    
    def test_detect_language_russian(self):
        assert voice.detect_language_from_text("Привет мир") == "ru"
    
    def test_detect_language_english(self):
        assert voice.detect_language_from_text("Hello world") == "en"
    
    def test_detect_language_chinese(self):
        assert voice.detect_language_from_text("你好世界") == "zh"
    
    def test_format_transcription(self):
        result = voice.format_transcription("Hello test", "en")
        assert "🎤" in result
        assert "Hello test" in result
    
    def test_format_transcription_empty(self):
        result = voice.format_transcription("")
        assert "Could not transcribe" in result


# ── Links module tests ────────────────────────────────────────────────────────

class TestLinks:
    def test_extract_urls_single(self):
        urls = links.extract_urls("Check this https://example.com please")
        assert urls == ["https://example.com"]
    
    def test_extract_urls_multiple(self):
        text = "See https://google.com and http://example.org"
        urls = links.extract_urls(text)
        assert len(urls) == 2
    
    def test_extract_urls_empty(self):
        assert links.extract_urls("No links here") == []
    
    def test_classify_url_facebook(self):
        assert links.classify_url("https://facebook.com/post") == "facebook"
    
    def test_classify_url_youtube(self):
        assert links.classify_url("https://youtube.com/watch") == "youtube"
    
    def test_classify_url_airbnb(self):
        assert links.classify_url("https://airbnb.com/rooms/123") == "airbnb"
    
    def test_classify_url_maps(self):
        assert links.classify_url("https://google.com/maps/place") == "maps"
    
    def test_classify_url_generic(self):
        assert links.classify_url("https://random-site.com") == "web"
    
    def test_looks_like_listing_true(self):
        text = "3 bedroom villa for rent 50,000 baht/month location Chaweng"
        assert links.looks_like_listing(text) is True
    
    def test_looks_like_listing_false(self):
        text = "This is just a regular article about cooking"
        assert links.looks_like_listing(text) is False
    
    def test_extract_metadata(self):
        html = '<html><title>Test Page</title><body>Content here</body></html>'
        metadata = links.extract_metadata(html)
        assert metadata["title"] == "Test Page"


# ── Projects module tests ─────────────────────────────────────────────────────

class TestProjects:
    @pytest.fixture
    def conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        projects.ensure_projects_table(conn)
        return conn
    
    def test_create_project(self, conn):
        project = projects.create_project(conn, user_id=123, title="Test Project")
        assert project.id is not None
        assert project.title == "Test Project"
        assert project.status == "idea"
    
    def test_get_projects(self, conn):
        projects.create_project(conn, user_id=123, title="Project 1")
        projects.create_project(conn, user_id=123, title="Project 2")
        result = projects.get_projects(conn, user_id=123)
        assert len(result) == 2
    
    def test_get_projects_user_isolation(self, conn):
        projects.create_project(conn, user_id=123, title="User 123 Project")
        projects.create_project(conn, user_id=456, title="User 456 Project")
        result = projects.get_projects(conn, user_id=123)
        assert len(result) == 1
        assert result[0].title == "User 123 Project"
    
    def test_update_project(self, conn):
        project = projects.create_project(conn, user_id=123, title="Original")
        updated = projects.update_project(conn, project.id, title="Updated", status="active")
        assert updated.title == "Updated"
        assert updated.status == "active"
    
    def test_delete_project(self, conn):
        project = projects.create_project(conn, user_id=123, title="To Delete")
        result = projects.delete_project(conn, project.id)
        assert result is True
        assert projects.get_project_by_id(conn, project.id) is None
    
    def test_find_project_by_title(self, conn):
        projects.create_project(conn, user_id=123, title="Villa Rental Business")
        found = projects.find_project_by_title(conn, user_id=123, title="villa")
        assert found is not None
        assert "Villa" in found.title
    
    def test_detect_language_thai(self):
        assert projects.detect_language("สวัสดี") == "th"
    
    def test_detect_language_russian(self):
        assert projects.detect_language("Привет") == "ru"
    
    def test_format_projects_list_empty(self, conn):
        result = projects.format_projects_list([])
        assert "No projects" in result
    
    def test_extract_score(self):
        analysis = "Market: 8/10, Feasibility: 7/10, Impact: 9/10"
        score = projects.extract_score_from_analysis(analysis)
        assert score == 8.0  # Average of 8, 7, 9


# ── Autoscan module tests ─────────────────────────────────────────────────────

class TestAutoscan:
    @pytest.fixture
    def conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        autoscan.ensure_pending_table(conn)
        return conn
    
    def test_increment_message_count(self):
        autoscan._message_counters.clear()
        count, should_scan = autoscan.increment_message_count(user_id=999)
        assert count == 1
        assert should_scan is False
    
    def test_increment_triggers_scan_at_interval(self):
        autoscan._message_counters.clear()
        for i in range(autoscan.AUTO_SCAN_INTERVAL - 1):
            autoscan.increment_message_count(user_id=888)
        count, should_scan = autoscan.increment_message_count(user_id=888)
        assert count == autoscan.AUTO_SCAN_INTERVAL
        assert should_scan is True
    
    def test_reset_message_count(self):
        autoscan._message_counters[777] = 50
        autoscan.reset_message_count(777)
        assert autoscan._message_counters[777] == 0
    
    def test_save_and_get_pending_items(self, conn):
        items = [
            autoscan.ExtractedItem(
                type="task", title="Test Task", detail="",
                who="User", when="", confidence=0.9, language="en"
            )
        ]
        autoscan.save_pending_items(conn, user_id=123, items=items)
        retrieved = autoscan.get_pending_items(conn, user_id=123)
        assert len(retrieved) == 1
        assert retrieved[0].title == "Test Task"
    
    def test_clear_pending_items(self, conn):
        items = [
            autoscan.ExtractedItem(
                type="task", title="To Clear", detail="",
                who="User", when="", confidence=0.9, language="en"
            )
        ]
        autoscan.save_pending_items(conn, user_id=123, items=items)
        autoscan.clear_pending_items(conn, user_id=123)
        retrieved = autoscan.get_pending_items(conn, user_id=123)
        assert len(retrieved) == 0
    
    def test_format_extracted_items_empty(self):
        result = autoscan.format_extracted_items([])
        assert "Nothing new" in result
    
    def test_format_scan_result(self):
        items = [
            autoscan.ExtractedItem(
                type="task", title="Do something", detail="",
                who="User", when="tomorrow", confidence=0.9, language="en"
            )
        ]
        result = autoscan.format_scan_result(items, language="en")
        assert "Found 1" in result
        assert "Do something" in result


# ── Digest module tests ───────────────────────────────────────────────────────

class TestDigest:
    def test_digest_templates_exist(self):
        assert "th" in digest.DIGEST_TEMPLATES
        assert "ru" in digest.DIGEST_TEMPLATES
        assert "en" in digest.DIGEST_TEMPLATES
    
    def test_template_has_required_keys(self):
        for lang in ["th", "ru", "en"]:
            template = digest.DIGEST_TEMPLATES[lang]
            assert "greeting" in template
            assert "pending_tasks" in template
            assert "today" in template
