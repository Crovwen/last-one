"""تسویه تنبل: هر بار که بازیکن تعامل می‌کند (یا کار زمان‌دار می‌رسد) فقط رویدادهای سررسیدشده اعمال می‌شود.
به همین دلیل با خواب سرویس رایگان یا ری‌استارت، هیچ رویدادی از دست نمی‌رود."""
from __future__ import annotations

from app.services import animals, captivity, death, events, hirelings, homes, world
from app.services.chars import refresh_vitals

DUE_SQL = """
SELECT
 EXISTS(SELECT 1 FROM characters WHERE user_id=$1) AS has_ch,
 EXISTS(SELECT 1 FROM users WHERE id=$1 AND last_seen_at < now() - interval '5 minutes') AS seen_old,
 EXISTS(SELECT 1 FROM activities WHERE user_id=$1 AND status='running' AND ends_at <= now()) AS act,
 EXISTS(SELECT 1 FROM characters WHERE user_id=$1 AND location IN ('resting','dead','operation') AND
        COALESCE(rest_until, dead_until) <= now()) AS ch_due,
 EXISTS(SELECT 1 FROM captivities WHERE status='active' AND ends_at <= now() AND (captive_id=$1 OR captor_id=$1)) AS cap,
 EXISTS(SELECT 1 FROM buildings WHERE scope='home' AND owner_id=$1 AND status='building' AND ready_at <= now()) AS b_home,
 (SELECT tribe_id FROM tribe_members WHERE user_id=$1) AS tribe_id,
 EXISTS(SELECT 1 FROM buildings b JOIN tribe_members m ON m.tribe_id=b.owner_id AND m.user_id=$1
        WHERE b.scope='tribe' AND b.status='building' AND b.ready_at <= now()) AS b_tribe,
 EXISTS(SELECT 1 FROM animals WHERE owner_user_id=$1 AND status='ok' AND (hp < max_hp OR train_until IS NOT NULL)) AS animals,
 EXISTS(SELECT 1 FROM hirelings WHERE owner_user_id=$1 AND status IN ('active','unpaid') AND
        (wage_paid_until <= now() OR hp < max_hp)) AS hire,
 EXISTS(SELECT 1 FROM missions_active WHERE user_id=$1 AND status='active' AND expires_at <= now()) AS mis,
 EXISTS(SELECT 1 FROM seasons WHERE (status='scheduled' AND starts_at <= now()) OR
        (status IN ('scheduled','active') AND ends_at <= now())) AS season
"""


async def settle_full(conn, settings, user_id: int):
    """(پیام‌ها، ردیف تازه شخصیت، آیا چیزی تغییر کرد). فرض: ردیف کاربر قبلاً قفل شده است."""
    f = await conn.fetchrow(DUE_SQL, user_id)
    if not f["has_ch"]:
        return [], None, False
    if f["seen_old"]:
        await conn.execute("UPDATE users SET last_seen_at=now() WHERE id=$1", user_id)
    changed = any(f[k] for k in ("act", "ch_due", "cap", "b_home", "b_tribe", "animals", "hire", "mis", "season"))
    msgs: list = []
    if f["ch_due"]:
        msgs += await world.settle_rest(conn, settings, user_id)
        msgs += await death.settle_respawn(conn, settings, user_id)
        msgs += await death.settle_recovery(conn, settings, user_id)
    if f["act"]:
        msgs += await world.settle_activity(conn, settings, user_id)
    if f["cap"]:
        msgs += await captivity.settle(conn, settings, user_id)
    if f["b_home"]:
        msgs += await homes.settle_buildings(conn, "home", user_id)
    if f["b_tribe"]:
        msgs += await homes.settle_buildings(conn, "tribe", f["tribe_id"])
    if f["animals"]:
        msgs += await animals.settle_animals(conn, settings, user_id)
    if f["hire"]:
        msgs += await hirelings.settle_hirelings(conn, settings, user_id)
    if f["mis"]:
        await events.expire_missions(conn, user_id)
    if f["season"]:
        await events.season_tick(conn)
    ch = await refresh_vitals(conn, settings, user_id)
    return msgs, ch, changed


async def settle(conn, settings, user_id: int) -> list:
    await conn.fetchval("SELECT id FROM users WHERE id=$1 FOR UPDATE", user_id)
    return (await settle_full(conn, settings, user_id))[0]
