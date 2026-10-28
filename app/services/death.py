"""مرگ (غیردائمی)، بازگشت به خانه، بازیابی وسایل از محل مرگ."""
from __future__ import annotations

import random
from datetime import timedelta

from app.services import inventory as inv
from app.services.chars import get_char, jload, now
from app.services.economy import change_coins
from app.services.errors import GameError
from app.services.notify import notify
from app.services.sched import schedule


async def kill(conn, settings, user_id: int, reason: str) -> int:
    """شخصیت می‌میرد؛ وسایل همراه در محل مرگ (drop) می‌ماند. شناسه drop را برمی‌گرداند."""
    exp = now() + timedelta(hours=int(settings.get("drop.expire_hours")))
    drop_id = await conn.fetchval("INSERT INTO drops(kind, victim_user_id, expires_at) VALUES('death',$1,$2) RETURNING id",
                                  user_id, exp)
    await inv.drop_carried(conn, user_id, drop_id, "death:" + reason)
    await conn.execute("UPDATE activities SET status='done', result='{\"cancelled\": true}'::jsonb WHERE user_id=$1 AND status='running'",
                       user_id)
    until = now() + timedelta(minutes=int(settings.get("death.respawn_minutes")))
    await conn.execute("UPDATE characters SET location='dead', health=0, dead_until=$2, rest_until=NULL WHERE user_id=$1",
                       user_id, until)
    await schedule("respawn", {"user_id": user_id}, until)
    await notify(conn, user_id, "death", f"☠️ کشته شدی. بعد از {settings.get('death.respawn_minutes')} دقیقه در خانه برمی‌گردی؛ "
                                         "وسایل همراهت در محل مرگ مانده است.")
    return drop_id


async def _respawn(conn, settings, user_id: int):
    await conn.execute(
        "UPDATE characters SET location='home', dead_until=NULL, health=$2, hunger=GREATEST(hunger,30), "
        "thirst=GREATEST(thirst,30), fatigue=0, energy=GREATEST(energy,40), vitals_updated_at=now() WHERE user_id=$1",
        user_id, int(settings.get("death.respawn_health")))


async def settle_respawn(conn, settings, user_id: int) -> list:
    ch = await get_char(conn, user_id)
    if ch["location"] != "dead" or ch["dead_until"] is None or ch["dead_until"] > now():
        return []
    await _respawn(conn, settings, user_id)
    msg = "🏠 در خانه‌ات دوباره ظاهر شدی."
    await notify(conn, user_id, "respawn", msg)
    return [msg]


async def fast_respawn(conn, settings, user_id: int, ch):
    if ch["location"] != "dead":
        raise GameError("مرده نیستی.")
    cost = int(settings.get("death.fast_respawn_cost"))
    try:
        await change_coins(conn, user_id, -cost, "fast_respawn")
    except Exception as exc:  # noqa: BLE001
        raise GameError(f"سکه کافی نداری (نیاز: {cost}).") from exc
    await _respawn(conn, settings, user_id)
    return "🏠 بازگشت سریع انجام شد."


async def last_drop(conn, user_id: int):
    return await conn.fetchrow(
        "SELECT * FROM drops WHERE victim_user_id=$1 AND kind='death' AND expires_at > now() ORDER BY id DESC LIMIT 1", user_id)


async def start_recovery(conn, settings, user_id: int, ch, fast: bool):
    """بازگشت به محل مرگ. با سکه سریع‌تر است، ولی وسایل هنوز بودنشان تضمین نیست."""
    if ch["location"] != "home":
        raise GameError("باید در خانه باشی.")
    d = await last_drop(conn, user_id)
    if d is None:
        raise GameError("محل مرگ فعالی نداری (وسایل منقضی شده).")
    if fast:
        cost = int(settings.get("death.return_fast_cost"))
        try:
            await change_coins(conn, user_id, -cost, "fast_return")
        except Exception as exc:  # noqa: BLE001
            raise GameError(f"سکه کافی نداری (نیاز: {cost}).") from exc
        minutes = 1
    else:
        minutes = int(settings.get("death.return_minutes"))
    until = now() + timedelta(minutes=minutes)
    await conn.execute("UPDATE characters SET location='operation', rest_until=$2 WHERE user_id=$1", user_id, until)
    await conn.execute("UPDATE users SET state_data = state_data || jsonb_build_object('recover', $2::bigint) WHERE id=$1",
                       user_id, d["id"])
    await schedule("recover", {"user_id": user_id}, until)
    return f"🧭 در راه محل مرگ هستی ({minutes} دقیقه)."


async def settle_recovery(conn, settings, user_id: int) -> list:
    ch = await get_char(conn, user_id)
    if ch["location"] != "operation" or ch["rest_until"] is None or ch["rest_until"] > now():
        return []
    u = await conn.fetchrow("SELECT state_data FROM users WHERE id=$1", user_id)
    drop_id = jload(u["state_data"]).get("recover")
    await conn.execute("UPDATE characters SET location='home', rest_until=NULL WHERE user_id=$1", user_id)
    if not drop_id:
        return []
    await conn.execute("UPDATE users SET state_data = state_data - 'recover' WHERE id=$1", user_id)
    taken = await inv.pickup_from_ground(conn, settings, user_id, await get_char(conn, user_id), drop_id)
    msg = ("🎒 وسایل بازیابی‌شده: " + "، ".join(f"{n} ×{q}" for n, q in taken)) if taken else \
        "😔 به محل مرگ رسیدی ولی چیزی نمانده بود (یا کوله‌ات جا نداشت)."
    await notify(conn, user_id, "recover", msg)
    return [msg]


async def cleanup_drops(conn) -> int:
    """وسایل drop های منقضی‌شده نابود می‌شوند (ثبت در دفتر)."""
    n = 0
    for d in await conn.fetch("SELECT id FROM drops WHERE expires_at <= now()"):
        for it in await conn.fetch("SELECT id FROM item_instances WHERE holder_type='ground' AND holder_id=$1", d["id"]):
            await inv.destroy_instance(conn, it["id"], "drop_expired")
        await conn.execute("DELETE FROM item_stacks WHERE holder_type='ground' AND holder_id=$1", d["id"])
        await conn.execute("UPDATE users SET state_data = state_data - 'recover' WHERE (state_data->>'recover')::bigint = $1", d["id"])
        await conn.execute("DELETE FROM drops WHERE id=$1", d["id"])
        n += 1
    return n
