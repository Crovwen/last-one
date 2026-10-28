"""کارهای زمان‌دار: پایان فعالیت/استراحت/ساخت/اسارت، مهلت نبرد، رویدادها، فصل، پشتیبان‌گیری."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.bot import combat_ui
from app.bot.handlers import admin
from app.bot.handlers import groups as groups_ui
from app.context import AppContext
from app.runtime import settle_uid
from app.services import combat, death, events, homes, market, notify

log = logging.getLogger(__name__)


def register(ctx: AppContext) -> None:
    jobs, pool, settings = ctx.jobs, ctx.pool, ctx.settings

    async def user_job(p: dict):
        await settle_uid(ctx, p["user_id"])

    for kind in ("activity_end", "rest_end", "respawn", "recover"):
        jobs.register(kind, user_job)

    async def captivity_end(p: dict):
        row = await pool.fetchrow("SELECT captive_id, captor_id FROM captivities WHERE id=$1", p["captivity_id"])
        if row:
            await settle_uid(ctx, row["captive_id"])
            await settle_uid(ctx, row["captor_id"])

    jobs.register("captivity_end", captivity_end)

    async def building_done(p: dict):
        if p["scope"] == "home":
            await settle_uid(ctx, p["owner_id"])
        else:
            async with notify.collecting() as q:
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await homes.settle_buildings(conn, "tribe", p["owner_id"])
            await notify.flush(q)

    jobs.register("building_done", building_done)

    async def combat_timeout(p: dict):
        async with notify.collecting() as q:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    changed = await combat.on_timeout(conn, settings, p["cid"], p["version"])
        await notify.flush(q)
        if changed:
            await combat_ui.refresh(ctx, p["cid"])

    jobs.register("combat_timeout", combat_timeout)

    async def event_end(p: dict):
        async with pool.acquire() as conn:
            async with conn.transaction():
                result = await events.finish_event(conn, settings, p["event_id"])
        if result:
            await groups_ui.refresh_event(ctx, p["event_id"], result)

    jobs.register("event_end", event_end)

    async def season_tick(_p: dict):
        async with notify.collecting() as q:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await events.season_tick(conn)
        await notify.flush(q)

    jobs.register("season_tick", season_tick)

    async def trade_expire(p: dict):
        async with pool.acquire() as conn:
            async with conn.transaction():
                await market.trade_expire(conn, p["trade_id"])

    jobs.register("trade_expire", trade_expire)

    async def event_auto(_p: dict):
        hours = int(settings.get("events.auto_interval_hours"))
        if hours <= 0:
            return
        for g in await pool.fetch("SELECT chat_id FROM chat_groups WHERE events_enabled"):
            try:
                await groups_ui.post_event(ctx, g["chat_id"])
            except Exception as exc:  # noqa: BLE001
                log.info("auto event skipped for %s: %s", g["chat_id"], exc)
        await jobs.enqueue("event_auto", {}, datetime.now(timezone.utc) + timedelta(hours=hours), "event_auto:next")

    jobs.register("event_auto", event_auto)

    async def drop_cleanup(_p: dict):
        async with pool.acquire() as conn:
            async with conn.transaction():
                await death.cleanup_drops(conn)
        await jobs.enqueue("drop_cleanup", {}, datetime.now(timezone.utc) + timedelta(hours=6), "drop_cleanup:next")

    jobs.register("drop_cleanup", drop_cleanup)

    async def backup_job(_p: dict):
        if ctx.config.admin_ids:
            await admin.send_backup(ctx, sorted(ctx.config.admin_ids))
        await jobs.enqueue("backup", {}, datetime.now(timezone.utc) + timedelta(hours=int(settings.get("backup.interval_hours"))),
                           "backup:recurring")

    jobs.register("backup", backup_job)
