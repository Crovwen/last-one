"""جهان جنگلی: فعالیت‌ها، استراحت، حالت رفتاری."""
from __future__ import annotations

import json
import random
from datetime import timedelta

from app.services import animals as an
from app.services import rules
from app.services import hirelings as hr
from app.services import inventory as inv
from app.services.chars import (get_char, grant_char_xp, grant_skill_xp, jload, now, player_stats, refresh_vitals,
                                require_home, skills_of)
from app.services.economy import change_coins
from app.services.errors import GameError
from app.services.notify import notify
from app.services.sched import schedule

LOC_BY_KIND = {"hunt": "hunting", "gather": "gathering", "explore": "exploring"}
STANCE_FA = {"cautious": "محتاط", "normal": "عادی", "fast": "سریع"}


async def list_activities(conn):
    return await conn.fetch("SELECT * FROM activity_defs WHERE is_active ORDER BY zone, min_skill, name")


async def current_activity(conn, user_id: int):
    return await conn.fetchrow(
        "SELECT a.*, d.name, d.kind FROM activities a JOIN activity_defs d ON d.id=a.def_id "
        "WHERE a.user_id=$1 AND a.status='running'", user_id)


async def start_activity(conn, settings, user_id: int, ch, code: str):
    require_home(ch, "شروع فعالیت")
    if ch["location"] == "resting":
        raise GameError("اول استراحتت را تمام کن (یا استراحت سریع بخر).")
    if await conn.fetchval("SELECT 1 FROM combats WHERE state <> 'closed' AND (attacker_user_id=$1 OR defender_user_id=$1)", user_id):
        raise GameError("درگیر نبرد هستی.")
    d = await conn.fetchrow("SELECT * FROM activity_defs WHERE code=$1 AND is_active", code)
    if d is None:
        raise GameError("فعالیت پیدا نشد.")
    sk = skills_of(ch)
    if sk.get(d["skill"], 1) < d["min_skill"]:
        raise GameError(f"حداقل مهارت لازم: {d['min_skill']}")
    cautious, fast = ch["stance"] == "cautious", ch["stance"] == "fast"
    env = rules.environment(now(), settings.get)
    energy = int(round(d["energy_cost"] * (float(settings.get("stance.cautious_energy_mult")) if cautious else 1.0)
                       * rules.env_energy_mult(env, settings.get)))
    if ch["energy"] < energy:
        raise GameError(f"انرژی کافی نیست (نیاز: {energy}).")
    companions = await an.companions(conn, settings, user_id)
    minutes = d["duration_min"] * (float(settings.get("stance.fast_time_mult")) if fast else 1.0)
    minutes *= max(0.5, 1 - 0.05 * an.perk_total([{"perks": c["perks"]} for c in companions], "speed"))
    minutes = max(1, int(round(minutes)))
    ends = now() + timedelta(minutes=minutes)
    await conn.execute("UPDATE characters SET energy=energy-$2, location=$3 WHERE user_id=$1",
                       user_id, energy, LOC_BY_KIND.get(d["kind"], "gathering"))
    await conn.execute("INSERT INTO activities(user_id, def_id, ends_at) VALUES($1,$2,$3)", user_id, d["id"], ends)
    await schedule("activity_end", {"user_id": user_id}, ends)
    return f"{d['name']} شروع شد؛ {minutes} دقیقه", ends


async def settle_activity(conn, settings, user_id: int, rng: random.Random = None) -> list:
    a = await conn.fetchrow(
        "SELECT a.*, d.* , a.id AS aid FROM activities a JOIN activity_defs d ON d.id=a.def_id "
        "WHERE a.user_id=$1 AND a.status='running' AND a.ends_at <= now() FOR UPDATE OF a", user_id)
    if a is None:
        return []
    rng = rng or random.Random()
    await refresh_vitals(conn, settings, user_id, at=a["ends_at"])   # تا پایان فعالیت «فعال» حساب می‌شود
    ch = await get_char(conn, user_id)
    stats = await player_stats(conn, settings, user_id, ch)
    comps = await an.companions(conn, settings, user_id)
    comp_perks = [{"perks": c["perks"]} for c in comps]
    bonus = 1.0
    if a["kind"] == "gather":
        bonus += 0.1 * an.perk_total(comp_perks, "forage")
    elif a["kind"] == "hunt":
        bonus += 0.1 * an.perk_total(comp_perks, "hunt")
    else:
        bonus += 0.1 * an.perk_total(comp_perks, "scout")
    lines, got = [], []
    pre = (await conn.fetchrow("SELECT * FROM characters WHERE user_id=$1", user_id))
    skill_lv = skills_of(pre).get(a["skill"], 1)
    for entry in jload(a["loot"]):
        chance = min(1.0, float(entry.get("chance", 1.0)) * (1 + 0.02 * skill_lv))
        if rng.random() > chance:
            continue
        if "item" in entry:
            n = max(1, int(round(rng.randint(int(entry["min"]), int(entry["max"])) * bonus)))
            d = await inv.def_by_code(conn, entry["item"])
            added = await inv.add_to_backpack(conn, settings, user_id, await get_char(conn, user_id), d, n, "activity")
            if added:
                got.append(f"{d['name']} ×{added}")
            if added < n:
                lines.append(f"🎒 کوله‌پشتی پر شد؛ {n - added} عدد {d['name']} جا ماند")
        elif "coins" in entry:
            c = rng.randint(int(entry["coins"][0]), int(entry["coins"][1]))
            await change_coins(conn, user_id, c, "activity")
            got.append(f"{c} 🪙")
        elif "animal" in entry:
            m = await an.add_wild(conn, settings, user_id, entry["animal"])
            if m:
                lines.append(m)
    risk = jload(a["risk"])
    if risk:
        rchance = float(risk.get("chance", 0)) * (float(settings.get("stance.cautious_risk_mult")) if pre["stance"] == "cautious"
                                                  else float(settings.get("stance.fast_risk_mult")) if pre["stance"] == "fast" else 1.0)
        rchance *= max(0.4, 1 - 0.02 * stats["endurance"])
        if rules.environment(a["ends_at"], settings.get)["night"]:
            rchance *= float(settings.get("env.night_risk_mult"))
        if rng.random() < rchance:
            dmg = rng.randint(int(risk["damage"][0]), int(risk["damage"][1]))
            await conn.execute("UPDATE characters SET health=GREATEST(1, health-$2) WHERE user_id=$1", user_id, dmg)
            lines.append(f"🩸 {risk.get('text', 'آسیب دیدی')} ({dmg} آسیب)")
    await conn.execute(
        "UPDATE characters SET hunger=GREATEST(0,hunger-$2), thirst=GREATEST(0,thirst-$3), "
        "fatigue=LEAST(100,fatigue+$4), location='home' WHERE user_id=$1",
        user_id, a["hunger_cost"], a["thirst_cost"], a["fatigue_gain"])
    ups = await grant_skill_xp(conn, settings, user_id, a["skill"], a["xp"])
    ups += await grant_char_xp(conn, settings, user_id, a["xp"])
    await conn.execute("UPDATE activities SET status='done', result=$2::jsonb WHERE id=$1", a["aid"],
                       json.dumps({"got": got}, ensure_ascii=False))
    from app.services import events as ev
    ups += await ev.mission_progress(conn, settings, user_id, "activity", a["kind"], 1)
    await ev.season_score(conn, user_id, a["xp"])
    text = f"✅ {a['name']} تمام شد.\n" + ("دستاورد: " + "، ".join(got) if got else "این بار دست‌خالی برگشتی.")
    msg = "\n".join([text] + lines + ups)
    await notify(conn, user_id, "activity", msg)
    return [msg]


# ───────── استراحت ─────────
async def start_rest(conn, settings, user_id: int, ch, hours: int):
    require_home(ch, "استراحت")
    if ch["location"] == "resting":
        raise GameError("در حال استراحتی.")
    hours = max(1, min(int(hours), int(settings.get("rest.max_hours"))))
    until = now() + timedelta(hours=hours)
    await conn.execute("UPDATE characters SET location='resting', rest_until=$2 WHERE user_id=$1", user_id, until)
    await schedule("rest_end", {"user_id": user_id}, until)
    return f"😴 {hours} ساعت استراحت شروع شد. لازم نیست آنلاین بمانی."


async def settle_rest(conn, settings, user_id: int) -> list:
    ch = await conn.fetchrow("SELECT * FROM characters WHERE user_id=$1", user_id)
    if ch["location"] != "resting" or ch["rest_until"] is None or ch["rest_until"] > now():
        return []
    await refresh_vitals(conn, settings, user_id, at=ch["rest_until"])
    await conn.execute("UPDATE characters SET location='home', rest_until=NULL WHERE user_id=$1", user_id)
    msg = "😌 استراحتت تمام شد."
    await notify(conn, user_id, "rest", msg)
    return [msg]


async def fast_rest(conn, settings, user_id: int, ch):
    if ch["location"] != "resting" or ch["rest_until"] is None:
        raise GameError("در حال استراحت نیستی.")
    left_h = max(0.0, (ch["rest_until"] - now()).total_seconds() / 3600)
    cost = max(1, int(round(left_h * int(settings.get("rest.fast_cost_per_hour")))))
    try:
        await change_coins(conn, user_id, -cost, "fast_rest")
    except Exception as exc:  # noqa: BLE001
        raise GameError(f"سکه کافی نداری (نیاز: {cost}).") from exc
    await refresh_vitals(conn, settings, user_id, at=ch["rest_until"])
    await conn.execute("UPDATE characters SET location='home', rest_until=NULL WHERE user_id=$1", user_id)
    return f"⚡️ استراحت سریع انجام شد (هزینه: {cost} 🪙)"


async def set_stance(conn, user_id: int, stance: str):
    if stance not in STANCE_FA:
        raise GameError("حالت نامعتبر.")
    await conn.execute("UPDATE characters SET stance=$2 WHERE user_id=$1", user_id, stance)


async def consume_item(conn, settings, user_id: int, def_id: int):
    return await inv.consume(conn, user_id, def_id)
