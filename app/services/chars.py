"""شخصیت: نیازهای حیاتی، تجربه، مهارت، قدرت مؤثر."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app.services import rules
from app.services.errors import GameError
from app.services.inventory import loadout
from app.services.settings import SKILLS


def now() -> datetime:
    return datetime.now(timezone.utc)


def jload(v):
    return json.loads(v) if isinstance(v, str) else v


def skills_of(ch) -> dict:
    sk = jload(ch["skills"])
    return {k: int(sk.get(k, 1)) for k in SKILLS}


async def refresh_vitals(conn, settings, user_id: int, at=None):
    """محاسبه تنبل نیازها (تا لحظه at یا اکنون) و ذخیره. ردیف تازه شخصیت را برمی‌گرداند."""
    ch = await conn.fetchrow("SELECT * FROM characters WHERE user_id=$1 FOR UPDATE", user_id)
    if ch is None:
        return None
    t = at or now()
    if at is not None and at < ch["vitals_updated_at"]:
        t = ch["vitals_updated_at"]
    new = rules.compute_vitals(dict(ch), t, settings.get, rules.environment(t, settings.get))
    await conn.execute(
        "UPDATE characters SET health=$2, hunger=$3, thirst=$4, energy=$5, fatigue=$6, vitals_updated_at=$7 "
        "WHERE user_id=$1", user_id, new["health"], new["hunger"], new["thirst"], new["energy"], new["fatigue"], t)
    return await conn.fetchrow("SELECT * FROM characters WHERE user_id=$1", user_id)


def require_free(ch, what: str = "این کار"):
    loc = ch["location"]
    if loc == "dead":
        raise GameError("☠️ تو مرده‌ای؛ منتظر بازگشت بمان.")
    if loc == "captive":
        raise GameError("⛓ در اسارتی و نمی‌توانی " + what + " را انجام دهی.")


def require_home(ch, what: str = "این کار"):
    require_free(ch, what)
    if ch["location"] not in ("home", "resting"):
        raise GameError("باید در خانه باشی تا " + what + " را انجام دهی.")


async def grant_skill_xp(conn, settings, user_id: int, skill: str, amount: int) -> list:
    ch = await conn.fetchrow("SELECT skills, skill_xp FROM characters WHERE user_id=$1 FOR UPDATE", user_id)
    sk, sx = jload(ch["skills"]), jload(ch["skill_xp"])
    base = int(settings.get("skills.xp_base"))
    lvl, xp, ups = rules.add_xp(int(sk.get(skill, 1)), int(sx.get(skill, 0)), amount,
                                lambda l: rules.skill_xp_needed(l, base), int(settings.get("skills.max_level")))
    sk[skill], sx[skill] = lvl, xp
    await conn.execute("UPDATE characters SET skills=$2::jsonb, skill_xp=$3::jsonb WHERE user_id=$1",
                       user_id, json.dumps(sk), json.dumps(sx))
    return [f"📈 مهارت {SKILLS[skill]} به سطح {lvl} رسید"] if ups else []


async def grant_char_xp(conn, settings, user_id: int, amount: int) -> list:
    ch = await conn.fetchrow("SELECT level, xp FROM characters WHERE user_id=$1 FOR UPDATE", user_id)
    base = int(settings.get("character.xp_base"))
    lvl, xp, ups = rules.add_xp(ch["level"], ch["xp"], amount, lambda l: rules.char_xp_needed(l, base), 999)
    await conn.execute("UPDATE characters SET level=$2, xp=$3 WHERE user_id=$1", user_id, lvl, xp)
    msgs = [f"⭐️ شخصیت به سطح {lvl} رسید"] if ups else []
    u = await conn.fetchrow("SELECT account_level, account_xp FROM users WHERE id=$1 FOR UPDATE", user_id)
    ab = int(settings.get("account.xp_base"))
    al, ax, aups = rules.add_xp(u["account_level"], u["account_xp"], int(settings.get("account.xp_per_activity")),
                                lambda l: l * ab, 999)
    await conn.execute("UPDATE users SET account_level=$2, account_xp=$3 WHERE id=$1", user_id, al, ax)
    if aups:
        msgs.append(f"🏅 سطح حساب به {al} رسید")
    return msgs


async def player_stats(conn, settings, user_id: int, ch=None) -> dict:
    ch = ch or await conn.fetchrow("SELECT * FROM characters WHERE user_id=$1", user_id)
    sk = skills_of(ch)
    lo = await loadout(conn, user_id)
    ranged = any(i["kind"] == "weapon" and i["effects"].get("ranged") for i in lo["items"])
    atk_skill = sk["marksmanship"] if ranged else sk["combat"]
    cond = rules.condition_mult(ch["hunger"], ch["thirst"], ch["fatigue"], ch["energy"])
    env = rules.environment(now(), settings.get)
    stealth = sk["stealth"] + (int(settings.get("stance.cautious_stealth_bonus")) if ch["stance"] == "cautious" else 0) \
        + (int(settings.get("env.night_stealth_bonus")) if env["night"] else 0)
    perception = sk["perception"] + (int(settings.get("stance.cautious_stealth_bonus")) if ch["stance"] == "cautious" else 0)
    return {
        "power": (6 + 1.5 * atk_skill + 1.0 * sk["strength"] + lo["power"]) * cond,
        "defense": lo["defense"] + 0.3 * sk["endurance"],
        "stealth": stealth, "perception": perception, "endurance": sk["endurance"],
        "cond": cond, "hp": ch["health"], "skills": sk, "lo": lo,
        "build_speed": min([float(i["effects"].get("build_speed", 1)) for i in lo["items"]] + [1.0]),
    }


async def get_char(conn, user_id: int):
    return await conn.fetchrow("SELECT * FROM characters WHERE user_id=$1", user_id)
