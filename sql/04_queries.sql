-- =============================================================================
--  Life OS — 04_queries.sql
--  ชุดคำสั่งที่บอทกับแดชบอร์ดเรียกใช้จริง คัดลอกไปวางในโค้ดได้เลย
--  ทุกอันอ่านจาก view ในไฟล์ 02 ไม่มี logic ซ้ำในโค้ดแอป
-- =============================================================================


-- ---------------------------------------------------------------------------
--  A. cron เช้าวันจันทร์ 09:00 — ข้อความสรุปสัปดาห์ที่ผ่านมา
-- ---------------------------------------------------------------------------

-- A1. ตัวเลขหลัก (หนึ่งแถว)
SELECT * FROM v_weekly_report
WHERE week_start = date('now', (SELECT tz_offset FROM cfg), 'weekday 0', '-13 days');

-- A2. บรรทัด "คอขวดสัปดาห์นี้คือ…"
SELECT
  'คอขวดสัปดาห์นี้: รอ ' || COALESCE(top_person,'—') || ' ' || COALESCE(top_person_hours,0)
  || ' ชม. · สาเหตุหลัก ' || COALESCE(top_reason,'—')
  || ' · flow ' || COALESCE(flow_eff_pct,0) || '%' AS line
FROM v_bottleneck_weekly
WHERE week_start = date('now', (SELECT tz_offset FROM cfg), 'weekday 0', '-13 days');

-- A3. สัญญาณเตือนที่ต้องส่ง (ตัดยอดแล้ว เรียงตามความเร่งด่วน)
SELECT severity, code, message, hidden_more FROM v_alerts ORDER BY severity, value DESC;

-- A4. งานค้างที่ต้องกดจัดการในข้อความเดียวกัน
SELECT task_id, title, project_name, status, ROUND(stale_hours/24.0,1) AS days_stuck, waiting_on
FROM v_stale_tasks LIMIT 10;

-- A5. เงินสัปดาห์ที่ผ่านมาแยกหมวด
SELECT COALESCE(category,'(ไม่ระบุหมวด)') AS category,
       COUNT(*) AS n, ROUND(SUM(amount),0) AS total
FROM v_expense_local
WHERE amount > 0 AND verified_by_user = 1
  AND week_start = date('now', (SELECT tz_offset FROM cfg), 'weekday 0', '-13 days')
GROUP BY category ORDER BY total DESC;


-- ---------------------------------------------------------------------------
--  B. cron วันที่ 1 ของเดือน — รายงานเดือน
-- ---------------------------------------------------------------------------

-- B1. เทียบเดือนต่อเดือน เรียงตามหมวดที่โตเร็วที่สุด
SELECT * FROM v_spend_month_category
WHERE ym = strftime('%Y-%m', date('now', (SELECT tz_offset FROM cfg), '-1 month'))
ORDER BY COALESCE(growth_pct,-999) DESC;

-- B2. สามอันดับที่โตเร็วที่สุด (สำหรับใส่ในย่อหน้าที่ AI เขียน)
SELECT category, total, prev_total, growth_pct FROM v_spend_fastest_growing LIMIT 3;

-- B3. รอยรั่วรายการเล็กของเดือน
SELECT label, times, avg_each, total, annual_run_rate FROM v_spend_small_leaks
WHERE ym = strftime('%Y-%m', date('now', (SELECT tz_offset FROM cfg), '-1 month'))
LIMIT 10;

-- B4. รายจ่ายประจำที่ซ่อนอยู่ (ของที่ยกเลิกได้แล้วประหยัดทั้งปี)
SELECT label, months_in_a_row, avg_per_month, annual_run_rate FROM v_spend_recurring LIMIT 15;

-- B5. เวลารอทั้งเดือน แยกตามคน — เอาไปคุยกับเขาได้เลย
SELECT person, times_blocked, wait_hours, avg_hours_each, still_open
FROM v_wait_by_person_month
WHERE ym = strftime('%Y-%m', date('now', (SELECT tz_offset FROM cfg), '-1 month'))
  AND contact_id IS NOT NULL
ORDER BY wait_hours DESC;

-- B6. เงินต่อผลลัพธ์ — โปรเจกต์ที่กินเงินแล้วไม่ขยับจะโผล่ที่นี่
SELECT project_name, spend, tasks_closed, spend_per_task, wait_hours
FROM v_spend_per_outcome
WHERE ym = strftime('%Y-%m', date('now', (SELECT tz_offset FROM cfg), '-1 month'))
ORDER BY spend DESC;


-- ---------------------------------------------------------------------------
--  C. บล็อกบนแดชบอร์ด
-- ---------------------------------------------------------------------------

-- C1. เส้น flow efficiency 12 สัปดาห์ล่าสุด
SELECT week_start, flow_eff_pct, touch_hours, wait_hours, tasks_done
FROM v_flow_weekly ORDER BY week_start DESC LIMIT 12;

-- C2. flow ทับกับการสลับโปรเจกต์ (สองเส้นบนแกนเดียวกัน)
SELECT week_start, flow_eff_pct, avg_projects_per_day
FROM v_flow_vs_switching_weekly ORDER BY week_start DESC LIMIT 12;

-- C3. WIP รายวัน 60 วันล่าสุด พร้อมเส้นเพดาน
SELECT day, wip, finished_today, (SELECT wip_ceiling FROM cfg) AS ceiling
FROM v_wip_daily
WHERE day >= date('now', (SELECT tz_offset FROM cfg), '-60 days')
ORDER BY day;

-- C4. แถบเวลาของงาน 10 ชิ้นที่ใช้เวลานานที่สุด — ข้อมูลของรูป "รอ 96% ของเวลา"
SELECT title, project_name, cycle_days, touch_min, wait_hours, flow_eff_pct, wait_breakdown
FROM v_task_timeline LIMIT 10;

-- C5. เวลารอแยกตามคน 3 เดือนล่าสุด (แผนภูมิแท่ง)
SELECT person, ROUND(SUM(wait_hours),1) AS wait_hours, SUM(times_blocked) AS times
FROM v_wait_by_person_month
WHERE contact_id IS NOT NULL
  AND ym >= strftime('%Y-%m', date('now', (SELECT tz_offset FROM cfg), '-3 months'))
GROUP BY person ORDER BY wait_hours DESC;

-- C6. เงินตามหมวด เดือนล่าสุด (โดนัท/แท่ง)
SELECT category, total, pct_of_month FROM v_spend_month_category
WHERE ym = strftime('%Y-%m', date('now', (SELECT tz_offset FROM cfg)))
ORDER BY total DESC;

-- C7. ความสม่ำเสมอของการบันทึก 90 วัน (heatmap)
SELECT day, logged, self_rating, expenses_logged FROM v_logging_daily
WHERE day >= date('now', (SELECT tz_offset FROM cfg), '-90 days') ORDER BY day;


-- ---------------------------------------------------------------------------
--  D. ถามสด ๆ ในแชท
-- ---------------------------------------------------------------------------

-- D1. "ตอนนี้ติดอะไรอยู่บ้าง"
SELECT title, person, reason, waiting_days FROM v_blocked_now LIMIT 15;

-- D2. "ใครถามอะไรค้างไว้บ้าง"
SELECT person, platform, waiting_hours, preview FROM v_unanswered_now LIMIT 15;

-- D3. "งานประเภทไหนที่ผมประเมินเวลาพลาดตลอด"
SELECT category, samples, avg_estimate_min, avg_actual_min, ratio FROM v_estimate_accuracy;

-- D4. "มีสลิปอะไรที่ยังไม่ได้ยืนยัน"
SELECT id, paid_local, amount, note, merchant, ocr_confidence FROM v_spend_unverified LIMIT 20;

-- D5. "งานไหนที่เลื่อนมาจนน่าจะไม่เสร็จแล้ว"
SELECT title, project_name, due_moved_count, due_local FROM v_carryover_tasks;


-- ---------------------------------------------------------------------------
--  E. งานบำรุงรักษาที่แอปต้องทำ (ไม่ใช่ view — เป็นคำสั่งเขียน)
-- ---------------------------------------------------------------------------

-- E1. เริ่มติด: เปิดแถวใหม่ใน task_blocks และอัปเดตตัวชี้บน tasks
--     INSERT INTO task_blocks(task_id,reason,contact_id,blocked_at)
--       VALUES (:task_id, :reason, :contact_id, strftime('%Y-%m-%dT%H:%M:%SZ','now'));
--     UPDATE tasks SET status='blocked', blocked_since=strftime('%Y-%m-%dT%H:%M:%SZ','now'),
--                      blocked_reason=:reason, blocked_on_contact_id=:contact_id
--      WHERE id=:task_id;

-- E2. เลิกติด: ปิดแถวที่ยังเปิดอยู่ แล้วล้างตัวชี้
--     UPDATE task_blocks SET unblocked_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
--      WHERE task_id=:task_id AND unblocked_at IS NULL;
--     UPDATE tasks SET status='doing', blocked_since=NULL, blocked_reason=NULL,
--                      blocked_on_contact_id=NULL WHERE id=:task_id;

-- E3. ตรวจว่าตัวชี้บน tasks ยังตรงกับ task_blocks — ควรได้ศูนย์แถวเสมอ
--     ถ้ามีแถวโผล่ แปลว่ามีจุดใดจุดหนึ่งในโค้ดลืมอัปเดตคู่กัน
SELECT t.id, t.status, t.blocked_since,
       (SELECT COUNT(*) FROM task_blocks b WHERE b.task_id=t.id AND b.unblocked_at IS NULL) AS open_blocks
FROM tasks t
WHERE (t.status='blocked') <> ((SELECT COUNT(*) FROM task_blocks b
                                 WHERE b.task_id=t.id AND b.unblocked_at IS NULL) > 0);

-- E4. เลื่อนกำหนดส่ง: ต้องบวก due_moved_count ทุกครั้ง ไม่งั้น v_carryover ไม่มีความหมาย
--     UPDATE tasks SET due_at=:new_due, due_moved_count=due_moved_count+1 WHERE id=:task_id;

-- E5. ล้างข้อมูลตัวอย่างก่อนขึ้นระบบจริง
--     DELETE FROM task_events; DELETE FROM work_sessions; DELETE FROM task_blocks;
--     DELETE FROM chat_messages; DELETE FROM chat_threads; DELETE FROM expenses;
--     DELETE FROM daily_logs; DELETE FROM idea_analyses; DELETE FROM ideas;
--     DELETE FROM tasks; DELETE FROM contacts; DELETE FROM projects;
--     VACUUM;
