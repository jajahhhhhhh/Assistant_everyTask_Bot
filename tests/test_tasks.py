"""
เทสต์งานใน bot.py — Storage.add_task / get_tasks / complete_task

(ไฟล์เดิมเทสต์ assistant/tasks.py ซึ่งถูกลบไปแล้ว)
"""

import unittest

from tests._bot_case import BotDbCase, bot

USER_A = 111
USER_B = 222


class TestAddTask(BotDbCase):
    async def test_defaults(self):
        task_id = await bot.Storage.add_task(USER_A, "เขียนเทสต์")
        task = (await bot.Storage.get_tasks(USER_A))[0]
        self.assertEqual(task["id"], task_id)
        self.assertEqual(task["title"], "เขียนเทสต์")
        self.assertEqual(task["priority"], "medium")
        self.assertEqual(task["status"], "todo")

    async def test_optional_fields_are_stored(self):
        await bot.Storage.add_task(
            USER_A, "ยื่นเอกสาร", priority="high", due_date="2026-09-01", project="เฉวง"
        )
        task = (await bot.Storage.get_tasks(USER_A))[0]
        self.assertEqual(task["priority"], "high")
        self.assertEqual(task["due_date"], "2026-09-01")
        self.assertEqual(task["project"], "เฉวง")

    async def test_ids_are_unique(self):
        first = await bot.Storage.add_task(USER_A, "งานหนึ่ง")
        second = await bot.Storage.add_task(USER_A, "งานสอง")
        self.assertNotEqual(first, second)


class TestListTasks(BotDbCase):
    async def test_only_your_own_tasks(self):
        await bot.Storage.add_task(USER_A, "ของ A")
        await bot.Storage.add_task(USER_B, "ของ B")
        self.assertEqual([t["title"] for t in await bot.Storage.get_tasks(USER_A)], ["ของ A"])

    async def test_filter_by_status(self):
        keep = await bot.Storage.add_task(USER_A, "ยังไม่เสร็จ")
        done = await bot.Storage.add_task(USER_A, "เสร็จแล้ว")
        await bot.Storage.complete_task(USER_A, done)

        todo = await bot.Storage.get_tasks(USER_A, status="todo")
        self.assertEqual([t["id"] for t in todo], [keep])
        self.assertEqual([t["id"] for t in await bot.Storage.get_tasks(USER_A, status="done")], [done])

    async def test_empty_list_for_a_new_user(self):
        self.assertEqual(await bot.Storage.get_tasks(USER_A), [])


class TestCompleteTask(BotDbCase):
    async def test_marks_done_and_stamps_the_time(self):
        task_id = await bot.Storage.add_task(USER_A, "งานที่จะปิด")
        self.assertTrue(await bot.Storage.complete_task(USER_A, task_id))

        row = self.rows("SELECT status, completed_at FROM tasks WHERE id = ?", (task_id,))[0]
        self.assertEqual(row["status"], "done")
        self.assertIsNotNone(row["completed_at"])

    async def test_cannot_close_someone_elses_task(self):
        task_id = await bot.Storage.add_task(USER_A, "ของ A")
        self.assertFalse(await bot.Storage.complete_task(USER_B, task_id))
        self.assertEqual((await bot.Storage.get_tasks(USER_A))[0]["status"], "todo")

    async def test_unknown_task_id(self):
        self.assertFalse(await bot.Storage.complete_task(USER_A, 9999))


if __name__ == "__main__":
    unittest.main()
