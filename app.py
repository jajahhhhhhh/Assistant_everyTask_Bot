"""
Entry point เดียวสำหรับ deploy — รัน Telegram bot กับ LINE webhook ในโปรเซสเดียว

ทำไมต้องรวมเป็นโปรเซสเดียว ไม่แยกสอง service
    ทั้งสองฝั่งเขียน SQLite ไฟล์เดียวกัน (`DATABASE_PATH`) และ volume ของ Railway
    ผูกกับ service เดียวเท่านั้น ถ้าแยก service ฝั่งหนึ่งจะเขียนลงดิสก์ที่หายทุก
    deploy หรือไม่ก็แย่งล็อกกันเอง — รวมไว้ที่เดียวจึงเป็นทางเดียวที่ข้อมูลตรงกัน

ส่วนไหนจะทำงานขึ้นกับตัวแปรแวดล้อมที่ตั้งไว้
    TELEGRAM_BOT_TOKEN  → เปิด Telegram bot (polling)
    LINE_CHANNEL_SECRET → เปิดการรับ webhook ของ LINE (เซิร์ฟเวอร์ HTTP ขึ้นเสมอ
                          เพื่อให้ health check ของ Railway ผ่าน)

ปิดโปรเซสด้วย SIGTERM (Railway ส่งมาตอน deploy ใหม่) จะรอให้งานเบื้องหลังของ
webhook ทำจนจบก่อน แล้วค่อยหยุด polling
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import signal

from aiohttp import web

import line_webhook

logger = logging.getLogger("app")

PORT = int(os.getenv("PORT", "8080"))
HOST = os.getenv("HOST", "0.0.0.0")


async def start_web() -> web.AppRunner:
    """เปิดเซิร์ฟเวอร์ HTTP: /webhook/line และ /healthz"""
    runner = web.AppRunner(line_webhook.create_app())
    await runner.setup()
    await web.TCPSite(runner, HOST, PORT).start()

    if line_webhook.LINE_CHANNEL_SECRET:
        logger.info("LINE webhook พร้อมที่ http://%s:%d%s", HOST, PORT, line_webhook.WEBHOOK_PATH)
    else:
        logger.warning(
            "ยังไม่ได้ตั้ง LINE_CHANNEL_SECRET — %s จะตอบ 503 (health check ยังผ่าน)",
            line_webhook.WEBHOOK_PATH,
        )
    return runner


# Railway ตั้งชื่อ environment ของ pull request ว่า "<repo>-pr-<เลข>" เสมอ
_PR_ENVIRONMENT_RE = re.compile(r"-pr-\d+$")


def is_pr_environment(name: str | None = None) -> bool:
    """โปรเซสนี้กำลังรันอยู่ใน environment ที่ Railway สร้างให้ pull request ไหม

    ทำไมต้องรู้: Telegram ยอมให้มีผู้เรียก getUpdates ได้ทีละรายต่อหนึ่งโทเคน
    เท่านั้น ใครขอก่อนได้ไป ข้อความนั้นก็หายจากคิวเลย environment ของ PR ถูก
    คัดลอกตัวแปรมาจาก project ทั้งชุด รวมถึง TELEGRAM_BOT_TOKEN มันจึงกลายเป็น
    ผู้เรียกรายที่สองที่แย่งข้อความไปราวครึ่งหนึ่ง แล้วเขียนลง volume ของตัวเอง
    ซึ่งถูกลบทิ้งตอน PR ถูก merge

    เกิดขึ้นจริงแล้ว: ประวัติแชท Renovate 325 ข้อความที่นำเข้าไประหว่างที่ PR #37
    เปิดอยู่ ถูก environment pr-37 รับไปทั้งชุด แล้วหายไปพร้อมกับมันตอน merge

    เข้มงวดไว้ก่อน — ตรงกับรูปแบบของ PR เท่านั้นจึงจะหยุด ชื่ออื่นทั้งหมด
    รวมถึงตอนที่ตัวแปรนี้ไม่ได้ตั้งไว้ (รันในเครื่องตัวเอง) ยัง poll ตามปกติ
    ผิดพลาดทางนี้แค่ทำให้ข้อความซ้ำ ผิดอีกทางคือบอทตัวจริงเงียบไปทั้งตัว
    """
    if name is None:
        name = os.getenv("RAILWAY_ENVIRONMENT_NAME", "")
    return bool(_PR_ENVIRONMENT_RE.search((name or "").strip()))


async def start_telegram():
    """เปิด Telegram polling ในลูปเดียวกับเว็บ

    คืน None เมื่อไม่ได้ตั้งโทเคน หรือเมื่อ environment นี้เป็นของ pull request
    """
    environment = os.getenv("RAILWAY_ENVIRONMENT_NAME", "")
    if is_pr_environment(environment):
        logger.warning(
            "environment %s เป็นของ pull request — ไม่เปิด Telegram polling "
            "เพราะจะแย่งข้อความไปจากบอทตัวจริง (เว็บและ health check ยังทำงาน)",
            environment,
        )
        return None

    import bot

    if not bot.BOT_TOKEN:
        logger.warning("ยังไม่ได้ตั้ง TELEGRAM_BOT_TOKEN — ข้ามส่วน Telegram")
        return None

    from telegram import Update

    bot.init_db()
    application = bot.build_application()
    await application.initialize()
    await application.start()
    await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)

    # initialize() ไม่เรียก post_init (มีแต่ run_polling/run_webhook ที่เรียก)
    # ตัวส่งการเตือนจึงต้องถูกเริ่มตรงนี้ ไม่งั้น /remind จะเก็บลงฐานข้อมูลแล้ว
    # ไม่มีใครมารับ เหมือนที่เป็นมาตลอด
    bot.start_reminder_scheduler(application)

    logger.info("Telegram bot เริ่ม polling แล้ว")
    return application


def data_dir_is_persistent(path: str) -> bool:
    """โฟลเดอร์นี้อยู่บนอุปกรณ์คนละตัวกับ root filesystem หรือไม่

    volume ของ Railway ถูก mount เป็นอุปกรณ์แยก การเทียบ st_dev กับของ / จึง
    บอกได้ว่าข้อมูลจะรอด deploy ไหม แยกออกมาเป็นฟังก์ชันของตัวเองเพราะผลของมัน
    ขึ้นกับสภาพเครื่องที่รัน เทสต์จึงต้องกำหนดคำตอบเองได้ ไม่งั้นจะได้เทสต์ที่
    ผ่านหรือแดงตามว่า /tmp ของเครื่องนั้นเป็น tmpfs หรือเปล่า
    """
    return os.stat(path).st_dev != os.stat("/").st_dev


def describe_storage() -> None:
    """บอกตอนบูตว่าข้อมูลจะไปอยู่ที่ไหนจริง ๆ

    กับดักที่เคยกินเวลาไปทั้งเช้า: DATABASE_PATH ที่เป็น path แบบสัมพัทธ์กลบ
    DATA_DIR ได้ทั้งใบ

        DATA_DIR = os.getenv("DATA_DIR", "data")
        DB_PATH  = os.getenv("DATABASE_PATH", f"{DATA_DIR}/assistant.db")

    ตั้ง DATA_DIR=/data ไว้แล้ว แต่ถ้ามี DATABASE_PATH=data/assistant.db ค้างอยู่
    ไฟล์จริงจะกลายเป็น /app/data/assistant.db คือดิสก์ในคอนเทนเนอร์ที่หายทุก
    deploy ส่วน volume ที่ mount ไว้ก็นอนว่างอยู่อย่างนั้น ไม่มีอะไรผิดพลาดให้
    เห็นสักอย่าง บอทตอบได้ปกติ ข้อมูลเข้าปกติ แล้วหายเงียบ ๆ ตอน deploy ถัดไป

    ทั้งสามอย่างที่ตรวจตรงนี้จึงเป็น "เงียบแล้วเจ็บทีหลัง" เหมือนกันหมด
    """
    import bot   # start_telegram() import อยู่แล้ว ตรงนี้จึงไม่เพิ่มผลข้างเคียง

    resolved = os.path.abspath(line_webhook.DB_PATH)
    logger.info("ฐานข้อมูล: %s", resolved)

    # 1. สองฝั่งต้องเขียนไฟล์เดียวกัน ไม่งั้นข้อมูลแตกเป็นสองชุดแบบไม่มีใครรู้
    if os.path.abspath(bot.DB_PATH) != resolved:
        logger.error(
            "Telegram กับ webhook ชี้คนละไฟล์ (%s กับ %s) — ข้อมูลจะแยกกันสองชุด",
            os.path.abspath(bot.DB_PATH),
            resolved,
        )

    # 2. path สัมพัทธ์แปลว่า DATA_DIR ถูกกลบไปแล้ว
    configured = os.getenv("DATABASE_PATH")
    if configured and not os.path.isabs(configured):
        logger.warning(
            "DATABASE_PATH=%r เป็น path แบบสัมพัทธ์ จึงกลายเป็น %s และทับ DATA_DIR=%r "
            "ที่ตั้งไว้ — ตั้งเป็น absolute path หรือลบทิ้งแล้วใช้ DATA_DIR แทน",
            configured,
            resolved,
            os.getenv("DATA_DIR"),
        )

    # 3. volume ของ Railway เป็นอุปกรณ์คนละตัวกับ root filesystem
    directory = os.path.dirname(resolved) or "/"
    # โฟลเดอร์ข้อมูลถูกสร้างใน start_web() ซึ่งยังไม่ได้รันตอนนี้ จึงต้องดูโฟลเดอร์
    # แม่ที่ใกล้ที่สุดที่มีอยู่จริง — volume ที่ mount ไว้ก็ตอบถูกอยู่ดี เพราะจุด
    # mount เองมีอยู่แล้วก่อนแอปเริ่ม
    probe = directory
    while not os.path.exists(probe) and os.path.dirname(probe) != probe:
        probe = os.path.dirname(probe)
    try:
        persistent = data_dir_is_persistent(probe)
    except OSError as exc:
        logger.warning("ตรวจไม่ได้ว่า %s อยู่บน volume หรือไม่: %s", directory, exc)
        return
    if persistent:
        logger.info("%s อยู่บน volume — ข้อมูลอยู่ข้าม deploy ได้", directory)
    else:
        logger.warning(
            "%s ไม่ได้อยู่บน volume — ข้อมูลทั้งหมดจะหายเมื่อ deploy ครั้งถัดไป",
            directory,
        )


def import_declared_expenses() -> None:
    """ลงบิลที่ประกาศไว้ใน EXPENSE_IMPORT_JSON — ไม่มีตัวแปร = ไม่ทำอะไร

    ต้องทำที่นี่ ไม่ใช่ preDeployCommand: คอนเทนเนอร์ของ pre-deploy ไม่ได้ mount
    volume จึงเปิด /data/assistant.db ไม่ได้เลย ("unable to open database file")
    โปรเซสแอปคือที่เดียวที่เห็นฐานข้อมูลจริง

    ตัวนำเข้ากันแถวซ้ำด้วย ref อยู่แล้ว บูตกี่รอบก็ปลอดภัย และห้ามพังการบูต
    เพราะบิลลงไม่ได้ไม่ใช่เหตุผลที่บอททั้งตัวจะไม่ขึ้น
    """
    if not os.getenv("EXPENSE_IMPORT_JSON"):
        return
    try:
        from scripts.import_expenses import main as import_expenses

        import_expenses([])
    except Exception:
        logger.exception("นำบิลเข้าตาราง expenses ไม่สำเร็จ — ข้ามไปก่อน")


async def run() -> None:
    describe_storage()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass

    import_declared_expenses()

    runner = await start_web()
    application = await start_telegram()

    try:
        await stop.wait()
        logger.info("ได้รับสัญญาณให้ปิด — กำลังเก็บงานที่ค้าง")
    finally:
        if application is not None:
            import bot   # start_telegram() import ไปแล้ว ตรงนี้จึงได้โมดูลเดิม

            bot.stop_reminder_scheduler()
            await application.updater.stop()
            await application.stop()
            await application.shutdown()
        # cleanup ของ aiohttp เรียก handler.close() ซึ่งรอ event ที่ค้างอยู่ให้จบ
        await runner.cleanup()
        logger.info("ปิดเรียบร้อย")


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    asyncio.run(run())


if __name__ == "__main__":
    main()
