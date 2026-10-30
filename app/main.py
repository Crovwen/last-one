"""نقطه شروع: وب‌هوک تلگرام + /health + صف کار، همه در یک سرویس."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiohttp import web

from app import jobs_handlers
from app.bot import combat_ui
from app.bot.handlers import admin, group_text, groups, private
from app.config import load_config
from app.context import AppContext
from app.db import create_pool, run_migrations
from app.services import notify, sched
from app.services.jobs import JobRunner
from app.services.seed import seed_content
from app.services.settings import SettingsService

log = logging.getLogger("lastone")


async def health(_request: web.Request) -> web.Response:
    # عمداً به دیتابیس وصل نمی‌شود تا پینگ‌های مکرر دیتابیس را بیدار نگه ندارند
    return web.Response(text="ok")


async def create_app() -> web.Application:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    pool = await create_pool(cfg.database_url)
    await run_migrations(pool)
    settings = SettingsService(pool)
    await settings.seed_and_load()
    added = await seed_content(pool)
    log.info("content seeded: %s new rows", added)

    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    jobs = JobRunner(pool)
    ctx = AppContext(config=cfg, pool=pool, settings=settings, jobs=jobs, bot=bot)

    async def push(tg_id: int, text: str) -> None:
        await bot.send_message(tg_id, text)

    async def schedule(kind, payload, run_at, unique_key=None):
        await jobs.enqueue(kind, payload, run_at, unique_key)

    notify.set_pusher(push)
    sched.set_scheduler(schedule)
    jobs_handlers.register(ctx)

    dp = Dispatcher(ctx=ctx)
    dp.include_router(admin.router)
    dp.include_router(group_text.router)
    dp.include_router(combat_ui.router)
    dp.include_router(groups.router)
    dp.include_router(private.router)

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=cfg.webhook_secret).register(app, path=cfg.webhook_path)

    async def on_startup(_app: web.Application) -> None:
        await bot.set_webhook(cfg.webhook_url, secret_token=cfg.webhook_secret,
                              allowed_updates=dp.resolve_used_update_types())
        try:
            await bot.set_my_commands([BotCommand(command="menu", description="منوی اصلی"),
                                       BotCommand(command="help", description="راهنما"),
                                       BotCommand(command="forest", description="جنگل"),
                                       BotCommand(command="inv", description="کوله‌پشتی"),
                                       BotCommand(command="home", description="خانه"),
                                       BotCommand(command="profile", description="شخصیت")])
        except Exception:  # noqa: BLE001
            log.warning("set_my_commands failed")
        await jobs.start()
        now = datetime.now(timezone.utc)
        await jobs.enqueue("backup", {}, now + timedelta(hours=int(settings.get("backup.interval_hours"))), "backup:recurring")
        await jobs.enqueue("drop_cleanup", {}, now + timedelta(minutes=5), "drop_cleanup:next")
        if int(settings.get("events.auto_interval_hours")) > 0 and await pool.fetchval("SELECT 1 FROM chat_groups LIMIT 1"):
            await jobs.enqueue("event_auto", {}, now + timedelta(hours=int(settings.get("events.auto_interval_hours"))),
                               "event_auto:next")
        log.info("LAST ONE started")

    async def on_cleanup(_app: web.Application) -> None:
        await jobs.stop()
        await bot.session.close()
        await pool.close()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=load_config().port)
