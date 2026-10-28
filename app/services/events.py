"""مأموریت‌ها، رویدادهای گروهی (محموله/جانور)، فصل‌های رقابتی."""
from __future__ import annotations

import json
import random
from datetime import timedelta

from app.services import inventory as inv
from app.services.chars import get_char, grant_char_xp, now, player_stats, require_free
from app.services.economy import change_coins
from app.services.errors import GameError
from app.services.notify import notify
from app.services.sched import schedule


# ───────── مأموریت ─────────
async def active_mission(conn, user_id: int):
    return await conn.fetchrow(
        "SELECT m.*, d.name, d.kind, d.target, d.amount, d.reward_coins, d.reward_xp, d.description FROM missions_active m "
        "JOIN mission_defs d ON d.id=m.def_id WHERE m.user_id=$1 AND m.status='active'", user_id)


async def new_mission(conn, settings, user_id: int, rng: random.Random = None):
    rng = rng or random.Random()
    if await active_mission(conn, user_id):
        raise GameError("یک مأموریت فعال داری.")
    cd = int(settings.get("missions.cooldown_min"))
    last = await conn.fetchval("SELECT max(expires_at - make_interval(mins => d.duration_min)) FROM missions_active m "
                               "JOIN mission_defs d ON d.id=m.def_id WHERE m.user_id=$1", user_id)
    if last and now() - last < timedelta(minutes=cd):
        raise GameError(f"مأموریت بعدی را {cd} دقیقه بعد از مأموریت قبلی می‌توانی بگیری.")
    defs = await conn.fetch("SELECT * FROM mission_defs WHERE is_active")
    if not defs:
        raise GameError("مأموریتی تعریف نشده.")
    d = rng.choice(defs)
    await conn.execute("INSERT INTO missions_active(user_id, def_id, expires_at) VALUES($1,$2,$3)", user_id, d["id"],
                       now() + timedelta(minutes=d["duration_min"]))
    return d["name"]


async def _complete(conn, settings, user_id: int, m) -> list:
    await conn.execute("UPDATE missions_active SET status='done' WHERE id=$1", m["id"])
    await change_coins(conn, user_id, m["reward_coins"], "mission", ref=m["name"])
    msgs = [f"🎯 مأموریت «{m['name']}» انجام شد: +{m['reward_coins']} 🪙"]
    msgs += await grant_char_xp(conn, settings, user_id, m["reward_xp"])
    return msgs


async def mission_progress(conn, settings, user_id: int, kind: str, target: str, n: int) -> list:
    m = await active_mission(conn, user_id)
    if m is None or m["kind"] != kind or m["target"] != target or m["expires_at"] <= now():
        return []
    prog = m["progress"] + n
    await conn.execute("UPDATE missions_active SET progress=$2 WHERE id=$1", m["id"], prog)
    if prog >= m["amount"]:
        return await _complete(conn, settings, user_id, m)
    return []


async def deliver_mission(conn, settings, user_id: int) -> list:
    m = await active_mission(conn, user_id)
    if m is None or m["kind"] != "deliver":
        raise GameError("مأموریت تحویل فعالی نداری.")
    d = await inv.def_by_code(conn, m["target"])
    need = m["amount"]
    hb = await inv.stack_qty(conn, "backpack", user_id, d["id"])
    hs = await inv.stack_qty(conn, "storage", user_id, d["id"])
    if hb + hs < need:
        raise GameError(f"{d['name']} کافی نداری (نیاز: {need}).")
    if min(hb, need):
        await inv.take_stack(conn, "backpack", user_id, d["id"], min(hb, need), "mission")
    if need - min(hb, need):
        await inv.take_stack(conn, "storage", user_id, d["id"], need - min(hb, need), "mission")
    return await _complete(conn, settings, user_id, m)


async def expire_missions(conn, user_id: int):
    await conn.execute("UPDATE missions_active SET status='expired' WHERE user_id=$1 AND status='active' AND expires_at <= now()", user_id)


# ───────── فصل رقابتی ─────────
async def create_season(conn, name: str, start_in_hours: float, days: float, fee: int, rewards: list, min_level: int = 1) -> int:
    s = now() + timedelta(hours=start_in_hours)
    e = s + timedelta(days=days)
    sid = await conn.fetchval(
        "INSERT INTO seasons(name, starts_at, ends_at, entry_fee, rewards, min_level) VALUES($1,$2,$3,$4,$5::jsonb,$6) RETURNING id",
        name, s, e, fee, json.dumps(rewards), min_level)
    await schedule("season_tick", {}, s)
    await schedule("season_tick", {}, e)
    return sid


async def current_season(conn):
    return await conn.fetchrow("SELECT * FROM seasons WHERE status IN ('scheduled','active') ORDER BY starts_at LIMIT 1")


async def join_season(conn, settings, user_id: int, ch):
    s = await current_season(conn)
    if s is None or s["ends_at"] <= now():
        raise GameError("فصل فعالی وجود ندارد.")
    if ch["level"] < s["min_level"]:
        raise GameError(f"حداقل سطح شخصیت: {s['min_level']}")
    if await conn.fetchval("SELECT 1 FROM season_entries WHERE season_id=$1 AND user_id=$2", s["id"], user_id):
        raise GameError("قبلاً ثبت‌نام کرده‌ای.")
    if s["entry_fee"]:
        try:
            await change_coins(conn, user_id, -s["entry_fee"], "season_fee", ref=str(s["id"]))
        except Exception as exc:  # noqa: BLE001
            raise GameError("سکه کافی نداری.") from exc
    await conn.execute("INSERT INTO season_entries(season_id, user_id) VALUES($1,$2)", s["id"], user_id)
    return s["name"]


async def season_score(conn, user_id: int, points: int):
    await conn.execute(
        "UPDATE season_entries e SET score = score + $2 FROM seasons s WHERE e.season_id=s.id AND e.user_id=$1 "
        "AND s.status='active' AND s.ends_at > now()", user_id, points)


async def leaderboard(conn, season_id: int, limit: int = 10):
    return await conn.fetch(
        "SELECT e.score, c.name FROM season_entries e JOIN characters c ON c.user_id=e.user_id "
        "WHERE e.season_id=$1 ORDER BY e.score DESC LIMIT $2", season_id, limit)


async def season_tick(conn) -> list:
    out = []
    await conn.execute("UPDATE seasons SET status='active' WHERE status='scheduled' AND starts_at <= now() AND ends_at > now()")
    for s in await conn.fetch("SELECT * FROM seasons WHERE status IN ('scheduled','active') AND ends_at <= now() FOR UPDATE"):
        rewards = json.loads(s["rewards"]) if isinstance(s["rewards"], str) else s["rewards"]
        top = await conn.fetch("SELECT user_id, score FROM season_entries WHERE season_id=$1 AND score > 0 "
                               "ORDER BY score DESC LIMIT $2", s["id"], max(len(rewards), 1))
        for i, row in enumerate(top):
            if i < len(rewards) and rewards[i] > 0:
                await change_coins(conn, row["user_id"], int(rewards[i]), "season_reward", ref=str(s["id"]))
                await notify(conn, row["user_id"], "season", f"🏆 رتبه {i + 1} فصل «{s['name']}»: +{rewards[i]} 🪙")
        await conn.execute("UPDATE seasons SET status='ended' WHERE id=$1", s["id"])
        out.append(s["name"])
    return out


# ───────── رویدادهای گروه ─────────
async def spawn_event(conn, settings, chat_id: int, kind: str = None, rng: random.Random = None):
    rng = rng or random.Random()
    kind = kind or rng.choice(["cargo", "beast"])
    if await conn.fetchval("SELECT 1 FROM events WHERE chat_id=$1 AND status='open'", chat_id):
        raise GameError("در این گروه رویداد بازی در جریان است.")
    ends = now() + timedelta(minutes=int(settings.get("events.duration_min")))
    if kind == "cargo":
        data = {"slots": int(settings.get("events.cargo_slots")), "coins": int(settings.get("events.cargo_coins")), "claimed": 0}
    else:
        data = {"hp": int(settings.get("events.beast_hp")), "name": rng.choice(["گرگ وحشی", "خرس غول‌پیکر", "گراز خشمگین"])}
    eid = await conn.fetchval("INSERT INTO events(chat_id, kind, data, ends_at) VALUES($1,$2,$3::jsonb,$4) RETURNING id",
                              chat_id, kind, json.dumps(data, ensure_ascii=False), ends)
    await schedule("event_end", {"event_id": eid}, ends)
    return await conn.fetchrow("SELECT * FROM events WHERE id=$1", eid)


async def event_row(conn, eid: int, lock=False):
    e = await conn.fetchrow("SELECT * FROM events WHERE id=$1" + (" FOR UPDATE" if lock else ""), eid)
    if e is None:
        raise GameError("رویداد پیدا نشد.")
    return e


def event_text(e, claims: list = None) -> tuple:
    d = json.loads(e["data"]) if isinstance(e["data"], str) else e["data"]
    if e["kind"] == "cargo":
        t = (f"📦 <b>محموله ارزشمند!</b>\nیک محموله در جنگل پیدا شد. ظرفیت برداشت: {d['slots']} نفر، "
             f"هر نفر {d['coins']} 🪙\nبرداشت‌شده: {d['claimed']}")
        btn = [("📦 برداشت محموله", "claim")] if e["status"] == "open" else []
    else:
        total = sum(c["power"] for c in (claims or []))
        t = (f"🐗 <b>{d['name']} حمله کرد!</b>\nقدرت جانور: {d['hp']}\nقدرت جمع‌شده شما: {int(total)} "
             f"({len(claims or [])} نفر)\nهمه با هم مقابله کنید!")
        btn = [("⚔️ مقابله با جانور", "claim")] if e["status"] == "open" else []
    if e["status"] != "open":
        t += "\n\n🏁 رویداد پایان یافت."
    return t, btn


async def claim_event(conn, settings, user_id: int, eid: int, rng=None):
    e = await event_row(conn, eid, True)
    if e["status"] != "open" or e["ends_at"] <= now():
        raise GameError("این رویداد تمام شده است.")
    ch = await get_char(conn, user_id)
    if ch is None:
        raise GameError("اول شخصیت بساز.")
    require_free(ch)
    if ch["location"] != "home":
        raise GameError("باید در خانه (آزاد) باشی.")
    if await conn.fetchval("SELECT 1 FROM event_claims WHERE event_id=$1 AND user_id=$2", eid, user_id):
        raise GameError("قبلاً شرکت کرده‌ای.")
    d = json.loads(e["data"]) if isinstance(e["data"], str) else e["data"]
    if e["kind"] == "cargo":
        if d["claimed"] >= d["slots"]:
            raise GameError("محموله تمام شد.")
        d["claimed"] += 1
        await conn.execute("INSERT INTO event_claims(event_id, user_id) VALUES($1,$2)", eid, user_id)
        await change_coins(conn, user_id, d["coins"], "event_cargo", ref=str(eid))
        done = d["claimed"] >= d["slots"]
        await conn.execute("UPDATE events SET data=$2::jsonb, status=$3 WHERE id=$1", eid, json.dumps(d, ensure_ascii=False),
                           "done" if done else "open")
        return f"📦 {d['coins']} 🪙 برداشتی!"
    st = await player_stats(conn, settings, user_id, ch)
    await conn.execute("INSERT INTO event_claims(event_id, user_id, power) VALUES($1,$2,$3)", eid, user_id, int(st["power"]))
    return "⚔️ به نبرد پیوستی؛ نتیجه در پایان رویداد اعلام می‌شود."


async def finish_event(conn, settings, eid: int):
    """پایان رویداد جانور: اگر قدرت جمع‌شده کافی باشد همه پاداش می‌گیرند. متن نتیجه را برمی‌گرداند."""
    e = await event_row(conn, eid, True)
    if e["status"] != "open":
        return None
    d = json.loads(e["data"]) if isinstance(e["data"], str) else e["data"]
    claims = await conn.fetch("SELECT * FROM event_claims WHERE event_id=$1", eid)
    await conn.execute("UPDATE events SET status='done' WHERE id=$1", eid)
    if e["kind"] == "cargo":
        return f"📦 محموله تمام شد؛ {d['claimed']} نفر برداشتند."
    total = sum(c["power"] for c in claims)
    if claims and total >= d["hp"]:
        reward = int(settings.get("events.beast_reward"))
        for c in claims:
            await change_coins(conn, c["user_id"], reward, "event_beast", ref=str(eid))
            await grant_char_xp(conn, settings, c["user_id"], 10)
            await season_score(conn, c["user_id"], 5)
            await notify(conn, c["user_id"], "event", f"🏆 {d['name']} شکست خورد! +{reward} 🪙")
        return f"🏆 {d['name']} با قدرت جمعی {int(total)} شکست خورد؛ {len(claims)} نفر پاداش گرفتند."
    for c in claims:
        await conn.execute("UPDATE characters SET health=GREATEST(1, health-10) WHERE user_id=$1", c["user_id"])
    return f"💀 {d['name']} پیروز شد (قدرت جمع‌شده {int(total)} از {d['hp']}). شرکت‌کنندگان کمی زخمی شدند."
