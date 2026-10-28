"""تسویه تنبل: هر بار که بازیکن تعامل می‌کند (یا کار زمان‌دار می‌رسد) همه رویدادهای سررسیدشده اعمال می‌شود.
به همین دلیل با خواب سرویس رایگان یا ری‌استارت، هیچ رویدادی از دست نمی‌رود."""
from __future__ import annotations

from app.services import animals, captivity, death, events, hirelings, homes, world
from app.services.chars import refresh_vitals


async def settle(conn, settings, user_id: int) -> list:
    await conn.fetchval("SELECT id FROM users WHERE id=$1 FOR UPDATE", user_id)
    if not await conn.fetchval("SELECT 1 FROM characters WHERE user_id=$1", user_id):
        return []
    msgs: list = []
    msgs += await world.settle_rest(conn, settings, user_id)
    msgs += await world.settle_activity(conn, settings, user_id)
    msgs += await death.settle_respawn(conn, settings, user_id)
    msgs += await death.settle_recovery(conn, settings, user_id)
    msgs += await captivity.settle(conn, settings, user_id)
    msgs += await homes.settle_buildings(conn, "home", user_id)
    tid = await conn.fetchval("SELECT tribe_id FROM tribe_members WHERE user_id=$1", user_id)
    if tid:
        msgs += await homes.settle_buildings(conn, "tribe", tid)
    msgs += await animals.settle_animals(conn, settings, user_id)
    msgs += await hirelings.settle_hirelings(conn, settings, user_id)
    await events.expire_missions(conn, user_id)
    await events.season_tick(conn)
    await refresh_vitals(conn, settings, user_id)
    return msgs
