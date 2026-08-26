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


async def run() -> None:
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
