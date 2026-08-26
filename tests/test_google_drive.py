"""
เทสต์การต่อ Google Drive

จุดที่ต่างจากโมดูลต้นทางและเป็นเหตุผลที่ต้องมีเทสต์ชุดนี้:

* ต้นทางเก็บ access token ซึ่งหมดอายุในหนึ่งชั่วโมง ที่นี่เก็บ refresh token
  แล้วขอ access token ใหม่เอง
* ต้นทางให้ผู้ใช้พิมพ์ credential ใส่แชต Telegram ที่นี่ใช้ OAuth ผ่านเว็บ
  จึงต้องมั่นใจว่า state ปลอมไม่ได้ ไม่งั้นใครก็ผูกไดรฟ์ตัวเองเข้ากับบัญชีคนอื่น
* คอลัมน์ของ Drive เพิ่มเข้ามาทีหลัง ฐานข้อมูลเก่าที่อยู่บน volume ต้องถูก
  ALTER ให้ ไม่งั้น get_settings พังด้วย "no such column"
"""

import sqlite3
import time
import unittest
from types import SimpleNamespace

import line_webhook
from tests._bot_case import BotDbCase, bot

USER_A = 4001
USER_B = 4002


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append({"text": text, **kwargs})


class FakeUpdate:
    def __init__(self, user_id=USER_A):
        self.effective_user = SimpleNamespace(id=user_id)
        self.message = FakeMessage()


# ═══════════════════════════════════════════════════════════════════════════════
# schema
# ═══════════════════════════════════════════════════════════════════════════════

class TestDriveColumns(BotDbCase):
    def test_fresh_database_has_the_drive_columns(self):
        columns = {row["name"] for row in self.rows(
            "SELECT name FROM pragma_table_info('user_storage_settings')"
        )}
        self.assertIn("google_refresh_token", columns)
        self.assertIn("google_drive_folder_id", columns)

    def test_an_older_database_gets_the_columns_added(self):
        """จำลองฐานข้อมูลบน volume ที่สร้างไว้ก่อนมีฟีเจอร์นี้

        นี่คือกรณีจริงบน Railway — ไฟล์อยู่ข้าม deploy มาแล้ว
        CREATE TABLE IF NOT EXISTS จึงไม่แตะมันอีกเลย
        """
        conn = sqlite3.connect(bot.DB_PATH)
        with conn:
            conn.execute("DROP TABLE user_storage_settings")
            conn.execute("""
                CREATE TABLE user_storage_settings (
                    user_id INTEGER PRIMARY KEY,
                    storage_type TEXT DEFAULT 'local',
                    airtable_api_key TEXT,
                    airtable_base_id TEXT,
                    airtable_table_name TEXT DEFAULT 'Tasks',
                    google_sheet_id TEXT,
                    preferred_language TEXT DEFAULT 'en',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute(
                "INSERT INTO user_storage_settings (user_id, storage_type) VALUES (?, 'airtable')",
                (USER_A,),
            )
        conn.close()

        # ก่อน migrate ต้องพังจริง ไม่งั้นเทสต์นี้ไม่ได้พิสูจน์อะไร
        with self.assertRaises(sqlite3.OperationalError):
            bot.StorageSettings.get_settings(USER_A)

        bot.init_db()

        settings = bot.StorageSettings.get_settings(USER_A)
        self.assertEqual(settings["storage_type"], "airtable")
        self.assertIsNone(settings["google_refresh_token"])

    def test_running_init_db_twice_does_not_duplicate_columns(self):
        bot.init_db()
        bot.init_db()
        columns = [row["name"] for row in self.rows(
            "SELECT name FROM pragma_table_info('user_storage_settings')"
        )]
        self.assertEqual(len(columns), len(set(columns)))


# ═══════════════════════════════════════════════════════════════════════════════
# การเก็บค่า
# ═══════════════════════════════════════════════════════════════════════════════

class TestDriveSettings(BotDbCase):
    def test_set_google_drive_round_trip(self):
        bot.StorageSettings.set_google_drive(USER_A, "refresh-abc", "folder-1")
        settings = bot.StorageSettings.get_settings(USER_A)
        self.assertEqual(settings["storage_type"], "drive")
        self.assertEqual(settings["google_refresh_token"], "refresh-abc")
        self.assertEqual(settings["google_drive_folder_id"], "folder-1")

    def test_reconnecting_replaces_the_old_token(self):
        bot.StorageSettings.set_google_drive(USER_A, "old")
        bot.StorageSettings.set_google_drive(USER_A, "new")
        self.assertEqual(
            bot.StorageSettings.get_settings(USER_A)["google_refresh_token"], "new"
        )
        self.assertEqual(len(self.rows("SELECT user_id FROM user_storage_settings")), 1)

    def test_reset_to_local_clears_the_token(self):
        """ผู้ใช้กลับไปใช้ที่เก็บของบอท = ถอนสิทธิ์ ต้องไม่มี token ค้าง"""
        bot.StorageSettings.set_google_drive(USER_A, "refresh-abc", "folder-1")
        bot.StorageSettings.reset_to_local(USER_A)
        settings = bot.StorageSettings.get_settings(USER_A)
        self.assertEqual(settings["storage_type"], "local")
        self.assertIsNone(settings["google_refresh_token"])
        self.assertIsNone(settings["google_drive_folder_id"])

    def test_set_drive_folder_remembers_without_touching_the_token(self):
        bot.StorageSettings.set_google_drive(USER_A, "refresh-abc")
        bot.StorageSettings.set_drive_folder(USER_A, "folder-9")
        settings = bot.StorageSettings.get_settings(USER_A)
        self.assertEqual(settings["google_drive_folder_id"], "folder-9")
        self.assertEqual(settings["google_refresh_token"], "refresh-abc")

    def test_settings_stay_separate_per_user(self):
        bot.StorageSettings.set_google_drive(USER_A, "token-a")
        bot.StorageSettings.set_google_drive(USER_B, "token-b")
        self.assertEqual(
            bot.StorageSettings.get_settings(USER_A)["google_refresh_token"], "token-a"
        )
        self.assertEqual(
            bot.StorageSettings.get_settings(USER_B)["google_refresh_token"], "token-b"
        )

    def test_drive_client_only_appears_once_configured(self):
        self.assertIsNone(bot.drive_client_for(USER_A))

        bot.StorageSettings.set_storage_type(USER_A, "drive")
        self.assertIsNone(
            bot.drive_client_for(USER_A),
            "เลือก drive แต่ยังไม่ได้อนุญาต ต้องไม่ได้ client ที่ใช้ไม่ได้",
        )

        bot.StorageSettings.set_google_drive(USER_A, "refresh-abc")
        self.assertIsInstance(bot.drive_client_for(USER_A), bot.GoogleDriveClient)

    def test_a_local_user_never_gets_a_drive_client(self):
        bot.StorageSettings.set_google_drive(USER_A, "refresh-abc")
        bot.StorageSettings.set_storage_type(USER_A, "local")
        self.assertIsNone(bot.drive_client_for(USER_A))


# ═══════════════════════════════════════════════════════════════════════════════
# state ของ OAuth
# ═══════════════════════════════════════════════════════════════════════════════

class TestOAuthState(unittest.TestCase):
    def setUp(self):
        self._secret = line_webhook.GOOGLE_CLIENT_SECRET
        line_webhook.GOOGLE_CLIENT_SECRET = "server-side-secret"

    def tearDown(self):
        line_webhook.GOOGLE_CLIENT_SECRET = self._secret

    def test_round_trip(self):
        state = line_webhook.make_oauth_state(USER_A)
        self.assertEqual(line_webhook.verify_oauth_state(state), USER_A)

    def test_a_forged_state_is_rejected(self):
        """นี่คือเหตุผลที่ต้องเซ็น — ไม่งั้นใครก็ผูกไดรฟ์ตัวเองกับบัญชีคนอื่นได้"""
        forged = f"{USER_B}.{int(time.time())}.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        self.assertIsNone(line_webhook.verify_oauth_state(forged))

    def test_swapping_the_user_id_breaks_the_signature(self):
        state = line_webhook.make_oauth_state(USER_A)
        _, stamp, sig = state.split(".")
        self.assertIsNone(line_webhook.verify_oauth_state(f"{USER_B}.{stamp}.{sig}"))

    def test_an_expired_link_is_rejected(self):
        old = time.time() - line_webhook.OAUTH_STATE_TTL_SECONDS - 1
        state = line_webhook.make_oauth_state(USER_A, issued_at=old)
        self.assertIsNone(line_webhook.verify_oauth_state(state))

    def test_a_link_inside_the_window_still_works(self):
        recent = time.time() - line_webhook.OAUTH_STATE_TTL_SECONDS + 60
        state = line_webhook.make_oauth_state(USER_A, issued_at=recent)
        self.assertEqual(line_webhook.verify_oauth_state(state), USER_A)

    def test_a_state_signed_with_another_secret_is_rejected(self):
        state = line_webhook.make_oauth_state(USER_A)
        line_webhook.GOOGLE_CLIENT_SECRET = "a-different-secret"
        self.assertIsNone(line_webhook.verify_oauth_state(state))

    def test_padded_user_ids_do_not_share_a_signature(self):
        """เทียบจากสตริงดิบ ไม่ใช่ int ที่แปลงแล้ว — "007" ต้องไม่ผ่านลายเซ็นของ "7"
        """
        state = line_webhook.make_oauth_state(7)
        _, stamp, sig = state.split(".")
        self.assertIsNone(line_webhook.verify_oauth_state(f"007.{stamp}.{sig}"))

    def test_malformed_states_are_rejected_without_raising(self):
        for bad in ("", "abc", "1.2", "1.2.3.4", "x.y.z"):
            with self.subTest(state=bad):
                self.assertIsNone(line_webhook.verify_oauth_state(bad))

    def test_nothing_verifies_when_oauth_is_not_configured(self):
        state = line_webhook.make_oauth_state(USER_A)
        line_webhook.GOOGLE_CLIENT_SECRET = ""
        self.assertIsNone(line_webhook.verify_oauth_state(state))


class TestConsentUrl(unittest.TestCase):
    def setUp(self):
        self._saved = (
            line_webhook.GOOGLE_CLIENT_ID,
            line_webhook.GOOGLE_CLIENT_SECRET,
            line_webhook.PUBLIC_BASE_URL,
        )
        line_webhook.GOOGLE_CLIENT_ID = "client-123"
        line_webhook.GOOGLE_CLIENT_SECRET = "secret-123"
        line_webhook.PUBLIC_BASE_URL = "https://bot.example.com"

    def tearDown(self):
        (
            line_webhook.GOOGLE_CLIENT_ID,
            line_webhook.GOOGLE_CLIENT_SECRET,
            line_webhook.PUBLIC_BASE_URL,
        ) = self._saved

    def test_url_carries_what_google_needs_for_a_refresh_token(self):
        url = line_webhook.google_consent_url(USER_A)
        # ขาดสองตัวนี้ Google จะให้แค่ access token อายุหนึ่งชั่วโมง
        self.assertIn("access_type=offline", url)
        self.assertIn("prompt=consent", url)

    def test_url_asks_only_for_the_narrow_scope(self):
        url = line_webhook.google_consent_url(USER_A)
        self.assertIn("drive.file", url)
        self.assertNotIn("auth%2Fdrive&", url)

    def test_redirect_uri_matches_the_route_that_is_registered(self):
        url = line_webhook.google_consent_url(USER_A)
        self.assertIn("bot.example.com", url)
        self.assertIn("oauth%2Fgoogle%2Fcallback", url)

    def test_no_url_when_the_owner_has_not_configured_oauth(self):
        for missing in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "PUBLIC_BASE_URL"):
            with self.subTest(missing=missing):
                saved = getattr(line_webhook, missing)
                setattr(line_webhook, missing, "")
                try:
                    self.assertIsNone(line_webhook.google_consent_url(USER_A))
                finally:
                    setattr(line_webhook, missing, saved)

    def test_the_callback_route_is_actually_registered(self):
        """ประกอบ URL ถูกแต่ไม่ได้ลงทะเบียน route = ผู้ใช้เจอ 404 หลังกดยินยอม"""
        app = line_webhook.create_app(handler=_DummyHandler())
        paths = {getattr(r.resource, "canonical", None) for r in app.router.routes()}
        self.assertIn(line_webhook.GOOGLE_OAUTH_CALLBACK_PATH, paths)


class _DummyHandler:
    db_path = ":memory:"

    async def handle(self, request):
        raise NotImplementedError

    async def close(self):
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# การต่ออายุ token
# ═══════════════════════════════════════════════════════════════════════════════

class _FakeResponse:
    def __init__(self, payload, status):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload

    async def text(self):
        import json as _json

        return _json.dumps(self._payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """แทน aiohttp.ClientSession แค่เท่าที่ _token() ใช้"""

    def __init__(self, payload, status=200, posted=None):
        self._payload = payload
        self._status = status
        self._posted = posted

    def post(self, url, data=None, **kwargs):
        if self._posted is not None:
            self._posted["url"] = url
            self._posted["data"] = data
        return _FakeResponse(self._payload, self._status)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _patched_session:
    """สลับ aiohttp.ClientSession ใน bot ชั่วคราว"""

    def __init__(self, session):
        self._session = session

    def __enter__(self):
        self._original = bot.aiohttp.ClientSession
        bot.aiohttp.ClientSession = lambda *a, **k: self._session
        return self._session

    def __exit__(self, *exc):
        bot.aiohttp.ClientSession = self._original
        return False


class TestTokenRefresh(unittest.IsolatedAsyncioTestCase):
    async def test_a_cached_token_is_reused(self):
        client = bot.GoogleDriveClient("refresh", client_id="id", client_secret="sec")
        client._access_token = "cached"
        client._expires_at = bot.datetime.utcnow() + bot.timedelta(minutes=5)
        self.assertEqual(await client._token(), "cached")

    async def test_an_expired_token_is_swapped_for_a_fresh_one(self):
        """นี่คือข้อต่างสำคัญจากโมดูลต้นทาง

        ต้นทางเก็บ access token ตรง ๆ พอผ่านไปหนึ่งชั่วโมงก็ใช้ไม่ได้และต่อใหม่
        ไม่ได้ด้วย ที่นี่ต้องเห็น token ใหม่มาแทนของเก่า
        """
        client = bot.GoogleDriveClient("refresh", client_id="id", client_secret="sec")
        client._access_token = "stale"
        client._expires_at = bot.datetime.utcnow() - bot.timedelta(seconds=1)

        posted = {}
        session = _FakeSession(
            {"access_token": "fresh", "expires_in": 3600}, posted=posted
        )
        with _patched_session(session):
            self.assertEqual(await client._token(), "fresh")

        self.assertEqual(posted["data"]["grant_type"], "refresh_token")
        self.assertEqual(posted["data"]["refresh_token"], "refresh")
        # ครั้งต่อไปต้องใช้ของที่ขอมาแล้ว ไม่ยิงซ้ำ
        with _patched_session(_FakeSession({}, status=500)):
            self.assertEqual(await client._token(), "fresh")

    async def test_a_refused_refresh_reports_no_token(self):
        """ผู้ใช้ถอนสิทธิ์ที่ฝั่ง Google — ต้องได้ None ไม่ใช่ token ที่ใช้ไม่ได้"""
        client = bot.GoogleDriveClient("refresh", client_id="id", client_secret="sec")
        with _patched_session(_FakeSession({"error": "invalid_grant"}, status=400)):
            with self.assertLogs("bot", level="WARNING"):
                self.assertIsNone(await client._token())

    async def test_folder_id_is_reused_without_a_lookup(self):
        client = bot.GoogleDriveClient("refresh", folder_id="existing")
        self.assertEqual(await client.ensure_folder(), "existing")

    async def test_missing_oauth_config_reports_instead_of_calling_google(self):
        saved = (bot.GOOGLE_CLIENT_ID, bot.GOOGLE_CLIENT_SECRET)
        bot.GOOGLE_CLIENT_ID, bot.GOOGLE_CLIENT_SECRET = "", ""
        try:
            client = bot.GoogleDriveClient("refresh")
            result = await client.test_connection()
        finally:
            bot.GOOGLE_CLIENT_ID, bot.GOOGLE_CLIENT_SECRET = saved
        self.assertFalse(result["success"])
        self.assertIn("GOOGLE_CLIENT_ID", result["message"])


# ═══════════════════════════════════════════════════════════════════════════════
# การส่งสำเนาขึ้น Drive
# ═══════════════════════════════════════════════════════════════════════════════

class TestMirroring(BotDbCase):
    async def test_a_local_user_never_touches_drive(self):
        update = FakeUpdate()
        await bot.mirror_and_warn(update, USER_A)
        self.assertEqual(update.message.replies, [])

    async def test_the_mirror_payload_carries_everything(self):
        bot.StorageSettings.set_google_drive(USER_A, "refresh-abc")
        await bot.Storage.add_task(USER_A, "ซื้อของ", "high")
        await bot.Storage.add_note(USER_A, "บันทึกหนึ่ง")

        sent = {}

        class FakeDrive:
            folder_id = "folder-1"

            async def write_json(self, name, payload):
                sent["name"] = name
                sent["payload"] = payload
                return "file-1"

        original = bot.drive_client_for
        bot.drive_client_for = lambda user_id: FakeDrive()
        try:
            self.assertTrue(await bot.mirror_to_drive(USER_A))
        finally:
            bot.drive_client_for = original

        self.assertEqual(sent["name"], f"assistant_data_{USER_A}.json")
        self.assertEqual(len(sent["payload"]["tasks"]), 1)
        self.assertEqual(len(sent["payload"]["notes"]), 1)
        self.assertIn("reminders", sent["payload"])
        self.assertEqual(sent["payload"]["user_id"], USER_A)

    async def test_a_failed_upload_tells_the_user_instead_of_going_quiet(self):
        """เงียบแล้วเจ็บทีหลัง — ผู้ใช้จะเชื่อว่าข้อมูลขึ้น Drive แล้วทั้งที่ไม่ได้ขึ้น"""
        bot.StorageSettings.set_google_drive(USER_A, "refresh-abc")

        class FailingDrive:
            folder_id = None

            async def write_json(self, name, payload):
                return None

        update = FakeUpdate()
        original = bot.drive_client_for
        bot.drive_client_for = lambda user_id: FailingDrive()
        try:
            await bot.mirror_and_warn(update, USER_A)
        finally:
            bot.drive_client_for = original

        self.assertEqual(len(update.message.replies), 1)
        self.assertIn("Drive", update.message.replies[0]["text"])

    async def test_an_exception_from_drive_does_not_escape(self):
        """คำสั่งต้องไม่ล้ม เพราะข้อมูลลง SQLite ไปแล้วก่อนถึงขั้นนี้"""
        bot.StorageSettings.set_google_drive(USER_A, "refresh-abc")

        class ExplodingDrive:
            folder_id = None

            async def write_json(self, name, payload):
                raise RuntimeError("drive ล่ม")

        original = bot.drive_client_for
        bot.drive_client_for = lambda user_id: ExplodingDrive()
        try:
            with self.assertLogs("bot", level="ERROR"):
                self.assertFalse(await bot.mirror_to_drive(USER_A))
        finally:
            bot.drive_client_for = original

    async def test_a_newly_created_folder_is_remembered(self):
        bot.StorageSettings.set_google_drive(USER_A, "refresh-abc")

        class FakeDrive:
            folder_id = "brand-new-folder"

            async def write_json(self, name, payload):
                return "file-1"

        original = bot.drive_client_for
        bot.drive_client_for = lambda user_id: FakeDrive()
        try:
            await bot.mirror_to_drive(USER_A)
        finally:
            bot.drive_client_for = original

        self.assertEqual(
            bot.StorageSettings.get_settings(USER_A)["google_drive_folder_id"],
            "brand-new-folder",
        )

    async def test_the_local_write_survives_a_drive_outage(self):
        """SQLite เป็นต้นฉบับ Drive เป็นสำเนา — Drive ล่มต้องไม่ทำให้งานหาย"""
        bot.StorageSettings.set_google_drive(USER_A, "refresh-abc")
        await bot.Storage.add_task(USER_A, "งานที่ต้องรอด")

        class FailingDrive:
            folder_id = None

            async def write_json(self, name, payload):
                return None

        original = bot.drive_client_for
        bot.drive_client_for = lambda user_id: FailingDrive()
        try:
            await bot.mirror_and_warn(FakeUpdate(), USER_A)
        finally:
            bot.drive_client_for = original

        tasks = await bot.Storage.get_tasks(USER_A)
        self.assertEqual(len(tasks), 1)


# ═══════════════════════════════════════════════════════════════════════════════
# หน้าตาที่ผู้ใช้เห็น
# ═══════════════════════════════════════════════════════════════════════════════

class TestDriveUi(BotDbCase):
    async def test_mystorage_never_prints_the_token(self):
        bot.StorageSettings.set_google_drive(USER_A, "super-secret-refresh-token")
        update = FakeUpdate()
        await bot.mystorage_command(update, None)
        text = update.message.replies[0]["text"]
        self.assertNotIn("super-secret-refresh-token", text)
        self.assertIn("เชื่อมต่อแล้ว", text)

    async def test_mystorage_says_so_when_drive_is_chosen_but_not_authorised(self):
        bot.StorageSettings.set_storage_type(USER_A, "drive")
        update = FakeUpdate()
        await bot.mystorage_command(update, None)
        self.assertIn("ยังไม่ได้อนุญาต", update.message.replies[0]["text"])

    async def test_settings_menu_offers_drive(self):
        update = FakeUpdate()
        await bot.settings_command(update, None)
        markup = update.message.replies[0]["reply_markup"]
        callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertIn("storage:drive", callbacks)


if __name__ == "__main__":
    unittest.main()
