-- =============================================================================
--  Life OS — 01_schema.sql
--  โครงตารางสำหรับหมวดวิเคราะห์คอขวด
--  SQLite 3.25+ (ใช้ window functions, ไม่ใช้ strftime %V/%G ที่ต้องการ 3.46)
--
--  กติกาเรื่องเวลา
--    • timestamp ทุกตัวเก็บเป็น TEXT ISO-8601 UTC เช่น '2026-08-25T14:30:00Z'
--    • การแปลงเป็นเวลาไทยทำในชั้น view เท่านั้น ผ่านค่า tz_offset ใน cfg
--    • เก็บ UTC ไว้เสมอ เพื่อไม่ให้ข้อมูลพังตอนย้ายเซิร์ฟเวอร์
--
--  จุดที่ต่างจาก schema ในพิมพ์เขียว และเหตุผล
--    1) task_blocks   — พิมพ์เขียวมี blocked_since/blocked_reason เป็นคอลัมน์เดียว
--                       ซึ่งเก็บได้แค่การติดครั้งล่าสุด งานหนึ่งชิ้นติด Farid สองรอบ
--                       และติด P'Korn อีกรอบ จะนับเวลารอไม่ครบ จึงต้องมีตารางประวัติ
--                       คอลัมน์เดิมบน tasks ยังอยู่ ใช้เป็นตัวชี้ "ตอนนี้ติดอะไร" ให้บอทอ่านเร็ว
--    2) work_sessions — flow efficiency ต้องรู้ว่าลงมือจริงกี่นาที ไม่ใช่แค่ตัวเลขสรุป
--                       ถ้ายังไม่พร้อมเก็บ ปล่อยตารางนี้ว่างไว้ได้ view จะถอยไปใช้ tasks.actual_min เอง
--    3) task_events   — ใช้หาว่างานค้างที่สถานะเดิมนานแค่ไหน และวันหนึ่งสลับกี่โปรเจกต์
-- =============================================================================

PRAGMA foreign_keys = ON;

-- ---------- มิติ -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS projects (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  type        TEXT NOT NULL CHECK (type IN ('chowrest','niksen','barbar','b52','personal')),
  is_active   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS contacts (
  id            INTEGER PRIMARY KEY,
  display_name  TEXT NOT NULL,
  org           TEXT,
  role          TEXT,
  -- handle ของคนเดียวกันจากทุกแพลตฟอร์มต้องอยู่แถวเดียว
  -- ไม่งั้น "รอ Farid" จะกลายเป็นสามคนคนละแถว
  line_user_id  TEXT UNIQUE,
  wa_number     TEXT UNIQUE,
  fb_psid       TEXT UNIQUE,
  tg_user_id    TEXT UNIQUE
);

-- ---------- งาน --------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tasks (
  id                    INTEGER PRIMARY KEY,
  title                 TEXT NOT NULL,
  note                  TEXT,
  project_id            INTEGER REFERENCES projects(id),
  category              TEXT,          -- เช่น 'คุยผู้รับเหมา','เอกสาร','ของร้าน','โค้ด'
  status                TEXT NOT NULL DEFAULT 'inbox'
                          CHECK (status IN ('inbox','doing','blocked','done','dropped')),
  created_at            TEXT NOT NULL,
  started_at            TEXT,          -- เริ่มลงมือจริง ไม่ใช่วันที่รับงาน
  completed_at          TEXT,
  due_at                TEXT,
  estimate_min          INTEGER,
  actual_min            INTEGER,       -- ใช้เมื่อไม่มี work_sessions
  due_moved_count       INTEGER NOT NULL DEFAULT 0,
  -- ตัวชี้สถานะปัจจุบัน (mirror ของ task_blocks แถวที่ยังเปิดอยู่)
  blocked_since         TEXT,
  blocked_reason        TEXT,
  blocked_on_contact_id INTEGER REFERENCES contacts(id),
  source                TEXT,          -- 'line','telegram','whatsapp','manual'
  source_ref            TEXT           -- chat_messages.id ต้นทาง กดย้อนดูได้
);

CREATE INDEX IF NOT EXISTS ix_tasks_status    ON tasks(status);
CREATE INDEX IF NOT EXISTS ix_tasks_completed ON tasks(completed_at);
CREATE INDEX IF NOT EXISTS ix_tasks_project   ON tasks(project_id);

-- ประวัติการติด — หัวใจของคำถาม "ติดใคร"
CREATE TABLE IF NOT EXISTS task_blocks (
  id            INTEGER PRIMARY KEY,
  task_id       INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  reason        TEXT NOT NULL,     -- 'รอคนตอบ','รอเอกสาร','รอเงิน','รอคิวตัวเอง','รอของ'
  contact_id    INTEGER REFERENCES contacts(id),   -- NULL = ไม่ได้ติดที่คน
  blocked_at    TEXT NOT NULL,
  unblocked_at  TEXT               -- NULL = ยังติดอยู่ตอนนี้
);

CREATE INDEX IF NOT EXISTS ix_blocks_task    ON task_blocks(task_id);
CREATE INDEX IF NOT EXISTS ix_blocks_contact ON task_blocks(contact_id);
CREATE INDEX IF NOT EXISTS ix_blocks_open    ON task_blocks(unblocked_at);

-- ช่วงที่ลงมือทำจริง
CREATE TABLE IF NOT EXISTS work_sessions (
  id          INTEGER PRIMARY KEY,
  task_id     INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  started_at  TEXT NOT NULL,
  ended_at    TEXT,
  minutes     INTEGER            -- ใส่ตรง ๆ ได้ ถ้าไม่ได้จับเวลา
);

CREATE INDEX IF NOT EXISTS ix_sessions_task ON work_sessions(task_id);

-- การเปลี่ยนสถานะ ใช้หางานที่ค้างนิ่งและการสลับบริบท
CREATE TABLE IF NOT EXISTS task_events (
  id          INTEGER PRIMARY KEY,
  task_id     INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  from_status TEXT,
  to_status   TEXT NOT NULL,
  at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_events_task ON task_events(task_id);
CREATE INDEX IF NOT EXISTS ix_events_at   ON task_events(at);

-- ---------- แชท --------------------------------------------------------------

CREATE TABLE IF NOT EXISTS chat_threads (
  id                INTEGER PRIMARY KEY,
  platform          TEXT NOT NULL CHECK (platform IN ('line','whatsapp','messenger','telegram')),
  external_chat_id  TEXT NOT NULL,
  title             TEXT,
  project_id        INTEGER REFERENCES projects(id),
  is_group          INTEGER NOT NULL DEFAULT 0,
  last_msg_at       TEXT,
  UNIQUE (platform, external_chat_id)
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id              INTEGER PRIMARY KEY,
  thread_id       INTEGER NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
  contact_id      INTEGER REFERENCES contacts(id),   -- NULL เมื่อ direction='out' (คือคุณเอง)
  direction       TEXT NOT NULL CHECK (direction IN ('in','out')),
  body            TEXT,
  sent_at         TEXT NOT NULL,
  intent          TEXT CHECK (intent IN ('request','promise','question','decision','smalltalk')),
  urgency         TEXT CHECK (urgency IN ('low','normal','high')),
  confidence      REAL,                              -- ความมั่นใจของตัวคัดแยก 0–1
  project_id      INTEGER REFERENCES projects(id),
  linked_task_id  INTEGER REFERENCES tasks(id),
  responded_at    TEXT,                              -- ถ้าแอปเติมให้ view จะใช้ค่านี้ก่อน
  raw_json        TEXT
);

CREATE INDEX IF NOT EXISTS ix_msg_thread  ON chat_messages(thread_id, sent_at);
CREATE INDEX IF NOT EXISTS ix_msg_contact ON chat_messages(contact_id);
CREATE INDEX IF NOT EXISTS ix_msg_dir     ON chat_messages(direction, intent);

-- ---------- เงิน -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS expenses (
  id                INTEGER PRIMARY KEY,
  amount            REAL NOT NULL,          -- บวก = จ่ายออก, ลบ = รับเข้า
  currency          TEXT NOT NULL DEFAULT 'THB',
  category          TEXT,
  note              TEXT,                   -- "ใช้จ่ายค่าอะไร" — สำคัญกว่า category
  merchant          TEXT,
  payment_method    TEXT,
  paid_at           TEXT NOT NULL,
  created_at        TEXT NOT NULL,
  slip_url          TEXT,
  slip_ocr_text     TEXT,
  ocr_confidence    REAL,
  verified_by_user  INTEGER NOT NULL DEFAULT 0,
  project_id        INTEGER REFERENCES projects(id),
  is_business       INTEGER NOT NULL DEFAULT 0,
  is_recurring      INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_exp_paid     ON expenses(paid_at);
CREATE INDEX IF NOT EXISTS ix_exp_category ON expenses(category);
CREATE INDEX IF NOT EXISTS ix_exp_project  ON expenses(project_id);

-- ---------- บันทึกประจำวัน ---------------------------------------------------

CREATE TABLE IF NOT EXISTS daily_logs (
  id             INTEGER PRIMARY KEY,
  log_date       TEXT NOT NULL UNIQUE,   -- วันที่ตามเวลาไทย 'YYYY-MM-DD'
  submitted_at   TEXT,                   -- NULL = วันที่ข้าม ← ข้อมูลที่มีค่าที่สุดในตารางนี้
  focus_note     TEXT,
  tasks_done     INTEGER,
  tasks_carried  INTEGER,
  minutes_logged INTEGER,
  self_rating    INTEGER CHECK (self_rating BETWEEN 1 AND 5)
);

-- ---------- ไอเดีย -----------------------------------------------------------

CREATE TABLE IF NOT EXISTS ideas (
  id                INTEGER PRIMARY KEY,
  title             TEXT NOT NULL,
  body              TEXT,
  tags              TEXT,
  status            TEXT NOT NULL DEFAULT 'raw'
                      CHECK (status IN ('raw','analysed','doing','parked','dropped')),
  created_at        TEXT NOT NULL,
  linked_project_id INTEGER REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS idea_analyses (
  id                INTEGER PRIMARY KEY,
  idea_id           INTEGER NOT NULL REFERENCES ideas(id) ON DELETE CASCADE,
  model             TEXT,
  feasibility_score INTEGER CHECK (feasibility_score BETWEEN 1 AND 10),
  effort            TEXT,
  impact            TEXT,
  risks_json        TEXT,
  next_steps_json   TEXT,
  created_at        TEXT NOT NULL     -- เก็บทุกรอบ ไม่ทับของเดิม
);
