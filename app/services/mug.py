"""خفتگیری: گرفتن مقدار کمی سکه از بازیکنی که بیرون از خانه است (با فاصله زمانی)."""
from __future__ import annotations

import random
from datetime import timedelta

from app.services.chars import get_char, is_protected, now, player_stats
from app.services.economy import change_coins
from app.services.errors import GameError
from app.services.notify import notify
from app.services.rules import clamp

OUTSIDE = ("hunting", "gathering", "exploring", "operation")


def mug_amount(coins: int, locked: int, frac: float, lo: int, hi: int) -> int:
    """مقدار اندک و متناسب با ثروت: هرچه قربانی پولدارتر، مبلغ بیشتر؛ اما هرگز همه پول نیست."""
    stealable = max(0, coins - locked)
    if stealable <= 0:
        return 0
    amt = int(stealable * frac)
    amt = max(amt, min(lo, stealable // 2))
    return max(0, min(amt, hi, stealable))


def mug_chance(att_power: float, def_power: float) -> float:
    return clamp(0.55 + (att_power - def_power) / (att_power + def_power + 1e-9) * 0.5, 0.1, 0.9)


async def mug(conn, settings, attacker_id: int, target_id: int, rng: random.Random = None) -> str:
    rng = rng or random.Random()
    if attacker_id == target_id:
        raise GameError("از خودت نمی‌توانی خفتگیری کنی.")
    await conn.fetch("SELECT id FROM users WHERE id = ANY($1::bigint[]) ORDER BY id FOR UPDATE", [attacker_id, target_id])
    a_ch, t_ch = await get_char(conn, attacker_id), await get_char(conn, target_id)
    au = await conn.fetchrow("SELECT * FROM users WHERE id=$1", attacker_id)
    tu = await conn.fetchrow("SELECT * FROM users WHERE id=$1", target_id)
    if a_ch is None or t_ch is None or tu["is_banned"]:
        raise GameError("هدف در دسترس نیست.")
    if a_ch["location"] != "home":
        raise GameError("برای خفتگیری باید در خانه و آزاد باشی.")
    cost = int(settings.get("mug.energy"))
    if a_ch["energy"] < cost or a_ch["health"] < 15:
        raise GameError("انرژی یا سلامتی کافی نداری.")
    if t_ch["location"] in ("home", "resting"):
        raise GameError("او در خانه است؛ برای خانه بنویس «حمله به خانه».")
    if t_ch["location"] not in OUTSIDE:
        raise GameError("الان نمی‌شود از او خفتگیری کرد.")
    if is_protected(tu):
        raise GameError("🛡 این بازیکن هنوز محافظ تازه‌وارد دارد.")
    if await conn.fetchval("SELECT 1 FROM combats WHERE state <> 'closed' AND (attacker_user_id=$1 OR defender_user_id=$1 "
                           "OR party @> to_jsonb($1::bigint))", target_id):
        raise GameError("او درگیر نبرد است.")
    cd = timedelta(minutes=int(settings.get("mug.cooldown_min")))
    for ts, who in ((au["last_mug_at"], "خودت"), (tu["last_mugged_at"], "این بازیکن")):
        if ts and now() - ts < cd:
            left = int((cd - (now() - ts)).total_seconds() // 60) + 1
            raise GameError(f"برای {who} هر {settings.get('mug.cooldown_min')} دقیقه فقط یک‌بار خفتگیری ممکن است؛ {left} دقیقه دیگر.")
    if au["protected_until"] and is_protected(au):
        await conn.execute("UPDATE users SET protected_until=NULL WHERE id=$1", attacker_id)
    await conn.execute("UPDATE characters SET energy=energy-$2 WHERE user_id=$1", attacker_id, cost)
    await conn.execute("UPDATE users SET last_mug_at=now() WHERE id=$1", attacker_id)
    a_st = await player_stats(conn, settings, attacker_id, a_ch)
    t_st = await player_stats(conn, settings, target_id, t_ch)
    if rng.random() > mug_chance(a_st["power"], t_st["power"]):
        dmg = rng.randint(5, 15)
        await conn.execute("UPDATE characters SET health=GREATEST(1, health-$2) WHERE user_id=$1", attacker_id, dmg)
        await notify(conn, target_id, "mug_failed", f"🥷 {a_ch['name']} قصد خفتگیری از تو را داشت ولی ناکام ماند.")
        return f"❌ خفتگیری ناکام ماند و {dmg} آسیب دیدی."
    amount = mug_amount(tu["coins"], tu["locked_coins"], float(settings.get("mug.fraction")),
                        int(settings.get("mug.min")), int(settings.get("mug.max")))
    if amount <= 0:
        return "😅 قربانی پول قابل‌برداشتی نداشت."
    await change_coins(conn, target_id, -amount, "mug_loss", ref=str(attacker_id))
    await change_coins(conn, attacker_id, amount, "mug_gain", ref=str(target_id))
    await conn.execute("UPDATE users SET last_mugged_at=now() WHERE id=$1", target_id)
    await notify(conn, target_id, "mugged", f"🥷 {a_ch['name']} از تو خفتگیری کرد و {amount} 🪙 برد.")
    from app.services import events as ev
    await ev.season_score(conn, attacker_id, 2)
    return f"🥷 خفتگیری موفق بود: +{amount} 🪙"
