"""گروگانگیری گروهی (۲ تا ۴ نفر) و حمله به خانه (تکی یا تا ۵ نفر).

مراحل حمله به خانه:  دعوت هم‌دستان ← رسیدن به خانه (تله‌ها) ← تصمیم صاحب‌خانه (دفاع/تسلیم/بی‌پاسخ=خواب)
                      ← تصمیم هر مهاجم درباره نگهبان (مبارزه یا دور زدن) ← نبرد ← غارت ۵ دقیقه‌ای."""
from __future__ import annotations

import json
import random
import re
from html import escape

from app.services import animals as an
from app.services import combat as cb
from app.services import homes, rules
from app.services.chars import get_char, is_protected, now, player_stats
from app.services.errors import GameError
from app.services.notify import notify

USER_RE = re.compile(r"@([A-Za-z0-9_]{3,32})")
OUTSIDE = ("hunting", "gathering", "exploring", "operation")


def parse_mentions(text: str) -> list:
    seen = []
    for m in USER_RE.finditer(text or ""):
        u = m.group(1).lower()
        if u not in seen:
            seen.append(u)
    return seen


def tag(c, uid) -> str:
    m = c["mods"]
    name = escape(str(m["names"].get(str(uid), "?")))
    tg = m["tg"].get(str(uid))
    return f'<a href="tg://user?id={tg}">{name}</a>' if tg else name


async def _resolve(conn, usernames: list) -> list:
    if not usernames:
        return []
    rows = await conn.fetch("SELECT u.id, u.telegram_id, u.username, c.name FROM users u JOIN characters c ON c.user_id=u.id "
                            "WHERE lower(u.username) = ANY($1::text[])", usernames)
    found = {r["username"].lower() for r in rows}
    missing = [u for u in usernames if u not in found]
    if missing:
        raise GameError("این بازیکن‌ها در بازی ثبت‌نام نکرده‌اند: " + "، ".join("@" + u for u in missing))
    return rows


async def _validate_member(conn, settings, uid: int, name: str):
    ch = await get_char(conn, uid)
    if ch is None:
        raise GameError(f"{name} شخصیت ندارد.")
    if ch["location"] != "home":
        raise GameError(f"{name} باید در خانه و آزاد باشد.")
    if ch["energy"] < int(settings.get("combat.attack_energy")) or ch["health"] < 15:
        raise GameError(f"{name} انرژی یا سلامتی کافی ندارد.")
    if await cb.in_combat(conn, [uid]):
        raise GameError(f"{name} در نبرد دیگری است.")


async def _member_parts(conn, settings, uid: int):
    me, st, ch = await cb._player_part(conn, settings, uid)
    comp, _ = await cb._companions(conn, settings, uid)
    for p in comp:
        p["owner"] = uid
    return [me] + comp, st, ch


STRUCT = {"k": "struct", "id": 0, "name": "دفاع خانه", "hp": 1, "max": 1, "power": 0, "def": 0}


async def start(conn, settings, initiator_id: int, target_id: int, kind: str, usernames: list, chat_id, message_id=None,
                rng: random.Random = None):
    """kind: hostage | raid"""
    rng = rng or random.Random()
    if initiator_id == target_id:
        raise GameError("هدف نمی‌تواند خودت باشی.")
    invitees = await _resolve(conn, usernames)
    ids = [initiator_id] + [r["id"] for r in invitees]
    if len(set(ids)) != len(ids):
        raise GameError("یک نفر را دوبار وارد کرده‌ای.")
    if target_id in ids:
        raise GameError("هدف نمی‌تواند هم‌دست باشد.")
    lo, hi = (int(settings.get("hostage.min_party")), int(settings.get("hostage.max_party"))) if kind == "hostage" \
        else (1, int(settings.get("raid.max_party")))
    if not lo <= len(ids) <= hi:
        raise GameError(f"تعداد نفرات باید بین {lo} تا {hi} باشد (با خودت).")
    await conn.fetch("SELECT id FROM users WHERE id = ANY($1::bigint[]) ORDER BY id FOR UPDATE", ids + [target_id])
    t_ch, tu = await get_char(conn, target_id), await conn.fetchrow("SELECT * FROM users WHERE id=$1", target_id)
    if t_ch is None or tu["is_banned"]:
        raise GameError("هدف در دسترس نیست.")
    if t_ch["location"] in ("dead", "captive"):
        raise GameError("هدف الان قابل‌حمله نیست (مرده یا اسیر).")
    if kind == "hostage" and t_ch["location"] not in OUTSIDE:
        raise GameError("برای گروگانگیری هدف باید بیرون از خانه باشد؛ برای خانه بنویس «حمله به خانه».")
    if kind == "raid" and t_ch["location"] not in ("home", "resting"):
        raise GameError("هدف بیرون از خانه است؛ بنویس «حمله» یا «گروگانگیری».")
    if is_protected(tu):
        raise GameError("🛡 این بازیکن هنوز محافظ تازه‌وارد دارد.")
    if kind == "hostage" and tu["captive_immune_until"] and tu["captive_immune_until"] > now():
        raise GameError("این بازیکن تازه از اسارت آزاد شده و موقتاً مصون است.")
    if await cb.in_combat(conn, [target_id]):
        raise GameError("هدف درگیر نبرد دیگری است.")
    cd = int(settings.get("combat.repeat_cooldown_min"))
    if await conn.fetchval("SELECT 1 FROM combats WHERE attacker_user_id=$1 AND defender_user_id=$2 "
                           "AND created_at > now() - make_interval(mins => $3)", initiator_id, target_id, cd):
        raise GameError(f"به همین هدف تا {cd} دقیقه بعد از حمله قبلی نمی‌توانی دوباره حمله کنی.")
    await _validate_member(conn, settings, initiator_id, "خودت")
    names = {r["user_id"]: r["name"] for r in await conn.fetch(
        "SELECT user_id, name FROM characters WHERE user_id = ANY($1::bigint[])", ids + [target_id])}
    tgs = {r["id"]: r["telegram_id"] for r in await conn.fetch("SELECT id, telegram_id FROM users WHERE id = ANY($1::bigint[])", ids + [target_id])}
    mods = {"att_type": "direct", "ambush": None, "recon_extra": False, "alerted": False, "def_joined": False, "trap": 0.0,
            "trap_seen": False, "def_base": 1.0, "looter": None, "drop_id": None, "raid_steps": 0, "result": "", "notice": "",
            "recon": [], "names": {str(k): v for k, v in names.items()}, "tg": {str(k): v for k, v in tgs.items()},
            "invites": {str(r["id"]): "pending" for r in invitees}, "away": False, "kind": kind, "stealth": {},
            "def_perception": 3, "def_choice": None, "gc": {}, "sneaked": [], "guard_parts": []}
    flow = "open" if kind == "hostage" else "home"
    cid = await conn.fetchval(
        "INSERT INTO combats(chat_id, message_id, flow, kind, attacker_user_id, defender_user_id, state, mods, sides, party) "
        "VALUES($1,$2,$3,$4,$5,$6,'party',$7::jsonb,'{\"att\":[],\"def\":[]}'::jsonb,$8::jsonb) RETURNING id",
        chat_id, message_id, flow, kind, initiator_id, target_id, json.dumps(mods, ensure_ascii=False), json.dumps([initiator_id]))
    c = await cb.get(conn, cid)
    if not invitees:
        await _begin(conn, settings, c, rng)
    else:
        await cb._save(conn, c)
        await cb._arm_timer(conn, settings, c, int(settings.get("party.invite_sec")))
    return c


async def respond(conn, settings, c, user_id: int, accept: bool, rng: random.Random):
    m = c["mods"]
    if c["state"] != "party" or m["invites"].get(str(user_id)) != "pending":
        raise GameError("این درخواست برای تو نیست یا پاسخ داده‌ای.")
    if accept:
        await _validate_member(conn, settings, user_id, "تو")
    m["invites"][str(user_id)] = "ok" if accept else "no"
    if all(v != "pending" for v in m["invites"].values()):
        await _begin(conn, settings, c, rng)
    else:
        await cb._save(conn, c)


async def on_party_timeout(conn, settings, c, rng):
    for u, v in c["mods"]["invites"].items():
        if v == "pending":
            c["mods"]["invites"][u] = "no"
    await _begin(conn, settings, c, rng)


async def _close(conn, c, text: str, outcome: str = "cancelled"):
    c["mods"]["result"] = text
    await cb._save(conn, c, state="closed", outcome=outcome, ended_at=now())


async def _begin(conn, settings, c, rng):
    m, kind = c["mods"], c["mods"]["kind"]
    ok_ids = [c["attacker_user_id"]] + [int(u) for u, v in m["invites"].items() if v == "ok"]
    lo = int(settings.get("hostage.min_party")) if kind == "hostage" else 1
    if len(ok_ids) < lo:
        return await _close(conn, c, "هم‌دستان کافی نشدند؛ درخواست لغو شد.")
    cost = int(settings.get("combat.attack_energy"))
    att = []
    try:
        for uid in ok_ids:
            await _validate_member(conn, settings, uid, m["names"].get(str(uid), "یکی از اعضا"))
            parts, st, ch = await _member_parts(conn, settings, uid)
            att += parts
            m["stealth"][str(uid)] = st["stealth"]
    except GameError as exc:
        return await _close(conn, c, f"شروع نشد: {exc}")
    await conn.execute("UPDATE characters SET location='operation', energy=energy-$2 WHERE user_id = ANY($1::bigint[])", ok_ids, cost)
    await conn.execute("UPDATE users SET protected_until=NULL WHERE id = ANY($1::bigint[])", ok_ids)
    await conn.execute("UPDATE combats SET party=$2::jsonb WHERE id=$1", c["id"], json.dumps(ok_ids))
    c["party"], m["away"] = ok_ids, True
    c["sides"]["att"] = att
    tid = c["defender_user_id"]
    if kind == "hostage":
        t_ch = await get_char(conn, tid)
        if t_ch["location"] not in OUTSIDE:
            return await _close(conn, c, "هدف به خانه برگشته بود؛ گروگانگیری لغو شد.")
        tp, tst, _ = await cb._player_part(conn, settings, tid)
        tcomp, _ = await cb._companions(conn, settings, tid)
        m["def_perception"] = tst["perception"]
        c["sides"]["def"] = [tp] + tcomp
        c["log"].append("درخواست شما توسط اشخاص مورد نظر تأیید شد، در حال شروع گروگانگیری 🥷🏻" if len(ok_ids) > 1
                        else "در حال شروع گروگانگیری 🥷🏻")
        await notify(conn, tid, "hostage_attempt", "🚨 گروهی قصد گروگان‌گرفتن تو را دارند! از پیام گروه می‌توانی فرار کنی.")
        await cb._save(conn, c, state="round", round_no=0)
        await cb._arm_timer(conn, settings, c)
        return await cb._round(conn, settings, c, rng)
    await _raid_approach(conn, settings, c, rng)


# ───────── حمله به خانه ─────────
async def _raid_approach(conn, settings, c, rng):
    m, tid = c["mods"], c["defender_user_id"]
    dp = await homes.defense_participants(conn, settings, tid)
    hs = dp["stats"]
    t_ch = await get_char(conn, tid)
    t_st = await player_stats(conn, settings, tid, t_ch)
    alert = an.perk_total(await an.guards_at_home(conn, settings, tid), "alert")
    m["trap"], m["def_base"] = hs["trap"], 1 + hs["defense"] / 100.0
    m["def_perception"] = t_st["perception"] + hs["alarm"] + alert
    m["guard_parts"] = dp["parts"]
    delay, lines = 0, []
    for p in [x for x in c["sides"]["att"] if x["k"] == "player"]:
        ch = await get_char(conn, p["id"])
        perc = (await player_stats(conn, settings, p["id"], ch))["perception"]
        if hs["trap"] <= 0:
            lines.append(f"✅ {p['name']} بدون مانع رسید")
        elif rng.random() < rules.trap_detect_chance(perc, ch["level"], hs["trap"]):
            delay += int(settings.get("raid.reroute_delay_min"))
            lines.append(f"🔎 {p['name']} تله را شناسایی کرد و مسیرش را عوض کرد (+{settings.get('raid.reroute_delay_min')} دقیقه)")
        else:
            dmg = max(1, int(hs["trap"] * rng.uniform(0.4, 0.9)))
            p["hp"] = max(1, p["hp"] - dmg)
            lines.append(f"🪤 {p['name']} در تله افتاد ({dmg} آسیب)")
    m["approach_lines"] = lines
    minutes = int(settings.get("raid.approach_min")) + delay
    m["arrive_min"] = minutes
    c["sides"]["def"] = [dict(STRUCT)]
    await cb._save(conn, c, state="approach")
    await cb._arm_timer(conn, settings, c, minutes * 60)


async def raid_prompt(conn, settings, c, rng):
    m = c["mods"]
    m["defend_min"] = int(settings.get("raid.defend_prompt_min"))
    await cb._save(conn, c, state="defend_prompt")
    await cb._arm_timer(conn, settings, c, int(settings.get("raid.defend_prompt_min")) * 60)
    await notify(conn, c["defender_user_id"], "raid", f"🚨 به خانه‌ات {len(c['party'])} نفری حمله شده! "
                 "در پیام گروه برای دفاع یا تسلیم تصمیم بگیر؛ اگر جواب ندهی خواب فرض می‌شوی.")


async def defender_choice(conn, settings, c, user_id: int, choice: str, rng):
    if c["state"] != "defend_prompt" or user_id != c["defender_user_id"]:
        raise GameError("اجازه نداری.")
    c["mods"]["def_choice"] = choice
    await _after_decision(conn, settings, c, rng)


async def _after_decision(conn, settings, c, rng):
    m = c["mods"]
    choice = m["def_choice"] or "asleep"
    m["def_choice"] = choice
    guards = [g for g in m["guard_parts"] if g["hp"] > 0]
    tid = c["defender_user_id"]
    if choice == "surrender":
        c["sides"]["def"] = [dict(STRUCT)]
        c["log"].append("🏳 صاحب‌خانه تسلیم شد؛ نگهبان‌ها کنار رفتند")
        return await to_loot(conn, settings, c, "صاحب‌خانه تسلیم شد.")
    defs = [dict(STRUCT)]
    if choice == "defend":
        tp, _, _ = await cb._player_part(conn, settings, tid)
        comp, _ = await cb._companions(conn, settings, tid)
        have = {(g["k"], g["id"]) for g in guards}
        defs += [tp] + [x for x in comp if (x["k"], x["id"]) not in have]
        m["def_alert"] = True
        c["log"].append("🛡 صاحب‌خانه تصمیم به دفاع گرفت")
    else:
        c["log"].append("😴 صاحب‌خانه جواب نداد؛ خواب فرض شد")
    defs += guards
    c["sides"]["def"] = defs
    if guards:
        m["gc"] = {str(u): None for u in c["party"]}
        await cb._save(conn, c, state="guard_choice")
        return await cb._arm_timer(conn, settings, c, int(settings.get("raid.guard_choice_sec")))
    if choice == "defend":
        m["att_type"] = "direct"
        await cb._save(conn, c, state="round", round_no=0)
        await cb._arm_timer(conn, settings, c)
        return await cb._round(conn, settings, c, rng)
    await to_loot(conn, settings, c, "خانه بدون مدافع بود.")


async def guard_decision(conn, settings, c, user_id: int, choice: str, rng):
    m = c["mods"]
    if c["state"] != "guard_choice" or str(user_id) not in m["gc"]:
        raise GameError("اجازه نداری.")
    if m["gc"][str(user_id)]:
        raise GameError("قبلاً تصمیم گرفته‌ای.")
    m["gc"][str(user_id)] = choice
    if all(v for v in m["gc"].values()):
        await resolve_guards(conn, settings, c, rng)
    else:
        await cb._save(conn, c)


async def resolve_guards(conn, settings, c, rng):
    m = c["mods"]
    fighters, sneaked = [], []
    for uid in c["party"]:
        choice = m["gc"].get(str(uid)) or "fight"
        if choice == "bypass":
            if rules.stealth_check(float(m["stealth"].get(str(uid), 1)), m["def_perception"], 0, rng):
                sneaked.append(uid)
                c["log"].append(f"🕵️ {m['names'].get(str(uid))} بی‌سروصدا از کنار نگهبان‌ها رد شد")
            else:
                fighters.append(uid)
                c["log"].append(f"🚨 {m['names'].get(str(uid))} موقع دور زدن لو رفت و باید بجنگد")
        else:
            fighters.append(uid)
    m["sneaked"] = sneaked

    def owner(p):
        return p["id"] if p["k"] == "player" else p.get("owner")

    for p in [x for x in c["sides"]["att"] if owner(x) in sneaked]:
        await cb._persist_part(conn, p)
    c["sides"]["att"] = [p for p in c["sides"]["att"] if owner(p) in fighters]
    if not fighters:
        return await to_loot(conn, settings, c, "همه از کنار نگهبان‌ها رد شدند.")
    m["att_type"] = "direct"
    await cb._save(conn, c, state="round", round_no=0)
    await cb._arm_timer(conn, settings, c)
    await cb._round(conn, settings, c, rng)


async def to_loot(conn, settings, c, note: str):
    """ورود بدون درگیری (یا تسلیم/خواب): همه اعضا فرصت غارت محدود دارند."""
    m = c["mods"]
    await cb.persist_all(conn, settings, c)
    m["looter"], m["result"] = "att", note
    await cb._save(conn, c, state="loot", outcome="raid_open", victor="att")
    await cb._arm_timer(conn, settings, c, int(settings.get("combat.loot_minutes")) * 60)
    await cb._inform_defenders(conn, c, "🏚 مهاجمان وارد خانه‌ات شدند و در حال غارت هستند. " + note)


async def on_timeout(conn, settings, c, rng) -> bool:
    st = c["state"]
    if st == "party":
        await on_party_timeout(conn, settings, c, rng)
    elif st == "approach":
        await raid_prompt(conn, settings, c, rng)
    elif st == "defend_prompt":
        c["mods"]["def_choice"] = None
        await _after_decision(conn, settings, c, rng)
    elif st == "guard_choice":
        await resolve_guards(conn, settings, c, rng)
    else:
        return False
    return True


# ───────── نمایش ─────────
def describe(c) -> tuple:
    m = c["mods"]
    kind_fa = "گروگانگیری" if m["kind"] == "hostage" else "حمله به خانه"
    target = tag(c, c["defender_user_id"])
    if c["state"] == "party":
        lines = [f"🥷 <b>درخواست {kind_fa}</b> علیه {target}", f"سرگروه: {tag(c, c['attacker_user_id'])}", ""]
        for u, v in m["invites"].items():
            lines.append({"pending": f"⏳ منتظر تأیید {tag(c, u)}", "ok": f"✅ {tag(c, u)} پذیرفت",
                          "no": f"❌ کاربر {tag(c, u)} درخواست شما را رد کرد."}[v])
        return "\n".join(lines), [("✅ تأیید", "pj"), ("❌ رد", "pn"), ("❌ لغو درخواست", "cancel")]
    if c["state"] == "approach":
        lines = [f"🏚 <b>حمله به خانه {target}</b>", f"👥 {len(c['party'])} نفر در راه خانه‌اند ({m.get('arrive_min', '?')} دقیقه)", ""]
        lines += m.get("approach_lines", [])
        return "\n".join(lines), []
    if c["state"] == "defend_prompt":
        lines = [f"🚨 بازمانده {target} به خانه شما {len(c['party'])} نفری حمله شده، آیا دفاع می‌کنید؟",
                 f"(تا {settings_minutes(m)} دقیقه فرصت داری؛ بی‌پاسخی یعنی خواب بوده‌ای و غارت آزاد است)"]
        return "\n".join(lines), [("🛡 دفاع", "defend"), ("🏳 تسلیم", "surrender")]
    if c["state"] == "guard_choice":
        lines = ["⚔️ <b>نگهبان‌ها سر راه‌اند!</b> هر مهاجم تصمیم بگیرد:", ""]
        for u, v in m["gc"].items():
            lines.append(f"بازمانده {tag(c, u)} آیا می‌خواهی با نگهبان مبارزه کنی؟  " +
                         {None: "⏳", "fight": "⚔️ مبارزه", "bypass": "🕵️ دور زدن"}[v])
        return "\n".join(lines), [("⚔️ مبارزه با نگهبان", "gfight"), ("🕵️ دور زدن مسیر", "gbypass")]
    return "", []


def settings_minutes(m) -> str:
    return str(m.get("defend_min", 5))
