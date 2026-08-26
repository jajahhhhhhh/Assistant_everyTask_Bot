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


async def start_telegram():
    """เปิด Telegram polling ในลูปเดียวกับเว็บ คืน None ถ้าไม่ได้ตั้งโทเคน"""
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
    logger.info("Telegram bot เริ่ม polling แล้ว")
    return application


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
        persistent = os.stat(probe).st_dev != os.stat("/").st_dev
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


async def run() -> None:
    describe_storage()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass

    runner = await start_web()
    application = await start_telegram()

    try:
        await stop.wait()
        logger.info("ได้รับสัญญาณให้ปิด — กำลังเก็บงานที่ค้าง")
    finally:
        if application is not None:
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
