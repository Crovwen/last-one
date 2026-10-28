"""موتور مبارزه وضعیت‌محور. همه‌چیز در جدول combats ذخیره می‌شود؛ با ری‌استارت چیزی از بین نمی‌رود.

حالت‌ها: recon → choose → round → loot → closed
هر تغییر، version را بالا می‌برد تا دکمه‌های قدیمی بی‌اثر شوند."""
from __future__ import annotations

import json
import random
from datetime import timedelta

from app.bot_fmt import bar, fa_num
from app.services import animals as an
from app.services import captivity, death, homes
from app.services import hirelings as hr
from app.services import inventory as inv
from app.services import rules
from app.services.chars import get_char, jload, now, player_stats
from app.services.errors import GameError
from app.services.notify import notify
from app.services.sched import schedule

ATTACK_FA = {"direct": "حمله مستقیم", "ambush": "حمله غافلگیرانه", "pursue": "تعقیب", "siege": "محاصره"}
OUTCOME_FA = {
    "att_defeated": "مهاجم شکست خورد", "def_defeated": "مدافع شکست خورد", "fled_att": "مهاجم عقب‌نشینی کرد",
    "fled_def": "مدافع فرار کرد", "cancelled": "حمله لغو شد", "timeout": "مهلت تمام شد؛ حمله لغو شد",
}


def _params(settings) -> dict:
    return {"dmg_k": float(settings.get("combat.dmg_k")), "armor_k": float(settings.get("combat.armor_k")),
            "luck_range": float(settings.get("combat.luck_range")),
            "adv_k": float(settings.get("combat.advantage_k"))}


def _alive(parts):
    return [p for p in parts if p["hp"] > 0 and p["k"] != "struct"]


# ───────── ذخیره و بارگذاری ─────────
async def get(conn, cid: int, lock: bool = True):
    r = await conn.fetchrow("SELECT * FROM combats WHERE id=$1" + (" FOR UPDATE" if lock else ""), cid)
    if r is None:
        raise GameError("این نبرد پیدا نشد.")
    c = dict(r)
    c["sides"], c["mods"], c["log"] = jload(r["sides"]), jload(r["mods"]), jload(r["log"])
    return c


async def _save(conn, c, **fields):
    c["version"] += 1
    sets = ["sides=$2::jsonb", "mods=$3::jsonb", "log=$4::jsonb", "version=$5"]
    args = [c["id"], json.dumps(c["sides"], ensure_ascii=False), json.dumps(c["mods"], ensure_ascii=False),
            json.dumps(c["log"][-40:], ensure_ascii=False), c["version"]]
    for k, v in fields.items():
        args.append(v)
        sets.append(f"{k}=${len(args)}")
        c[k] = v
    await conn.execute(f"UPDATE combats SET {', '.join(sets)} WHERE id=$1", *args)


async def _arm_timer(conn, settings, c, seconds: int = None):
    sec = seconds or int(settings.get("combat.round_timeout_sec"))
    dl = now() + timedelta(seconds=sec)
    await conn.execute("UPDATE combats SET deadline=$2 WHERE id=$1", c["id"], dl)
    c["deadline"] = dl
    await schedule("combat_timeout", {"cid": c["id"], "version": c["version"]}, dl)


# ───────── ساخت شرکت‌کنندگان ─────────
async def _player_part(conn, settings, user_id: int):
    ch = await get_char(conn, user_id)
    st = await player_stats(conn, settings, user_id, ch)
    return {"k": "player", "id": user_id, "name": ch["name"], "hp": ch["health"], "max": 100, "power": st["power"],
            "def": st["defense"], "end": st["endurance"]}, st, ch


async def _companions(conn, settings, user_id: int):
    parts = [c["part"] for c in await an.companions(conn, settings, user_id)]
    parts += [c["part"] for c in await hr.companions(conn, settings, user_id)]
    return parts, await an.companions(conn, settings, user_id)


# ───────── شروع حمله ─────────
async def _check_attacker(conn, settings, ch):
    if ch["location"] == "dead":
        raise GameError("☠️ مرده‌ای.")
    if ch["location"] == "captive":
        raise GameError("⛓ در اسارتی.")
    if ch["location"] != "home":
        raise GameError("برای حمله باید در خانه باشی (نه در فعالیت یا استراحت).")
    if ch["energy"] < int(settings.get("combat.attack_energy")):
        raise GameError("انرژی کافی برای حمله نداری.")
    if ch["health"] < 15:
        raise GameError("برای حمله خیلی زخمی هستی.")


async def start_attack(conn, settings, attacker_id: int, target_id: int, chat_id, message_id, rng: random.Random = None):
    rng = rng or random.Random()
    if attacker_id == target_id:
        raise GameError("به خودت نمی‌توانی حمله کنی.")
    a_ch = await get_char(conn, attacker_id)
    t_ch = await get_char(conn, target_id)
    if a_ch is None or t_ch is None:
        raise GameError("هدف شخصیت ندارد.")
    await _check_attacker(conn, settings, a_ch)
    au = await conn.fetchrow("SELECT * FROM users WHERE id=$1 FOR UPDATE", attacker_id)
    tu = await conn.fetchrow("SELECT * FROM users WHERE id=$1", target_id)
    if tu["is_banned"]:
        raise GameError("این بازیکن در دسترس نیست.")
    if t_ch["location"] in ("dead", "captive"):
        raise GameError("هدف در حال حاضر قابل حمله نیست (مرده یا اسیر).")
    if tu["protected_until"] and tu["protected_until"] > now():
        raise GameError("🛡 این بازیکن تازه‌وارد است و هنوز محافظت اولیه دارد.")
    if tu["captive_immune_until"] and tu["captive_immune_until"] > now():
        raise GameError("این بازیکن تازه از اسارت آزاد شده و موقتاً مصون است.")
    cd = int(settings.get("combat.repeat_cooldown_min"))
    if await conn.fetchval("SELECT 1 FROM combats WHERE attacker_user_id=$1 AND defender_user_id=$2 "
                           "AND created_at > now() - make_interval(mins => $3)", attacker_id, target_id, cd):
        raise GameError(f"به همین هدف تا {cd} دقیقه بعد از حمله قبلی نمی‌توانی دوباره حمله کنی.")
    if await conn.fetchval("SELECT 1 FROM combats WHERE state <> 'closed' AND (attacker_user_id=ANY($1::bigint[]) "
                           "OR defender_user_id=ANY($1::bigint[]))", [attacker_id, target_id]):
        raise GameError("یکی از دو طرف در نبرد دیگری است.")
    notice = ""
    if au["protected_until"] and au["protected_until"] > now():
        await conn.execute("UPDATE users SET protected_until=NULL WHERE id=$1", attacker_id)
        notice = "⚠️ با حمله کردن، محافظت اولیه‌ات پایان یافت.\n"

    me, st, _ = await _player_part(conn, settings, attacker_id)
    comp_parts, comp_raw = await _companions(conn, settings, attacker_id)
    att = [me] + comp_parts
    home_flow = t_ch["location"] in ("home", "resting", "tribe_base")
    t_stats = await player_stats(conn, settings, target_id, t_ch)
    mods = {"att_type": None, "ambush": None, "recon_extra": False, "alerted": False, "def_joined": False,
            "trap": 0.0, "trap_seen": False, "def_base": 1.0, "looter": None, "drop_id": None, "raid_steps": 0,
            "result": "", "notice": notice, "att_stealth": st["stealth"], "recon": []}
    if home_flow:
        dp = await homes.defense_participants(conn, settings, target_id)
        dfn = dp["parts"] + [{"k": "struct", "id": 0, "name": "دفاع خانه", "hp": 1, "max": 1, "power": 0, "def": 0}]
        hs = dp["stats"]
        mods["trap"], mods["def_base"] = hs["trap"], 1 + hs["defense"] / 100.0
        alert = an.perk_total(await an.guards_at_home(conn, settings, target_id), "alert")
        mods["def_perception"] = t_stats["perception"] + hs["alarm"] + alert
        track = an.perk_total(comp_raw, "track")
        perc = max(0, int(st["perception"] + track - hs["conceal"]))
        mods["recon"] = rules.recon_report(dp["info"], perc, st["power"], rng)
        mods["recon_perc"] = perc
        mods["recon_info"] = dp["info"]
        state, flow = "recon", "home"
    else:
        tparts, traw = await _companions(conn, settings, target_id)
        tp, _, _ = await _player_part(conn, settings, target_id)
        dfn = [tp] + tparts
        mods["def_perception"] = t_stats["perception"] + an.perk_total(traw, "alert")
        state, flow = "choose", "open"
    cid = await conn.fetchval(
        "INSERT INTO combats(chat_id, message_id, flow, attacker_user_id, defender_user_id, state, sides, mods) "
        "VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb) RETURNING id",
        chat_id, message_id, flow, attacker_id, target_id, state,
        json.dumps({"att": att, "def": dfn}, ensure_ascii=False), json.dumps(mods, ensure_ascii=False))
    c = await get(conn, cid)
    await _save(conn, c)
    await _arm_timer(conn, settings, c, int(settings.get("combat.round_timeout_sec")) * 2)
    return c


async def start_tribe_attack(conn, settings, attacker_id: int, tribe_id: int, chat_id=None, message_id=None,
                             rng: random.Random = None):
    rng = rng or random.Random()
    a_ch = await get_char(conn, attacker_id)
    await _check_attacker(conn, settings, a_ch)
    mine = await conn.fetchval("SELECT tribe_id FROM tribe_members WHERE user_id=$1", attacker_id)
    if mine == tribe_id:
        raise GameError("به قبیله خودت نمی‌توانی حمله کنی.")
    if mine and await conn.fetchval("SELECT 1 FROM tribe_relations WHERE kind='ally' AND status='active' AND "
                                    "((tribe_a=$1 AND tribe_b=$2) OR (tribe_a=$2 AND tribe_b=$1))", mine, tribe_id):
        raise GameError("این قبیله هم‌پیمان توست.")
    if await conn.fetchval("SELECT 1 FROM combats WHERE state <> 'closed' AND (attacker_user_id=$1 OR defender_tribe_id=$2)",
                           attacker_id, tribe_id):
        raise GameError("یکی از دو طرف در نبرد است.")
    me, st, _ = await _player_part(conn, settings, attacker_id)
    comp_parts, comp_raw = await _companions(conn, settings, attacker_id)
    defenders = await hr.tribe_defenders(conn, tribe_id)
    from app.services import tribes as tr
    ts = await tr.tribe_stats(conn, tribe_id)
    dfn = [d["part"] for d in defenders] + [{"k": "struct", "id": 0, "name": "دفاع پایگاه", "hp": 1, "max": 1, "power": 0, "def": 0}]
    info = {"guards": [{"name": d["part"]["name"], "gear": d["gear"]} for d in defenders], "dogs": [], "traps": 0,
            "defense_power": sum(d["part"]["power"] for d in defenders) * (1 + ts["defense"] / 100.0) + ts["defense"]}
    mods = {"att_type": None, "ambush": None, "recon_extra": False, "alerted": False, "def_joined": False, "trap": 0.0,
            "trap_seen": False, "def_base": 1 + ts["defense"] / 100.0, "looter": None, "drop_id": None, "raid_steps": 0,
            "result": "", "notice": "", "att_stealth": st["stealth"], "def_perception": 3 + ts["alarm"], "recon_info": info}
    perc = max(0, int(st["perception"] + an.perk_total(comp_raw, "track")))
    mods["recon_perc"] = perc
    mods["recon"] = rules.recon_report(info, perc, st["power"], rng)
    cid = await conn.fetchval(
        "INSERT INTO combats(chat_id, message_id, flow, attacker_user_id, defender_tribe_id, state, sides, mods) "
        "VALUES($1,$2,'tribe',$3,$4,'recon',$5::jsonb,$6::jsonb) RETURNING id",
        chat_id, message_id, attacker_id, tribe_id,
        json.dumps({"att": [me] + comp_parts, "def": dfn}, ensure_ascii=False), json.dumps(mods, ensure_ascii=False))
    c = await get(conn, cid)
    await _save(conn, c)
    await _arm_timer(conn, settings, c, int(settings.get("combat.round_timeout_sec")) * 2)
    return c


# ───────── کمک‌های وضعیت ─────────
def _def_user(c):
    return c["defender_user_id"]


def _notify_targets(c):
    """کاربرانی که باید خبردار شوند: مدافع انسانی (یا اعضای قبیله هدف)."""
    return [c["defender_user_id"]] if c["defender_user_id"] else []


async def _tribe_members(conn, tribe_id: int):
    return [r["user_id"] for r in await conn.fetch("SELECT user_id FROM tribe_members WHERE tribe_id=$1", tribe_id)]


async def _inform_defenders(conn, c, text: str):
    ids = _notify_targets(c)
    if c["defender_tribe_id"]:
        ids = await _tribe_members(conn, c["defender_tribe_id"])
    for uid in ids:
        await notify(conn, uid, "combat", text)


# ───────── اکشن‌ها ─────────
async def cancel(conn, settings, c, user_id: int):
    if user_id != c["attacker_user_id"] or c["state"] not in ("recon", "choose"):
        raise GameError("اجازه نداری.")
    c["mods"]["result"] = "حمله لغو شد."
    await _save(conn, c, state="closed", outcome="cancelled", ended_at=now())


async def recon_more(conn, settings, c, user_id: int, rng):
    if user_id != c["attacker_user_id"] or c["state"] != "recon":
        raise GameError("اجازه نداری.")
    if c["mods"]["recon_extra"]:
        raise GameError("قبلاً بررسی بیشتر انجام داده‌ای.")
    cost = int(settings.get("combat.recon_extra_energy"))
    ch = await get_char(conn, user_id)
    if ch["energy"] < cost:
        raise GameError("انرژی کافی نداری.")
    await conn.execute("UPDATE characters SET energy=energy-$2 WHERE user_id=$1", user_id, cost)
    m = c["mods"]
    m["recon_extra"] = True
    st = await player_stats(conn, settings, user_id, ch)
    if not rules.stealth_check(st["stealth"], m["def_perception"], 0, rng):
        m["alerted"] = True
    me = c["sides"]["att"][0]
    m["recon"] = rules.recon_report(m["recon_info"], m["recon_perc"], me["power"], rng, extra=True)
    if m["alerted"]:
        m["recon"].append("🚨 حین بررسی دیده شدی؛ مدافعان آماده‌ترند.")
    await _save(conn, c)
    await _arm_timer(conn, settings, c, int(settings.get("combat.round_timeout_sec")) * 2)


async def confirm_start(conn, settings, c, user_id: int):
    if user_id != c["attacker_user_id"] or c["state"] != "recon":
        raise GameError("اجازه نداری.")
    await _save(conn, c, state="choose")
    await _arm_timer(conn, settings, c)


async def choose_type(conn, settings, c, user_id: int, atype: str, rng):
    if user_id != c["attacker_user_id"] or c["state"] != "choose" or atype not in ATTACK_FA:
        raise GameError("اجازه نداری.")
    if atype == "siege" and len(_alive(c["sides"]["att"])) < 2:
        raise GameError("برای محاصره حداقل ۲ نفر (با همراه) لازم است.")
    cost = int(settings.get("combat.attack_energy"))
    ch = await get_char(conn, user_id)
    if ch["energy"] < cost:
        raise GameError("انرژی کافی نداری.")
    await conn.execute("UPDATE characters SET energy=energy-$2 WHERE user_id=$1", user_id, cost)
    m = c["mods"]
    m["att_type"] = atype
    if atype == "ambush":
        m["ambush"] = "ok" if rules.stealth_check(m["att_stealth"], m["def_perception"], 0, rng) else "fail"
    c["log"].append(f"⚔️ {ATTACK_FA[atype]}" + (" (غافلگیری موفق!)" if m["ambush"] == "ok" else
                                               " (لو رفتی!)" if m["ambush"] == "fail" else ""))
    await _inform_defenders(conn, c, f"🚨 به تو یا خانه‌ات حمله شد ({ATTACK_FA[atype]})! "
                                     "اگر آنلاین هستی، در پیام نبرد در گروه وارد شو یا فرار کن.")
    await _round(conn, settings, c, rng)


async def join(conn, settings, c, user_id: int, rng):
    if c["flow"] != "home" or user_id != c["defender_user_id"] or c["mods"]["def_joined"]:
        raise GameError("اجازه نداری.")
    if c["state"] not in ("choose", "round") or c["round_no"] > 2:
        raise GameError("زمان ورود به نبرد گذشته است.")
    me, _, ch = await _player_part(conn, settings, user_id)
    if ch["location"] not in ("home", "resting") or ch["health"] <= 0:
        raise GameError("نمی‌توانی وارد نبرد شوی.")
    parts, _ = await _companions(conn, settings, user_id)
    have = {(p["k"], p["id"]) for p in c["sides"]["def"]}
    c["sides"]["def"] += [me] + [p for p in parts if (p["k"], p["id"]) not in have]
    c["mods"]["def_joined"] = True
    c["log"].append(f"🛡 {me['name']} وارد نبرد شد")
    await _save(conn, c)


async def continue_round(conn, settings, c, user_id: int, rng, auto: bool = False):
    if c["state"] != "round" or (user_id != c["attacker_user_id"] and not auto):
        raise GameError("اجازه نداری.")
    await _round(conn, settings, c, rng)


async def _round(conn, settings, c, rng, bonus_att: float = 1.0, bonus_def: float = 1.0):
    s, m = c["sides"], c["mods"]
    n = c["round_no"] + 1
    att_mult, def_mult, trap = bonus_att, m["def_base"] * bonus_def, 0.0
    if n == 1:
        if m["ambush"] == "ok":
            att_mult *= float(settings.get("combat.ambush_att_mult"))
        elif m["ambush"] == "fail":
            def_mult *= float(settings.get("combat.ambush_fail_def_mult"))
        if m["alerted"]:
            def_mult *= 1.1
        trap = float(m["trap"]) * (0.6 if m["trap_seen"] else 1.0)
    if m["att_type"] == "pursue":
        att_mult *= float(settings.get("combat.pursue_att_mult"))
    res = rules.resolve_round(s["att"], s["def"], att_mult, def_mult, _params(settings), rng, trap)
    lines = [f"— دور {fa_num(n)} — (قدرت مهاجم {fa_num(res['ap'])} | مدافع {fa_num(res['dp'])})"]
    if trap and n == 1:
        lines.append(f"🪤 تله‌ها فعال شدند (−{fa_num(int(trap))})")
    lines += res["lines"]
    loss = int(settings.get("combat.durability_loss"))
    for p in s["att"] + s["def"]:
        if p["k"] == "player" and p["hp"] > 0:
            lines += [f"🔧 {b} شکست" for b in await inv.damage_equipment(conn, settings, p["id"], loss)]
    c["log"] += lines
    c["round_no"] = n
    if rules.attacker_down(s["att"]):
        return await _finish(conn, settings, c, "att_defeated", rng)
    if rules.defender_down(s["def"], c["flow"]):
        return await _finish(conn, settings, c, "def_defeated", rng)
    await _save(conn, c, state="round", round_no=n)
    await _arm_timer(conn, settings, c)


async def flee(conn, settings, c, user_id: int, rng):
    if c["state"] != "round":
        raise GameError("الان نمی‌توانی فرار کنی.")
    s, m = c["sides"], c["mods"]
    if user_id == c["attacker_user_id"]:
        mine, other, side = s["att"], s["def"], "att"
    elif user_id == c["defender_user_id"]:
        mine, other, side = s["def"], s["att"], "def"
    else:
        raise GameError("اجازه نداری.")
    me = next((p for p in mine if p["k"] == "player" and p["id"] == user_id and p["hp"] > 0), None)
    if me is None:
        raise GameError("در این نبرد حضور فعال نداری.")
    mp, op = rules.side_power(mine), rules.side_power(other)
    pen = 0.0
    if side == "def":
        if m["att_type"] == "siege":
            pen += float(settings.get("combat.siege_escape_penalty"))
        if m["att_type"] == "pursue":
            pen += float(settings.get("combat.siege_escape_penalty")) / 2
    chance = rules.escape_chance(float(settings.get("combat.escape_base")), me["end"], me["hp"] / me["max"], pen,
                                 op / (mp + op + 1e-9))
    if rng.random() < chance:
        c["log"].append(f"🏃 {me['name']} با موفقیت فرار کرد")
        return await _finish(conn, settings, c, "fled_att" if side == "att" else "fled_def", rng)
    c["log"].append(f"❌ تلاش {me['name']} برای فرار ناکام ماند و طرف مقابل ضربه زد")
    if side == "att":
        await _round(conn, settings, c, rng, bonus_att=0.6, bonus_def=1.25)
    else:
        await _round(conn, settings, c, rng, bonus_att=1.25, bonus_def=0.6)


# ───────── پایان نبرد ─────────
async def _ensure_drop(conn, settings, c) -> int:
    if not c["mods"]["drop_id"]:
        exp = now() + timedelta(hours=int(settings.get("drop.expire_hours")))
        c["mods"]["drop_id"] = await conn.fetchval("INSERT INTO drops(kind, expires_at) VALUES('combat',$1) RETURNING id", exp)
    return c["mods"]["drop_id"]


async def _fate(conn, settings, c, victim: int, victor: int, victor_parts: list, rng):
    u = await conn.fetchrow("SELECT captive_immune_until FROM users WHERE id=$1", victim)
    can_hold = any(p["k"] in ("animal", "hireling") and p["hp"] > 0 for p in victor_parts) or \
        any(p["k"] == "player" and p["hp"] > 0 for p in victor_parts)
    chance = float(settings.get("combat.capture_chance"))
    if any(p.get("stance") == "capture" and p["hp"] > 0 for p in victor_parts):
        chance = min(0.95, chance * 1.3)
    immune = u["captive_immune_until"] and u["captive_immune_until"] > now()
    if victor and can_hold and not immune and rng.random() < chance:
        await captivity.capture(conn, settings, victim, victor)
        return "captured"
    drop = await death.kill(conn, settings, victim, "combat")
    c["mods"]["drop_id"] = drop
    return "death"


async def _finish(conn, settings, c, outcome: str, rng):
    s, m = c["sides"], c["mods"]
    result_lines = [OUTCOME_FA.get(outcome, outcome)]
    victim_state = None
    # ۱) ذخیره سلامتی شرکت‌کنندگان
    drop_for_hireling = None
    for p in s["att"] + s["def"]:
        if p["k"] == "animal":
            await an.apply_result(conn, p)
        elif p["k"] == "hireling":
            if p["hp"] <= 0 and not drop_for_hireling:
                drop_for_hireling = await _ensure_drop(conn, settings, c)
            await hr.apply_result(conn, p, drop_for_hireling)
        elif p["k"] == "player" and p["hp"] > 0:
            await conn.execute("UPDATE characters SET health=$2 WHERE user_id=$1", p["id"], p["hp"])
    att_id, def_id = c["attacker_user_id"], c["defender_user_id"]
    me_att = s["att"][0]["name"]
    me_def = next((p["name"] for p in s["def"] if p["k"] == "player"), None)
    looter = None
    # ۲) نتیجه
    if outcome == "att_defeated":
        victor = def_id
        victim_state = await _fate(conn, settings, c, att_id, victor, s["def"], rng)
        result_lines.append(f"{me_att} {'اسیر شد ⛓' if victim_state == 'captured' else 'کشته شد ☠️'}")
        if victim_state == "death" and victor:
            looter = "def"
    elif outcome == "def_defeated":
        victor = att_id
        pl = next((p for p in s["def"] if p["k"] == "player"), None)
        if pl is not None and pl["hp"] <= 0:
            victim_state = await _fate(conn, settings, c, pl["id"], victor, s["att"], rng)
            result_lines.append(f"{pl['name']} {'اسیر شد ⛓' if victim_state == 'captured' else 'کشته شد ☠️'}")
        else:
            result_lines.append("دفاع شکسته شد 🏚")
        looter = "att"
        from app.services import events as ev
        await ev.season_score(conn, victor, 10)
    elif outcome in ("fled_att", "fled_def"):
        who = att_id if outcome == "fled_att" else def_id
        if who:
            await conn.execute("UPDATE characters SET fatigue=LEAST(100,fatigue+$2) WHERE user_id=$1", who,
                               int(settings.get("combat.flee_fatigue")))
    m["result"] = "\n".join(result_lines)
    c["log"].append("🏁 " + " | ".join(result_lines))
    # ۳) غارت یا بستن
    drop_has = False
    if m["drop_id"]:
        drop_has = bool(await conn.fetchval(
            "SELECT (SELECT count(*) FROM item_stacks WHERE holder_type='ground' AND holder_id=$1) + "
            "(SELECT count(*) FROM item_instances WHERE holder_type='ground' AND holder_id=$1)", m["drop_id"]))
    can_raid = looter == "att" and c["flow"] in ("home", "tribe")
    if looter and (drop_has or can_raid):
        m["looter"] = looter
        await _save(conn, c, state="loot", outcome=outcome, victor="att" if looter == "att" else "def")
        await _arm_timer(conn, settings, c, int(settings.get("combat.loot_minutes")) * 60)
    else:
        await _save(conn, c, state="closed", outcome=outcome, ended_at=now(),
                    victor="att" if outcome == "def_defeated" else "def" if outcome == "att_defeated" else "none")
    await _inform_defenders(conn, c, "📜 نتیجه نبرد: " + "؛ ".join(result_lines))
    await notify(conn, att_id, "combat", "📜 نتیجه نبرد: " + "؛ ".join(result_lines))


# ───────── غنیمت ─────────
def _looter_id(c):
    return c["attacker_user_id"] if c["mods"]["looter"] == "att" else c["defender_user_id"]


async def loot_take(conn, settings, c, user_id: int):
    if c["state"] != "loot" or user_id != _looter_id(c) or not c["mods"]["drop_id"]:
        raise GameError("غنیمتی برای برداشتن نیست.")
    taken = await inv.pickup_from_ground(conn, settings, user_id, await get_char(conn, user_id), c["mods"]["drop_id"])
    c["log"].append("🎒 برداشتی: " + ("، ".join(f"{n} ×{q}" for n, q in taken) if taken else "چیزی جا نشد"))
    await _save(conn, c)
    return taken


async def loot_raid(conn, settings, c, user_id: int, rng):
    m = c["mods"]
    if c["state"] != "loot" or m["looter"] != "att" or user_id != c["attacker_user_id"] or c["flow"] not in ("home", "tribe"):
        raise GameError("غارت ممکن نیست.")
    steps = int(settings.get("loot.steps"))
    if m["raid_steps"] >= steps:
        raise GameError("مراحل غارت تمام شده است.")
    alive = len(_alive(c["sides"]["def"]))
    chance = max(0.1, float(settings.get("loot.base_success")) - float(settings.get("loot.step_penalty")) * m["raid_steps"]
                 - 0.04 * alive)
    m["raid_steps"] += 1
    if rng.random() > chance:
        c["log"].append("🚨 آژیر به صدا درآمد؛ مجبور شدی عقب‌نشینی کنی")
        await _inform_defenders(conn, c, "🛡 یک غارت ناکام ماند.")
        await _save(conn, c, state="closed", ended_at=now())
        return []
    ch = await get_char(conn, user_id)
    frac = float(settings.get("loot.max_storage_fraction"))
    k = int(settings.get("loot.step_items"))
    if c["flow"] == "home":
        src_t, src_id = "storage", c["defender_user_id"]
    else:
        src_t, src_id = "tribe", c["defender_tribe_id"]
    cands = []
    for st in await conn.fetch(
            "SELECT s.def_id, s.qty, d.name, d.weight FROM item_stacks s JOIN item_defs d ON d.id=s.def_id "
            "WHERE s.holder_type=$1 AND s.holder_id=$2 AND d.lootable", src_t, src_id):
        cands.append(("s", st))
    for it in await conn.fetch(
            "SELECT i.id, i.def_id, d.name, d.weight FROM item_instances i JOIN item_defs d ON d.id=i.def_id "
            "WHERE i.holder_type=$1 AND i.holder_id=$2 AND d.lootable AND NOT i.bound AND i.owner_user_id IS NOT DISTINCT FROM i.owner_user_id",
            src_t if src_t != "storage" else "storage", src_id):
        cands.append(("i", it))
    rng.shuffle(cands)
    free = await inv.free_weight(conn, settings, user_id, ch)
    taken = []
    for kind, row in cands[:k]:
        w = float(row["weight"])
        if kind == "s":
            want = max(1, int(row["qty"] * frac))
            n = inv.fit_qty(w, free, want)
            if n > 0:
                await inv.move_stack(conn, src_t, src_id, "backpack", user_id, row["def_id"], n, "raid")
                free -= n * w
                taken.append((row["name"], n))
        elif inv.fit_qty(w, free, 1) >= 1:
            await inv.move_instance(conn, row["id"], src_t, src_id, "backpack", user_id, "raid")
            await conn.execute("UPDATE item_instances SET owner_user_id=$2 WHERE id=$1", row["id"], user_id)
            free -= w
            taken.append((row["name"], 1))
    coins = 0
    if c["flow"] == "tribe":
        have = await conn.fetchval("SELECT coins FROM tribes WHERE id=$1 FOR UPDATE", src_id)
        coins = int((have or 0) * float(settings.get("loot.tribe_coin_fraction")))
        if coins > 0:
            from app.services.economy import change_coins
            await conn.execute("UPDATE tribes SET coins=coins-$2 WHERE id=$1", src_id, coins)
            await conn.execute("INSERT INTO tribe_ledger(tribe_id, user_id, kind, coins, note) VALUES($1,$2,'raided',$3,'raid')",
                               src_id, user_id, -coins)
            await change_coins(conn, user_id, coins, "raid_loot", ref=str(src_id))
    desc = "، ".join(f"{n} ×{q}" for n, q in taken) + (f" و {coins} 🪙" if coins else "")
    c["log"].append(f"🏚 مرحله {fa_num(m['raid_steps'])} غارت: " + (desc or "چیزی جا نشد"))
    if taken or coins:
        await _inform_defenders(conn, c, "🏚 خانه یا پایگاه شما غارت شد: " + desc)
    if m["raid_steps"] >= steps:
        await _save(conn, c, state="closed", ended_at=now())
    else:
        await _save(conn, c)
    return taken


async def loot_finish(conn, settings, c, user_id: int):
    if c["state"] != "loot" or user_id != _looter_id(c):
        raise GameError("اجازه نداری.")
    await _save(conn, c, state="closed", ended_at=now())


# ───────── مهلت‌ها ─────────
async def on_timeout(conn, settings, cid: int, version: int, rng: random.Random = None) -> bool:
    """True یعنی چیزی تغییر کرد (برای به‌روزرسانی پیام)."""
    rng = rng or random.Random()
    r = await conn.fetchrow("SELECT version FROM combats WHERE id=$1", cid)
    if r is None or r["version"] != version:
        return False
    c = await get(conn, cid)
    if c["state"] in ("recon", "choose"):
        c["mods"]["result"] = "مهلت تمام شد؛ حمله لغو شد."
        await _save(conn, c, state="closed", outcome="timeout", ended_at=now())
    elif c["state"] == "round":
        await _round(conn, settings, c, rng)           # حرکت پیش‌فرض: ادامه حمله
    elif c["state"] == "loot":
        await _save(conn, c, state="closed", ended_at=now())
    else:
        return False
    return True


# ───────── نمایش ─────────
def _hp_line(p) -> str:
    icon = {"player": "🧍", "animal": "🐕", "hireling": "🛡"}.get(p["k"], "•")
    return f"{icon} {p['name']} {bar(p['hp'], p.get('max') or 100, 6)} {fa_num(p['hp'])}"


def describe(c) -> tuple:
    """(متن, دکمه‌ها[(برچسب, اکشن)]) — برای پیام گروه."""
    s, m = c["sides"], c["mods"]
    head = {"open": "⚔️ <b>درگیری</b>", "home": "🏚 <b>حمله به خانه</b>", "tribe": "🏰 <b>حمله به پایگاه قبیله</b>"}[c["flow"]]
    lines = [head]
    if m.get("notice"):
        lines.append(m["notice"].strip())
    if c["state"] == "recon":
        lines += ["", "🔎 <b>شناسایی</b>"] + m["recon"] + ["", "برای شروع، تأیید نهایی لازم است."]
        return "\n".join(lines), [("⚔️ تأیید و ادامه", "start"), ("🔍 بررسی بیشتر", "recon"), ("❌ انصراف", "cancel")]
    lines.append("")
    lines.append("<b>مهاجمان</b>")
    lines += [_hp_line(p) for p in s["att"] if p["k"] != "struct"]
    lines.append("<b>مدافعان</b>")
    dl = [_hp_line(p) for p in s["def"] if p["k"] != "struct"]
    lines += dl or ["(بدون مدافع فعال)"]
    if c["state"] == "choose":
        lines += ["", "نوع حمله را انتخاب کن:"]
        return "\n".join(lines), [("⚔️ مستقیم", "direct"), ("🥷 غافلگیرانه", "ambush"), ("🐾 تعقیب", "pursue"),
                                  ("🕸 محاصره", "siege"), ("❌ انصراف", "cancel")]
    if c["log"]:
        lines += ["", "<b>گزارش</b>"] + c["log"][-8:]
    if c["state"] == "round":
        btns = [("➡️ ادامه حمله", "continue"), ("🏃 فرار", "flee")]
        if c["flow"] == "home" and not m["def_joined"] and c["round_no"] <= 2:
            btns.append(("🛡 ورود مدافع", "join"))
        return "\n".join(lines), btns
    if c["state"] == "loot":
        btns = [("🎒 برداشت غنیمت", "take")]
        if m["looter"] == "att" and c["flow"] in ("home", "tribe") and m["raid_steps"] < 99:
            btns.append(("🏚 غارت مرحله‌ای", "raid"))
        btns.append(("✅ پایان", "finish"))
        return "\n".join(lines), btns
    lines += ["", "🏁 " + (m["result"] or OUTCOME_FA.get(c["outcome"] or "", ""))]
    return "\n".join(lines), []


async def perform(conn, settings, cid: int, version: int, user_id: int, act: str, role: str = "", rng=None):
    """نقطه ورود دکمه‌های نبرد. role برای دکمه‌های هم‌نام: a (مهاجم) یا d (مدافع)."""
    rng = rng or random.Random()
    c = await get(conn, cid)
    if c["version"] != version:
        raise GameError("این پیام قدیمی شده؛ نسخه جدید را ببین.")
    if act == "cancel":
        await cancel(conn, settings, c, user_id)
    elif act == "recon":
        await recon_more(conn, settings, c, user_id, rng)
    elif act == "start":
        await confirm_start(conn, settings, c, user_id)
    elif act in ATTACK_FA:
        await choose_type(conn, settings, c, user_id, act, rng)
    elif act == "join":
        await join(conn, settings, c, user_id, rng)
    elif act == "continue":
        await continue_round(conn, settings, c, user_id, rng)
    elif act == "flee":
        await flee(conn, settings, c, user_id, rng)
    elif act == "take":
        await loot_take(conn, settings, c, user_id)
    elif act == "raid":
        await loot_raid(conn, settings, c, user_id, rng)
    elif act == "finish":
        await loot_finish(conn, settings, c, user_id)
    else:
        raise GameError("اکشن نامعتبر.")
    return await get(conn, cid, lock=False)
