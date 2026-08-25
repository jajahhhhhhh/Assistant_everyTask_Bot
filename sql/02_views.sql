-- =============================================================================
--  Life OS — 02_views.sql
--  หมวดวิเคราะห์คอขวด (พิมพ์เขียวหมวด 06) แบบรันได้จริง
--
--  ต้องรัน 01_schema.sql ก่อน
--  ทดสอบบน SQLite 3.45 — ใช้เฉพาะฟีเจอร์ที่มีตั้งแต่ 3.28 (window functions + WITH ใน view)
--  ไม่ใช้ strftime %V/%G ที่ต้องการ 3.46 ขึ้นไป จึงรันได้บน Railway/Debian รุ่นเก่ากว่าด้วย
--
--  ลำดับการอ่าน
--    ส่วน 0  ค่าตั้งต้น + view พื้นฐานที่ตัวอื่นต่อยอด
--    ส่วน 1  คำถามที่ 1 — ทำไมทำงานช้า
--    ส่วน 2  คำถามที่ 2 — คอขวดอยู่ตรงไหน
--    ส่วน 3  คำถามที่ 3 — เงินรั่วไปกับอะไร
--    ส่วน 4  สุขภาพการบันทึก
--    ส่วน 5  รายงานวันจันทร์ + สัญญาณเตือน (สองตัวนี้คือของที่บอทเรียกใช้จริง)
-- =============================================================================


-- ############################################################################
--  ส่วน 0 — ค่าตั้งต้นและ view พื้นฐาน
-- ############################################################################

-- ปรับเกณฑ์ทุกอย่างที่นี่ที่เดียว ไม่ต้องไปไล่แก้ทีละ view
DROP VIEW IF EXISTS cfg;
CREATE VIEW cfg AS
SELECT
  '+7 hours' AS tz_offset,                 -- Asia/Bangkok
  0.15       AS flow_eff_floor,            -- ต่ำกว่านี้ = ปัญหาคือการรอ ไม่ใช่ความเร็ว
  3          AS wip_ceiling,               -- งานที่เปิดค้างพร้อมกันเกินนี้ = เริ่มถ่วงกันเอง
  5          AS wip_breach_days,           -- เกินเพดานติดกันกี่วันถึงเตือน
  72         AS stale_hours,               -- ไม่ขยับกี่ชั่วโมงถือว่าค้าง
  3          AS carryover_moves,           -- เลื่อนกี่ครั้งถือว่าจะไม่มีวันเสร็จ
  300.0      AS small_expense_max,         -- นิยาม "รายการเล็ก" (บาท)
  8          AS small_leak_min_count,      -- เล็กแต่ซ้ำกี่ครั้งถึงเรียกว่ารั่ว
  20.0       AS wait_alert_hours,          -- รอคนคนเดียวเกินกี่ชม./สัปดาห์ ถึงเตือน
  4.0        AS reply_alert_hours,         -- คุณตอบช้าเกินกี่ชม.โดยเฉลี่ย ถึงเตือน
  3          AS recurring_min_months,      -- โผล่ติดกันกี่เดือนถือว่าเป็นรายจ่ายประจำ
  3          AS switch_ceiling,            -- สลับโปรเจกต์เกินกี่ตัวต่อวันถือว่าเยอะ
  0.80       AS min_classifier_confidence; -- ต่ำกว่านี้ อย่าเชื่อผลคัดแยกของ AI

-- ---- นาทีที่ลงมือทำจริงต่องาน ------------------------------------------------
-- ใช้ work_sessions ก่อน ถ้ายังไม่ได้เก็บ ถอยไปใช้ tasks.actual_min
DROP VIEW IF EXISTS v_task_touch;
CREATE VIEW v_task_touch AS
SELECT
  t.id AS task_id,
  COALESCE(
    (SELECT SUM(COALESCE(
                 ws.minutes,
                 CASE WHEN ws.ended_at IS NOT NULL
                      THEN (julianday(ws.ended_at) - julianday(ws.started_at)) * 1440.0 END))
       FROM work_sessions ws WHERE ws.task_id = t.id),
    t.actual_min,
    0
  ) AS touch_min
FROM tasks t;

-- ---- นาทีที่ติดรอต่องาน ------------------------------------------------------
-- block ที่ยังไม่ปิด นับถึงตอนนี้
DROP VIEW IF EXISTS v_task_wait;
CREATE VIEW v_task_wait AS
SELECT
  t.id AS task_id,
  COALESCE((SELECT SUM((julianday(COALESCE(b.unblocked_at, 'now')) - julianday(b.blocked_at)) * 1440.0)
              FROM task_blocks b WHERE b.task_id = t.id), 0) AS wait_min,
  (SELECT COUNT(*) FROM task_blocks b WHERE b.task_id = t.id) AS block_count,
  (SELECT COUNT(*) FROM task_blocks b WHERE b.task_id = t.id AND b.unblocked_at IS NULL) AS open_blocks
FROM tasks t;

-- ---- งานที่ปิดแล้ว พร้อมตัวเลขครบชุด ----------------------------------------
-- นี่คือ view ที่ทุกอย่างในส่วน 1 ต่อยอดออกไป
DROP VIEW IF EXISTS v_task_cycle;
CREATE VIEW v_task_cycle AS
SELECT
  t.id                AS task_id,
  t.title,
  t.project_id,
  p.name              AS project_name,
  COALESCE(t.category,'(ไม่ระบุประเภท)') AS category,
  datetime(t.completed_at, cfg.tz_offset)                                   AS completed_local,
  date(datetime(t.completed_at, cfg.tz_offset), 'weekday 0', '-6 days')     AS week_start,
  strftime('%Y-%m', datetime(t.completed_at, cfg.tz_offset))                AS ym,
  ROUND((julianday(t.completed_at) - julianday(t.created_at)) * 1440.0, 1)  AS cycle_min,
  ROUND(tt.touch_min, 1)                                                    AS touch_min,
  ROUND(tw.wait_min, 1)                                                     AS wait_min,
  tw.block_count,
  CASE WHEN (julianday(t.completed_at) - julianday(t.created_at)) * 1440.0 > 0
       THEN ROUND(tt.touch_min / ((julianday(t.completed_at) - julianday(t.created_at)) * 1440.0), 4)
  END                                                                       AS flow_eff,
  t.estimate_min,
  t.due_moved_count
FROM tasks t
CROSS JOIN cfg
LEFT JOIN projects   p  ON p.id = t.project_id
JOIN v_task_touch    tt ON tt.task_id = t.id
JOIN v_task_wait     tw ON tw.task_id = t.id
WHERE t.status = 'done' AND t.completed_at IS NOT NULL;

-- ---- ช่วงวันที่ที่มีข้อมูล ----------------------------------------------------
DROP VIEW IF EXISTS v_day_bounds;
CREATE VIEW v_day_bounds AS
SELECT MIN(d) AS d0, MAX(d) AS d1 FROM (
  SELECT date(datetime(t.created_at, cfg.tz_offset)) AS d FROM tasks t, cfg
  UNION ALL SELECT date(datetime(e.paid_at,  cfg.tz_offset)) FROM expenses e, cfg
  UNION ALL SELECT date(datetime(m.sent_at,  cfg.tz_offset)) FROM chat_messages m, cfg
  UNION ALL SELECT date(datetime('now', cfg.tz_offset)) FROM cfg
);

-- ---- ปฏิทินวัน ใช้เป็นแกนของกราฟรายวัน ---------------------------------------
DROP VIEW IF EXISTS v_days;
CREATE VIEW v_days AS
WITH RECURSIVE d(day) AS (
  SELECT d0 FROM v_day_bounds WHERE d0 IS NOT NULL
  UNION ALL
  SELECT date(day, '+1 day') FROM d WHERE day < (SELECT d1 FROM v_day_bounds)
)
SELECT day FROM d;

-- ---- ทุกครั้งที่คุณ "แตะ" โปรเจกต์ ในหนึ่งวัน ---------------------------------
DROP VIEW IF EXISTS v_touch_log;
CREATE VIEW v_touch_log AS
  SELECT date(datetime(ws.started_at, cfg.tz_offset)) AS day, t.project_id, 'work' AS src
    FROM work_sessions ws, cfg JOIN tasks t ON t.id = ws.task_id
   WHERE t.project_id IS NOT NULL
UNION ALL
  SELECT date(datetime(ev.at, cfg.tz_offset)), t.project_id, 'event'
    FROM task_events ev, cfg JOIN tasks t ON t.id = ev.task_id
   WHERE t.project_id IS NOT NULL
UNION ALL
  SELECT date(datetime(m.sent_at, cfg.tz_offset)), COALESCE(m.project_id, th.project_id), 'chat'
    FROM chat_messages m, cfg JOIN chat_threads th ON th.id = m.thread_id
   WHERE m.direction = 'out' AND COALESCE(m.project_id, th.project_id) IS NOT NULL
UNION ALL
  SELECT date(datetime(e.paid_at, cfg.tz_offset)), e.project_id, 'expense'
    FROM expenses e, cfg
   WHERE e.project_id IS NOT NULL;


-- ############################################################################
--  ส่วน 1 — คำถามที่ 1: ทำไมทำงานช้า
-- ############################################################################

-- 1.1  Flow efficiency รายสัปดาห์ — ตัวเลขเดียวที่สำคัญที่สุด
--      ต่ำกว่า flow_eff_floor แปลว่าปัญหาไม่ได้อยู่ที่ความเร็วของคุณ
DROP VIEW IF EXISTS v_flow_weekly;
CREATE VIEW v_flow_weekly AS
SELECT
  c.week_start,
  COUNT(*)                                              AS tasks_done,
  ROUND(SUM(c.touch_min) / 60.0, 1)                     AS touch_hours,
  ROUND(SUM(c.cycle_min) / 60.0, 1)                     AS cycle_hours,
  ROUND(SUM(c.wait_min)  / 60.0, 1)                     AS wait_hours,
  CASE WHEN SUM(c.cycle_min) > 0
       THEN ROUND(SUM(c.touch_min) * 100.0 / SUM(c.cycle_min), 1) END AS flow_eff_pct,
  ROUND(AVG(c.cycle_min) / 1440.0, 2)                   AS avg_cycle_days,
  ROUND(MAX(c.cycle_min) / 1440.0, 2)                   AS worst_cycle_days
FROM v_task_cycle c
GROUP BY c.week_start
ORDER BY c.week_start DESC;

-- 1.2  Flow efficiency แยกตามโปรเจกต์ — บอกว่าธุรกิจไหนกินเวลารอมากที่สุด
DROP VIEW IF EXISTS v_flow_weekly_by_project;
CREATE VIEW v_flow_weekly_by_project AS
SELECT
  c.week_start,
  COALESCE(c.project_name, '(ไม่ระบุโปรเจกต์)')          AS project_name,
  COUNT(*)                                              AS tasks_done,
  ROUND(SUM(c.touch_min) / 60.0, 1)                     AS touch_hours,
  ROUND(SUM(c.wait_min)  / 60.0, 1)                     AS wait_hours,
  CASE WHEN SUM(c.cycle_min) > 0
       THEN ROUND(SUM(c.touch_min) * 100.0 / SUM(c.cycle_min), 1) END AS flow_eff_pct
FROM v_task_cycle c
GROUP BY c.week_start, project_name
ORDER BY c.week_start DESC, flow_eff_pct ASC;

-- 1.3  แถบเวลาของงานรายชิ้น — ข้อมูลดิบของรูป "รอ 96% ของเวลา" ในพิมพ์เขียว
DROP VIEW IF EXISTS v_task_timeline;
CREATE VIEW v_task_timeline AS
SELECT
  c.task_id, c.title, c.project_name,
  ROUND(c.cycle_min / 1440.0, 2)   AS cycle_days,
  ROUND(c.touch_min, 0)            AS touch_min,
  ROUND(c.wait_min / 60.0, 1)      AS wait_hours,
  ROUND(COALESCE(c.flow_eff,0) * 100, 1) AS flow_eff_pct,
  c.block_count,
  (SELECT GROUP_CONCAT(x, ' · ') FROM (
     SELECT COALESCE(ct.display_name, b.reason)
            || ' ' || CAST(ROUND((julianday(COALESCE(b.unblocked_at,'now')) - julianday(b.blocked_at)) * 24.0) AS INT) || ' ชม.' AS x
       FROM task_blocks b LEFT JOIN contacts ct ON ct.id = b.contact_id
      WHERE b.task_id = c.task_id
      ORDER BY b.blocked_at
   )) AS wait_breakdown
FROM v_task_cycle c
ORDER BY c.cycle_min DESC;

-- 1.4  WIP รายวัน — กฎของ Little: cycle_time ≈ WIP ÷ throughput
DROP VIEW IF EXISTS v_wip_daily;
CREATE VIEW v_wip_daily AS
SELECT
  d.day,
  (SELECT COUNT(*)
     FROM tasks t, cfg
    WHERE t.status <> 'dropped'
      AND t.started_at IS NOT NULL
      AND date(datetime(t.started_at, cfg.tz_offset)) <= d.day
      AND (t.completed_at IS NULL
           OR date(datetime(t.completed_at, cfg.tz_offset)) >= d.day)) AS wip,
  (SELECT COUNT(*)
     FROM tasks t, cfg
    WHERE t.started_at IS NOT NULL
      AND date(datetime(t.completed_at, cfg.tz_offset)) = d.day)       AS finished_today
FROM v_days d;

-- 1.5  ช่วงที่ WIP เกินเพดานติดกันหลายวัน — ใช้เทคนิค gaps-and-islands
DROP VIEW IF EXISTS v_wip_breaches;
CREATE VIEW v_wip_breaches AS
WITH over AS (
  SELECT w.day, w.wip,
         julianday(w.day) - ROW_NUMBER() OVER (ORDER BY w.day) AS grp
    FROM v_wip_daily w, cfg
   WHERE w.wip > cfg.wip_ceiling
)
SELECT MIN(day) AS from_day, MAX(day) AS to_day,
       COUNT(*) AS days_in_a_row, MAX(wip) AS peak_wip
  FROM over
 GROUP BY grp
HAVING COUNT(*) >= (SELECT wip_breach_days FROM cfg)
 ORDER BY from_day DESC;

-- 1.6  ประเมินเวลาพลาดซ้ำที่เรื่องอะไร
--      ratio 3.4 แปลว่างานประเภทนี้ใช้เวลาจริง 3.4 เท่าของที่คุณคิดเสมอ — เอาไปคูณในปฏิทินได้เลย
DROP VIEW IF EXISTS v_estimate_accuracy;
CREATE VIEW v_estimate_accuracy AS
SELECT
  c.category,
  COUNT(*)                                    AS samples,
  ROUND(AVG(c.estimate_min), 0)               AS avg_estimate_min,
  ROUND(AVG(c.touch_min), 0)                  AS avg_actual_min,
  ROUND(AVG(c.touch_min * 1.0 / c.estimate_min), 2) AS ratio,
  ROUND(MAX(c.touch_min * 1.0 / c.estimate_min), 2) AS worst_ratio
FROM v_task_cycle c
WHERE c.estimate_min > 0 AND c.touch_min > 0
GROUP BY c.category
HAVING COUNT(*) >= 3          -- ต่ำกว่านี้ยังไม่ใช่รูปแบบ เป็นแค่ความบังเอิญ
ORDER BY ratio DESC;

-- 1.7  สลับบริบทกี่ครั้งต่อวัน
DROP VIEW IF EXISTS v_context_switches_daily;
CREATE VIEW v_context_switches_daily AS
SELECT
  d.day,
  (SELECT COUNT(DISTINCT tl.project_id) FROM v_touch_log tl WHERE tl.day = d.day) AS projects_touched,
  (SELECT COUNT(*)                      FROM v_touch_log tl WHERE tl.day = d.day) AS touches
FROM v_days d;

-- 1.8  เอาสองเส้นมาทับกัน — สัปดาห์ที่สลับโปรเจกต์เยอะ มักเป็นสัปดาห์ที่ flow ตก
DROP VIEW IF EXISTS v_flow_vs_switching_weekly;
CREATE VIEW v_flow_vs_switching_weekly AS
SELECT
  f.week_start,
  f.flow_eff_pct,
  f.tasks_done,
  ROUND((SELECT AVG(s.projects_touched)
           FROM v_context_switches_daily s
          WHERE s.day BETWEEN f.week_start AND date(f.week_start, '+6 days')
            AND s.projects_touched > 0), 2) AS avg_projects_per_day
FROM v_flow_weekly f
ORDER BY f.week_start DESC;


-- ############################################################################
--  ส่วน 2 — คำถามที่ 2: คอขวดอยู่ตรงไหน
-- ############################################################################

-- 2.1  ทุกช่วงที่ติดรอ พร้อมชั่วโมงที่เสียไป (view ฐานของส่วนนี้)
--      ช่วงที่คร่อมเดือน ถูกนับเข้าเดือนที่เริ่มติด
DROP VIEW IF EXISTS v_wait_spans;
CREATE VIEW v_wait_spans AS
SELECT
  b.id AS block_id, b.task_id, t.title, t.project_id, p.name AS project_name,
  b.contact_id,
  COALESCE(ct.display_name, '(ไม่ได้ติดที่คน)')                     AS person,
  b.reason,
  datetime(b.blocked_at, cfg.tz_offset)                            AS blocked_local,
  strftime('%Y-%m', datetime(b.blocked_at, cfg.tz_offset))         AS ym,
  date(datetime(b.blocked_at, cfg.tz_offset), 'weekday 0', '-6 days') AS week_start,
  (b.unblocked_at IS NULL)                                         AS still_waiting,
  ROUND((julianday(COALESCE(b.unblocked_at, 'now')) - julianday(b.blocked_at)) * 24.0, 2) AS wait_hours
FROM task_blocks b
CROSS JOIN cfg
JOIN tasks t       ON t.id = b.task_id
LEFT JOIN projects p  ON p.id = t.project_id
LEFT JOIN contacts ct ON ct.id = b.contact_id;

-- 2.2  เวลารอ แยกตาม "คน" ไม่ใช่แยกตามงาน
--      ผลลัพธ์คือประโยคที่เอาไปคุยกับคนนั้นได้จริง
DROP VIEW IF EXISTS v_wait_by_person_month;
CREATE VIEW v_wait_by_person_month AS
SELECT
  ws.ym,
  ws.person,
  ws.contact_id,
  COUNT(*)                       AS times_blocked,
  ROUND(SUM(ws.wait_hours), 1)   AS wait_hours,
  ROUND(AVG(ws.wait_hours), 1)   AS avg_hours_each,
  ROUND(MAX(ws.wait_hours), 1)   AS worst_hours,
  SUM(ws.still_waiting)          AS still_open
FROM v_wait_spans ws
GROUP BY ws.ym, ws.person, ws.contact_id
ORDER BY ws.ym DESC, wait_hours DESC;

DROP VIEW IF EXISTS v_wait_by_person_week;
CREATE VIEW v_wait_by_person_week AS
SELECT
  ws.week_start, ws.person, ws.contact_id,
  COUNT(*) AS times_blocked,
  ROUND(SUM(ws.wait_hours), 1) AS wait_hours,
  SUM(ws.still_waiting) AS still_open
FROM v_wait_spans ws
GROUP BY ws.week_start, ws.person, ws.contact_id
ORDER BY ws.week_start DESC, wait_hours DESC;

-- 2.3  เวลารอ แยกตามสาเหตุ — บอกว่าควรแก้ที่กระบวนการหรือแก้ที่คน
DROP VIEW IF EXISTS v_wait_by_reason;
CREATE VIEW v_wait_by_reason AS
SELECT
  ws.ym, ws.reason,
  COUNT(*)                     AS times,
  ROUND(SUM(ws.wait_hours), 1) AS wait_hours,
  ROUND(AVG(ws.wait_hours), 1) AS avg_hours,
  ROUND(SUM(ws.wait_hours) * 100.0 /
        (SELECT SUM(w2.wait_hours) FROM v_wait_spans w2 WHERE w2.ym = ws.ym), 1) AS pct_of_month
FROM v_wait_spans ws
GROUP BY ws.ym, ws.reason
ORDER BY ws.ym DESC, wait_hours DESC;

-- 2.4  ที่ยังค้างอยู่ตอนนี้ — ของที่ควรไปตามวันนี้
DROP VIEW IF EXISTS v_blocked_now;
CREATE VIEW v_blocked_now AS
SELECT
  ws.task_id, ws.title, ws.project_name, ws.person, ws.reason,
  ws.blocked_local AS waiting_since,
  ROUND(ws.wait_hours, 1)        AS waiting_hours,
  ROUND(ws.wait_hours / 24.0, 1) AS waiting_days
FROM v_wait_spans ws
WHERE ws.still_waiting = 1
ORDER BY ws.wait_hours DESC;

-- 2.5  แล้วคุณเป็นคอขวดของใคร (view ฐาน)
--      ใช้ responded_at ถ้าแอปเติมไว้ ถ้าไม่มีก็หาข้อความขาออกถัดไปในเธรดเดียวกัน
--
--      ข้อควรรู้เรื่องกลุ่มแชท: ในกลุ่ม การตอบครั้งเดียวของคุณจะถูกนับว่าตอบทุกคน
--      การวัด "คุณตอบใครช้า" จึงแม่นเฉพาะแชท 1:1 — ดู v_reply_latency_by_contact (1:1 เท่านั้น)
--      ส่วนกลุ่มให้ดูที่ v_reply_latency_by_thread ซึ่งวัดระดับเธรด ไม่ใช่ระดับคน
DROP VIEW IF EXISTS v_reply_latency;
CREATE VIEW v_reply_latency AS
SELECT
  m.id AS message_id,
  m.thread_id,
  th.title       AS thread_title,
  th.platform,
  th.is_group,
  m.contact_id,
  COALESCE(ct.display_name, '(ไม่ทราบผู้ส่ง)') AS person,
  m.intent,
  m.urgency,
  datetime(m.sent_at, cfg.tz_offset)                              AS asked_local,
  date(datetime(m.sent_at, cfg.tz_offset), 'weekday 0', '-6 days') AS week_start,
  strftime('%Y-%m', datetime(m.sent_at, cfg.tz_offset))           AS ym,
  r.replied_at,
  CASE WHEN r.replied_at IS NULL THEN 1 ELSE 0 END                AS unanswered,
  ROUND((julianday(COALESCE(r.replied_at, 'now')) - julianday(m.sent_at)) * 24.0, 2) AS latency_hours
FROM chat_messages m
CROSS JOIN cfg
JOIN chat_threads th  ON th.id = m.thread_id
LEFT JOIN contacts ct ON ct.id = m.contact_id
LEFT JOIN (
  SELECT m2.id AS mid,
         COALESCE(m2.responded_at,
                  (SELECT MIN(o.sent_at) FROM chat_messages o
                    WHERE o.thread_id = m2.thread_id
                      AND o.direction = 'out'
                      AND o.sent_at > m2.sent_at)) AS replied_at
    FROM chat_messages m2
   WHERE m2.direction = 'in'
) r ON r.mid = m.id
WHERE m.direction = 'in'
  AND m.intent IN ('request','question')
  AND COALESCE(m.confidence, 1.0) >= (SELECT min_classifier_confidence FROM cfg);

-- 2.6  เวลาตอบของคุณ แยกตามคน — ด้านที่คนส่วนใหญ่ไม่กล้าวัด
--      นับเฉพาะแชท 1:1 เพื่อให้การชี้ตัวคนแม่นจริง
DROP VIEW IF EXISTS v_reply_latency_by_contact;
CREATE VIEW v_reply_latency_by_contact AS
--      รวมคนเดียวกันจากทุกแพลตฟอร์มเป็นแถวเดียว — Farid ใน LINE, WhatsApp และ Messenger คือคนคนเดียว
--      ถ้าแยกตามแพลตฟอร์ม ตัวเลขจะถูกหารจนไม่เห็นปัญหา
SELECT
  rl.ym, rl.person, rl.contact_id,
  GROUP_CONCAT(DISTINCT rl.platform)              AS platforms,
  COUNT(*)                                        AS asks,
  SUM(rl.unanswered)                              AS still_unanswered,
  ROUND(AVG(CASE WHEN rl.unanswered = 0 THEN rl.latency_hours END), 1) AS avg_reply_hours,
  ROUND(MAX(rl.latency_hours), 1)                 AS worst_hours,
  ROUND(SUM(CASE WHEN rl.unanswered = 0 AND rl.latency_hours <= 4 THEN 1 ELSE 0 END)
        * 100.0 / COUNT(*), 0)                    AS pct_within_4h
FROM v_reply_latency rl
WHERE rl.is_group = 0
GROUP BY rl.ym, rl.person, rl.contact_id
ORDER BY rl.ym DESC, avg_reply_hours DESC;

-- 2.6b เวลาตอบระดับเธรด — ใช้กับกลุ่มแชท ซึ่งชี้ตัวคนไม่ได้
DROP VIEW IF EXISTS v_reply_latency_by_thread;
CREATE VIEW v_reply_latency_by_thread AS
SELECT
  rl.ym, rl.thread_title, rl.platform, rl.is_group,
  COUNT(*)                                        AS asks,
  SUM(rl.unanswered)                              AS still_unanswered,
  ROUND(AVG(CASE WHEN rl.unanswered = 0 THEN rl.latency_hours END), 1) AS avg_reply_hours,
  ROUND(MAX(rl.latency_hours), 1)                 AS worst_hours
FROM v_reply_latency rl
GROUP BY rl.ym, rl.thread_id, rl.thread_title, rl.platform, rl.is_group
ORDER BY rl.ym DESC, avg_reply_hours DESC;

-- 2.7  คำถามที่ยังไม่ได้ตอบ เรียงจากค้างนานที่สุด
DROP VIEW IF EXISTS v_unanswered_now;
CREATE VIEW v_unanswered_now AS
SELECT
  rl.message_id, rl.platform, rl.thread_title, rl.person, rl.intent, rl.urgency,
  rl.asked_local,
  ROUND(rl.latency_hours, 1)        AS waiting_hours,
  (SELECT substr(m.body,1,80) FROM chat_messages m WHERE m.id = rl.message_id) AS preview
FROM v_reply_latency rl
WHERE rl.unanswered = 1
ORDER BY rl.latency_hours DESC;

-- 2.8  งานที่ค้างที่สถานะเดิมนานผิดปกติ — รายการสำหรับเช้าวันจันทร์
DROP VIEW IF EXISTS v_stale_tasks;
CREATE VIEW v_stale_tasks AS
SELECT
  t.id AS task_id, t.title, p.name AS project_name, t.status,
  datetime(COALESCE((SELECT MAX(ev.at) FROM task_events ev WHERE ev.task_id = t.id),
                    t.started_at, t.created_at), cfg.tz_offset) AS last_moved_local,
  ROUND((julianday('now') - julianday(COALESCE(
           (SELECT MAX(ev.at) FROM task_events ev WHERE ev.task_id = t.id),
           t.started_at, t.created_at))) * 24.0, 1) AS stale_hours,
  t.due_moved_count,
  (SELECT ct.display_name FROM task_blocks b LEFT JOIN contacts ct ON ct.id = b.contact_id
    WHERE b.task_id = t.id AND b.unblocked_at IS NULL ORDER BY b.blocked_at LIMIT 1) AS waiting_on
FROM tasks t
CROSS JOIN cfg
LEFT JOIN projects p ON p.id = t.project_id
WHERE t.status IN ('inbox','doing','blocked')
  AND (julianday('now') - julianday(COALESCE(
        (SELECT MAX(ev.at) FROM task_events ev WHERE ev.task_id = t.id),
        t.started_at, t.created_at))) * 24.0 >= cfg.stale_hours
ORDER BY stale_hours DESC;

-- 2.9  อัตราการเลื่อน — งานที่เลื่อนครบสามครั้งแทบไม่เคยเสร็จ ควรถูกบังคับให้ตัดสินใจ
DROP VIEW IF EXISTS v_carryover;
CREATE VIEW v_carryover AS
SELECT
  COALESCE(p.name,'(ไม่ระบุโปรเจกต์)') AS project_name,
  COUNT(*)                              AS open_tasks,
  SUM(CASE WHEN t.due_moved_count >= cfg.carryover_moves THEN 1 ELSE 0 END) AS chronic,
  ROUND(SUM(CASE WHEN t.due_moved_count >= cfg.carryover_moves THEN 1 ELSE 0 END)
        * 100.0 / COUNT(*), 1)          AS chronic_pct,
  ROUND(AVG(t.due_moved_count), 2)      AS avg_moves
FROM tasks t
CROSS JOIN cfg
LEFT JOIN projects p ON p.id = t.project_id
WHERE t.status IN ('inbox','doing','blocked')
GROUP BY project_name
ORDER BY chronic_pct DESC;

DROP VIEW IF EXISTS v_carryover_tasks;
CREATE VIEW v_carryover_tasks AS
SELECT t.id AS task_id, t.title, p.name AS project_name, t.status,
       t.due_moved_count, date(datetime(t.due_at, cfg.tz_offset)) AS due_local
FROM tasks t CROSS JOIN cfg
LEFT JOIN projects p ON p.id = t.project_id
WHERE t.status IN ('inbox','doing','blocked')
  AND t.due_moved_count >= cfg.carryover_moves
ORDER BY t.due_moved_count DESC;

-- 2.10 บรรทัดเดียวที่รายงานวันจันทร์ต้องการ: "คอขวดสัปดาห์นี้คือ…"
DROP VIEW IF EXISTS v_bottleneck_weekly;
CREATE VIEW v_bottleneck_weekly AS
SELECT
  f.week_start,
  f.flow_eff_pct,
  f.wait_hours,
  (SELECT wp.person FROM v_wait_by_person_week wp
    WHERE wp.week_start = f.week_start AND wp.contact_id IS NOT NULL
    ORDER BY wp.wait_hours DESC LIMIT 1)      AS top_person,
  (SELECT ROUND(wp.wait_hours,1) FROM v_wait_by_person_week wp
    WHERE wp.week_start = f.week_start AND wp.contact_id IS NOT NULL
    ORDER BY wp.wait_hours DESC LIMIT 1)      AS top_person_hours,
  (SELECT ws.reason FROM v_wait_spans ws
    WHERE ws.week_start = f.week_start
    GROUP BY ws.reason ORDER BY SUM(ws.wait_hours) DESC LIMIT 1) AS top_reason,
  (SELECT ROUND(AVG(rl.latency_hours),1) FROM v_reply_latency rl
    WHERE rl.week_start = f.week_start AND rl.unanswered = 0)    AS my_avg_reply_hours
FROM v_flow_weekly f
ORDER BY f.week_start DESC;


-- ############################################################################
--  ส่วน 3 — คำถามที่ 3: เงินรั่วไปกับอะไร
--  ทุก view ในส่วนนี้นับเฉพาะรายการที่ verified_by_user = 1
--  รายการที่ AI แกะแล้วคุณยังไม่ยืนยัน ไม่ควรถูกนับในรายงาน — ดู v_spend_unverified
-- ############################################################################

DROP VIEW IF EXISTS v_expense_local;
CREATE VIEW v_expense_local AS
SELECT
  e.*,
  datetime(e.paid_at, cfg.tz_offset)                              AS paid_local,
  date(datetime(e.paid_at, cfg.tz_offset))                        AS paid_day,
  strftime('%Y-%m', datetime(e.paid_at, cfg.tz_offset))           AS ym,
  date(datetime(e.paid_at, cfg.tz_offset), 'weekday 0', '-6 days') AS week_start,
  CAST(strftime('%Y', datetime(e.paid_at, cfg.tz_offset)) AS INTEGER) * 12
    + CAST(strftime('%m', datetime(e.paid_at, cfg.tz_offset)) AS INTEGER) AS month_index
FROM expenses e CROSS JOIN cfg;

-- 3.1  รายจ่ายรายเดือนแยกหมวด พร้อมเทียบเดือนก่อนหน้า
--      จับ "หมวดที่โตเร็วที่สุด" ไม่ใช่ "หมวดที่ใหญ่ที่สุด" — ค่าเช่าใหญ่ที่สุดเสมอและคุณรู้อยู่แล้ว
DROP VIEW IF EXISTS v_spend_month_category;
CREATE VIEW v_spend_month_category AS
WITH m AS (
  SELECT ym, month_index, COALESCE(category,'(ไม่ระบุหมวด)') AS category,
         SUM(amount) AS total, COUNT(*) AS n
    FROM v_expense_local
   WHERE amount > 0 AND verified_by_user = 1
   GROUP BY ym, month_index, category
),
lagged AS (
  SELECT m.*,
         LAG(total)       OVER (PARTITION BY category ORDER BY month_index) AS prev_total,
         LAG(month_index) OVER (PARTITION BY category ORDER BY month_index) AS prev_mi
    FROM m
)
SELECT
  ym, category,
  ROUND(total, 2) AS total,
  n,
  CASE WHEN prev_mi = month_index - 1 THEN ROUND(prev_total, 2) END              AS prev_total,
  CASE WHEN prev_mi = month_index - 1 THEN ROUND(total - prev_total, 2) END      AS delta,
  CASE WHEN prev_mi = month_index - 1 AND prev_total > 0
       THEN ROUND((total - prev_total) * 100.0 / prev_total, 1) END              AS growth_pct,
  ROUND(total * 100.0 / SUM(total) OVER (PARTITION BY ym), 1)                    AS pct_of_month
FROM lagged
ORDER BY ym DESC, total DESC;

-- 3.2  หมวดที่โตเร็วที่สุดในเดือนล่าสุด — ของที่ควรเด้งเตือน
DROP VIEW IF EXISTS v_spend_fastest_growing;
CREATE VIEW v_spend_fastest_growing AS
SELECT ym, category, total, prev_total, delta, growth_pct, pct_of_month
FROM v_spend_month_category
WHERE growth_pct IS NOT NULL
  AND ym = (SELECT MAX(ym) FROM v_spend_month_category)
  AND delta > 0
ORDER BY growth_pct DESC;

-- 3.3  ตรวจรอยรั่วรายการเล็ก
--      นี่คือเหตุผลที่ช่อง note สำคัญกว่า category —
--      "อาหาร ฿12,000" ไร้ประโยชน์ แต่ "กาแฟหน้าร้าน 43 ครั้ง ฿4,120" เปลี่ยนพฤติกรรมได้
DROP VIEW IF EXISTS v_spend_small_leaks;
CREATE VIEW v_spend_small_leaks AS
SELECT
  e.ym,
  COALESCE(NULLIF(TRIM(e.note),''), e.merchant, '(ไม่มีหมายเหตุ)') AS label,
  COUNT(*)                 AS times,
  ROUND(AVG(e.amount), 0)  AS avg_each,
  ROUND(SUM(e.amount), 0)  AS total,
  ROUND(SUM(e.amount) * 12, 0) AS annual_run_rate   -- ถ้าเดือนไหนก็เป็นแบบนี้ ทั้งปีคือเท่านี้
FROM v_expense_local e, cfg
WHERE e.amount > 0 AND e.verified_by_user = 1
  AND e.amount < cfg.small_expense_max
GROUP BY e.ym, label
HAVING COUNT(*) >= (SELECT small_leak_min_count FROM cfg)
ORDER BY e.ym DESC, total DESC;

-- 3.4  รายจ่ายประจำที่ซ่อนอยู่ — ร้านเดิมที่โผล่ติดกันหลายเดือน
--      gaps-and-islands บนดัชนีเดือน
DROP VIEW IF EXISTS v_spend_recurring;
CREATE VIEW v_spend_recurring AS
WITH m AS (
  SELECT COALESCE(NULLIF(TRIM(merchant),''), NULLIF(TRIM(note),''), '(ไม่ระบุ)') AS label,
         ym, month_index, SUM(amount) AS total, COUNT(*) AS n
    FROM v_expense_local
   WHERE amount > 0 AND verified_by_user = 1
   GROUP BY label, ym, month_index
),
grp AS (
  SELECT m.*, month_index - ROW_NUMBER() OVER (PARTITION BY label ORDER BY month_index) AS island
    FROM m
)
SELECT
  label,
  COUNT(*)                    AS months_in_a_row,
  MIN(ym)                     AS from_month,
  MAX(ym)                     AS to_month,
  ROUND(AVG(total), 0)        AS avg_per_month,
  ROUND(SUM(total), 0)        AS total_so_far,
  ROUND(AVG(total) * 12, 0)   AS annual_run_rate
FROM grp
GROUP BY label, island
HAVING COUNT(*) >= (SELECT recurring_min_months FROM cfg)
ORDER BY avg_per_month DESC;

-- 3.5  เงินธุรกิจกับเงินตัวเอง แยกตามโปรเจกต์
DROP VIEW IF EXISTS v_spend_business_split;
CREATE VIEW v_spend_business_split AS
SELECT
  e.ym,
  CASE e.is_business WHEN 1 THEN 'ธุรกิจ' ELSE 'ส่วนตัว' END AS bucket,
  COALESCE(p.name, '(ไม่ระบุโปรเจกต์)')                      AS project_name,
  COUNT(*)                AS n,
  ROUND(SUM(e.amount), 0) AS total,
  ROUND(SUM(e.amount) * 100.0 /
        (SELECT SUM(e2.amount) FROM v_expense_local e2
          WHERE e2.ym = e.ym AND e2.amount > 0 AND e2.verified_by_user = 1), 1) AS pct_of_month
FROM v_expense_local e
LEFT JOIN projects p ON p.id = e.project_id
WHERE e.amount > 0 AND e.verified_by_user = 1
GROUP BY e.ym, bucket, project_name
ORDER BY e.ym DESC, total DESC;

-- 3.6  เงินต่อผลลัพธ์ — แยกโปรเจกต์ที่ "กินเงินแล้วไม่ขยับ" ออกจาก "ใช้เงินเยอะแต่เดินหน้า"
DROP VIEW IF EXISTS v_spend_per_outcome;
CREATE VIEW v_spend_per_outcome AS
SELECT
  e.ym,
  COALESCE(p.name, '(ไม่ระบุโปรเจกต์)') AS project_name,
  ROUND(SUM(e.amount), 0)               AS spend,
  (SELECT COUNT(*) FROM v_task_cycle c
    WHERE c.ym = e.ym AND COALESCE(c.project_id,-1) = COALESCE(e.project_id,-1)) AS tasks_closed,
  CASE WHEN (SELECT COUNT(*) FROM v_task_cycle c
              WHERE c.ym = e.ym AND COALESCE(c.project_id,-1) = COALESCE(e.project_id,-1)) > 0
       THEN ROUND(SUM(e.amount) * 1.0 /
                  (SELECT COUNT(*) FROM v_task_cycle c
                    WHERE c.ym = e.ym AND COALESCE(c.project_id,-1) = COALESCE(e.project_id,-1)), 0)
  END AS spend_per_task,
  ROUND((SELECT COALESCE(SUM(ws.wait_hours),0) FROM v_wait_spans ws
          WHERE ws.ym = e.ym AND COALESCE(ws.project_id,-1) = COALESCE(e.project_id,-1)), 1) AS wait_hours
FROM v_expense_local e
LEFT JOIN projects p ON p.id = e.project_id
WHERE e.amount > 0 AND e.verified_by_user = 1
GROUP BY e.ym, e.project_id, project_name
ORDER BY e.ym DESC, spend DESC;

-- 3.7  รายการที่ AI แกะแล้วแต่ยังไม่ยืนยัน — ต้องเคลียร์ก่อนปิดเดือน
DROP VIEW IF EXISTS v_spend_unverified;
CREATE VIEW v_spend_unverified AS
SELECT
  e.id, e.paid_local, ROUND(e.amount,2) AS amount, e.category, e.note, e.merchant,
  ROUND(COALESCE(e.ocr_confidence,0), 2) AS ocr_confidence,
  e.slip_url,
  ROUND((julianday('now') - julianday(e.created_at)) * 24.0, 1) AS hours_pending
FROM v_expense_local e
WHERE e.verified_by_user = 0
ORDER BY e.ocr_confidence ASC, e.paid_at DESC;


-- ############################################################################
--  ส่วน 4 — สุขภาพการบันทึก
--  ระบบนี้ตายได้ทางเดียว คือคุณเลิกบันทึก ตัวเลขนี้จับสัญญาณนั้นก่อนที่จะสาย
-- ############################################################################

DROP VIEW IF EXISTS v_logging_daily;
CREATE VIEW v_logging_daily AS
SELECT
  d.day,
  CASE WHEN dl.submitted_at IS NOT NULL THEN 1 ELSE 0 END AS logged,
  dl.self_rating,
  (SELECT COUNT(*) FROM v_expense_local e WHERE e.paid_day = d.day) AS expenses_logged
FROM v_days d
LEFT JOIN daily_logs dl ON dl.log_date = d.day;

-- ช่วงที่ขาดการบันทึกติดกัน — ขาดเกินสามวัน แปลว่าต้องลดจำนวนคำถามตอนสองทุ่มครึ่ง ไม่ใช่เพิ่มวินัย
DROP VIEW IF EXISTS v_logging_gaps;
CREATE VIEW v_logging_gaps AS
WITH missed AS (
  SELECT day, julianday(day) - ROW_NUMBER() OVER (ORDER BY day) AS grp
    FROM v_logging_daily
   WHERE logged = 0 AND day <= date(datetime('now', (SELECT tz_offset FROM cfg)))
)
SELECT MIN(day) AS from_day, MAX(day) AS to_day, COUNT(*) AS days_missed
FROM missed
GROUP BY grp
HAVING COUNT(*) >= 2
ORDER BY from_day DESC;


-- ############################################################################
--  ส่วน 5 — ของที่บอทเรียกใช้จริง
-- ############################################################################

-- 5.1  รายงานเช้าวันจันทร์ — หนึ่งแถวต่อสัปดาห์ ครบทุกตัวเลขที่ต้องพูดถึง
DROP VIEW IF EXISTS v_weekly_report;
CREATE VIEW v_weekly_report AS
SELECT
  b.week_start,
  date(b.week_start, '+6 days')                       AS week_end,
  f.tasks_done,
  f.touch_hours,
  f.wait_hours,
  b.flow_eff_pct,
  ROUND(f.avg_cycle_days, 1)                          AS avg_cycle_days,
  b.top_person,
  b.top_person_hours,
  b.top_reason,
  b.my_avg_reply_hours,
  (SELECT ROUND(AVG(w.wip),1) FROM v_wip_daily w
    WHERE w.day BETWEEN b.week_start AND date(b.week_start,'+6 days'))          AS avg_wip,
  (SELECT ROUND(AVG(s.projects_touched),1) FROM v_context_switches_daily s
    WHERE s.day BETWEEN b.week_start AND date(b.week_start,'+6 days')
      AND s.projects_touched > 0)                                              AS avg_projects_per_day,
  (SELECT ROUND(SUM(e.amount),0) FROM v_expense_local e
    WHERE e.week_start = b.week_start AND e.amount > 0 AND e.verified_by_user = 1) AS spend,
  (SELECT COUNT(*) FROM v_logging_daily l
    WHERE l.day BETWEEN b.week_start AND date(b.week_start,'+6 days') AND l.logged = 1) AS days_logged
FROM v_bottleneck_weekly b
JOIN v_flow_weekly f ON f.week_start = b.week_start
ORDER BY b.week_start DESC;

-- 5.2  สัญญาณเตือนทั้งหมดแบบดิบ
--      severity: 1 = ต้องจัดการวันนี้, 2 = ควรดู, 3 = รู้ไว้
--      อย่าส่งตัวนี้เข้า Telegram ตรง ๆ — ใช้ v_alerts ข้างล่างที่ตัดยอดแล้ว
DROP VIEW IF EXISTS v_alerts_raw;
CREATE VIEW v_alerts_raw AS
  -- flow efficiency ตก
  SELECT 1 AS severity, 'flow_low' AS code,
         'Flow efficiency สัปดาห์ที่แล้วอยู่ที่ ' || f.flow_eff_pct
         || '% — ปัญหาไม่ได้อยู่ที่ความเร็วของคุณ ไปดูว่ารอใครอยู่' AS message,
         f.flow_eff_pct AS value, f.week_start AS ref
    FROM v_flow_weekly f, cfg
   WHERE f.week_start = (SELECT MAX(week_start) FROM v_flow_weekly)
     AND f.flow_eff_pct < cfg.flow_eff_floor * 100

UNION ALL
  -- WIP เกินเพดานติดกันหลายวัน
  SELECT 1, 'wip_high',
         'งานเปิดค้างพร้อมกันเกิน ' || (SELECT wip_ceiling FROM cfg) || ' ชิ้น ติดกัน '
         || b.days_in_a_row || ' วัน (สูงสุด ' || b.peak_wip || ') — ปิดให้ได้สองชิ้นก่อนรับงานใหม่',
         b.peak_wip, b.to_day
    FROM v_wip_breaches b
   WHERE b.to_day >= date(datetime('now',(SELECT tz_offset FROM cfg)), '-14 days')

UNION ALL
  -- รอคนคนเดียวนานเกินเกณฑ์
  SELECT 1, 'wait_person',
         'สัปดาห์ที่แล้วเสียไป ' || w.wait_hours || ' ชม. กับการรอ ' || w.person
         || ' (' || w.times_blocked || ' ครั้ง) — เอาตัวเลขนี้ไปคุยกับเขาได้เลย',
         w.wait_hours, w.week_start
    FROM v_wait_by_person_week w, cfg
   WHERE w.contact_id IS NOT NULL
     AND w.week_start = (SELECT MAX(week_start) FROM v_wait_by_person_week)
     AND w.wait_hours >= cfg.wait_alert_hours

UNION ALL
  -- คุณเองตอบช้า
  SELECT 2, 'my_reply_slow',
         'คุณตอบ ' || r.person || ' เฉลี่ย ' || r.avg_reply_hours
         || ' ชม. เดือนนี้ — คอขวดนี้อยู่ที่คุณ แก้ได้ทันที',
         r.avg_reply_hours, r.ym
    FROM v_reply_latency_by_contact r, cfg
   WHERE r.ym = strftime('%Y-%m', datetime('now', cfg.tz_offset))
     AND r.avg_reply_hours >= cfg.reply_alert_hours

UNION ALL
  -- คำถามค้างยังไม่ตอบ
  SELECT 1, 'unanswered',
         u.person || ' ถามค้างไว้ ' || u.waiting_hours || ' ชม. ยังไม่ได้ตอบ: ' || COALESCE(u.preview,''),
         u.waiting_hours, CAST(u.message_id AS TEXT)
    FROM v_unanswered_now u
   WHERE u.waiting_hours >= 24

UNION ALL
  -- งานค้างนิ่ง
  SELECT 2, 'stale_task',
         'งาน "' || s.title || '" ไม่ขยับมา ' || ROUND(s.stale_hours/24.0,1) || ' วัน'
         || COALESCE(' (ติดที่ ' || s.waiting_on || ')', ''),
         s.stale_hours, CAST(s.task_id AS TEXT)
    FROM v_stale_tasks s

UNION ALL
  -- เลื่อนซ้ำจนน่าจะไม่เสร็จ
  SELECT 2, 'carryover',
         'งาน "' || c.title || '" เลื่อนมา ' || c.due_moved_count || ' ครั้งแล้ว — ปิดทิ้ง มอบต่อ หรือตั้งวันจริง',
         c.due_moved_count, CAST(c.task_id AS TEXT)
    FROM v_carryover_tasks c

UNION ALL
  -- หมวดที่โตเร็วผิดปกติ
  SELECT 2, 'spend_growth',
         'หมวด ' || g.category || ' เดือนนี้ ' || g.total || ' บาท โตขึ้น ' || g.growth_pct
         || '% จากเดือนก่อน',
         g.growth_pct, g.ym
    FROM v_spend_fastest_growing g
   WHERE g.growth_pct >= 40

UNION ALL
  -- รอยรั่วรายการเล็ก
  SELECT 3, 'spend_leak',
         l.label || ' ' || l.times || ' ครั้งเดือนนี้ รวม ' || l.total || ' บาท (เฉลี่ยครั้งละ ' || l.avg_each || ')',
         l.total, l.ym
    FROM v_spend_small_leaks l
   WHERE l.ym = strftime('%Y-%m', datetime('now', (SELECT tz_offset FROM cfg)))

UNION ALL
  -- สลิปที่ยังไม่ยืนยัน
  SELECT 3, 'unverified_slips',
         'มีรายการที่ AI แกะแล้วยังไม่ได้ยืนยัน ' || COUNT(*) || ' รายการ — ยังไม่ถูกนับในรายงาน',
         COUNT(*), NULL
    FROM v_spend_unverified
  HAVING COUNT(*) > 0

UNION ALL
  -- ขาดการบันทึก
  SELECT 1, 'logging_gap',
         'ขาดการบันทึก ' || g.days_missed || ' วันติด (' || g.from_day || ' ถึง ' || g.to_day
         || ') — ลดจำนวนคำถามตอนสองทุ่มครึ่งลง อย่าเพิ่มวินัย',
         g.days_missed, g.to_day
    FROM v_logging_gaps g
   WHERE g.days_missed >= 3
     AND g.to_day >= date(datetime('now',(SELECT tz_offset FROM cfg)), '-10 days');

-- 5.3  สัญญาณเตือนที่ส่งจริง — ตัดเหลือ 5 อันดับแรกของแต่ละประเภท
--      งานค้างมี 30 ชิ้นก็จริง แต่ข้อความที่ยาว 30 บรรทัดคือข้อความที่ไม่มีใครอ่าน
--      ถ้าอยากเห็นทั้งหมด เปิดในแดชบอร์ดจาก v_alerts_raw
DROP VIEW IF EXISTS v_alerts;
CREATE VIEW v_alerts AS
WITH ranked AS (
  SELECT a.*,
         ROW_NUMBER() OVER (PARTITION BY a.code ORDER BY a.severity, a.value DESC) AS rn,
         COUNT(*)    OVER (PARTITION BY a.code) AS total_in_code
    FROM v_alerts_raw a
)
SELECT severity, code, message, value, ref,
       CASE WHEN total_in_code > 5 THEN total_in_code - 5 END AS hidden_more
FROM ranked
WHERE rn <= 5
ORDER BY severity, code, value DESC;
