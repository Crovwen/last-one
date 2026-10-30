"""اسارت: باج، مذاکره، کار آزادی، فرار، نجات؛ همیشه با سقف زمانی."""
from __future__ import annotations

import json
import random
from datetime import timedelta

from app.services import homes
from app.services import inventory as inv
from app.services.chars import get_char, jload, now, player_stats
from app.services.economy import InsufficientFunds, change_coins
from app.services.errors import GameError
from app.services.notify import notify
from app.services.rules import clamp
from app.services.sched import schedule


async def active_for(conn, user_id: int):
    return await conn.fetchrow("SELECT * FROM captivities WHERE captive_id=$1 AND status='active'", user_id)


async def held_by(conn, user_id: int):
    return await conn.fetch(
        "SELECT c.*, ch.name AS captive_name FROM captivities c JOIN characters ch ON ch.user_id=c.captive_id "
        "WHERE c.captor_id=$1 AND c.status='active' ORDER BY c.id", user_id)


async def capture(conn, settings, captive_id: int, captor_id: int, party: list = None) -> int:
    ends = now() + timedelta(hours=int(settings.get("captivity.max_hours")))
    party = [int(x) for x in (party or []) if int(x) != captor_id]
    cid = await conn.fetchval(
        "INSERT INTO captivities(captive_id, captor_id, ends_at, party) VALUES($1,$2,$3,$4::jsonb) RETURNING id",
        captive_id, captor_id, ends, json.dumps(party))
    await conn.execute("UPDATE characters SET location='captive', health=GREATEST(health, 10), rest_until=NULL WHERE user_id=$1",
                       captive_id)
    await conn.execute("UPDATE activities SET status='done', result='{\"cancelled\": true}'::jsonb WHERE user_id=$1 AND status='running'",
                       captive_id)
    await schedule("captivity_end", {"captivity_id": cid}, ends)
    names = await conn.fetchrow("SELECT (SELECT name FROM characters WHERE user_id=$1) AS a, (SELECT name FROM characters WHERE user_id=$2) AS b",
                                captive_id, captor_id)
    await notify(conn, captive_id, "captured",
                 f"⛓ توسط {names['b']} اسیر شدی. حداکثر {settings.get('captivity.max_hours')} ساعت. "
                 "از منو می‌توانی مذاکره، باج، فرار یا انجام کار آزادی را انتخاب کنی.")
    await notify(conn, captor_id, "capture", f"⛓ {names['a']} اسیر تو شد. از بخش اسرا می‌توانی تعیین باج/آزادی کنی.")
    return cid


async def _end(conn, settings, cap, reason: str):
    await conn.execute("UPDATE captivities SET status='ended', end_reason=$2 WHERE id=$1", cap["id"], reason)
    until = now() + timedelta(hours=int(settings.get("captivity.immunity_hours")))
    await conn.execute("UPDATE characters SET location='home' WHERE user_id=$1 AND location='captive'", cap["captive_id"])
    await conn.execute("UPDATE users SET captive_immune_until=$2 WHERE id=$1", cap["captive_id"], until)
    fa = {"timeout": "مدت اسارت تمام شد", "ransom": "باج پرداخت شد", "released": "آزاد شدی", "task": "کار آزادی انجام شد",
          "escaped": "از اسارت فرار کردی", "rescued": "اعضای قبیله نجاتت دادند"}.get(reason, "آزاد شدی")
    await notify(conn, cap["captive_id"], "freed", f"🔓 {fa}. تا {settings.get('captivity.immunity_hours')} ساعت اسیر نمی‌شوی.")
    await notify(conn, cap["captor_id"], "freed_captor", f"🔓 اسیرت آزاد شد ({fa}).")


async def settle(conn, settings, user_id: int) -> list:
    rows = await conn.fetch(
        "SELECT * FROM captivities WHERE status='active' AND ends_at <= now() AND (captive_id=$1 OR captor_id=$1) FOR UPDATE", user_id)
    for r in rows:
        await _end(conn, settings, r, "timeout")
    return []


async def get_cap(conn, cap_id: int, lock: bool = True):
    cap = await conn.fetchrow("SELECT * FROM captivities WHERE id=$1 AND status='active'" + (" FOR UPDATE" if lock else ""), cap_id)
    if cap is None:
        raise GameError("این اسارت دیگر فعال نیست.")
    return cap


async def confiscate(conn, settings, captor_id: int, cap_id: int):
    cap = await get_cap(conn, cap_id)
    if cap["captor_id"] != captor_id:
        raise GameError("اجازه نداری.")
    exp = now() + timedelta(hours=int(settings.get("drop.expire_hours")))
    drop_id = await conn.fetchval("INSERT INTO drops(kind, victim_user_id, expires_at) VALUES('combat',$1,$2) RETURNING id",
                                  cap["captive_id"], exp)
    await inv.drop_carried(conn, cap["captive_id"], drop_id, "confiscate")
    taken = await inv.pickup_from_ground(conn, settings, captor_id, await get_char(conn, captor_id), drop_id)
    left = await conn.fetchval("SELECT (SELECT count(*) FROM item_stacks WHERE holder_type='ground' AND holder_id=$1) + "
                               "(SELECT count(*) FROM item_instances WHERE holder_type='ground' AND holder_id=$1)", drop_id)
    if left:   # آنچه جا نشد به اسیر برمی‌گردد (مصادره ناقص)
        for st in await conn.fetch("SELECT def_id, qty FROM item_stacks WHERE holder_type='ground' AND holder_id=$1", drop_id):
            await inv.move_stack(conn, "ground", drop_id, "backpack", cap["captive_id"], st["def_id"], st["qty"], "confiscate_return")
        for it in await conn.fetch("SELECT id FROM item_instances WHERE holder_type='ground' AND holder_id=$1", drop_id):
            await inv.move_instance(conn, it["id"], "ground", drop_id, "backpack", cap["captive_id"], "confiscate_return")
    await conn.execute("DELETE FROM drops WHERE id=$1", drop_id)
    await notify(conn, cap["captive_id"], "confiscated", "🧳 وسایل همراهت مصادره شد (لباس پایه و انبار خانه حفظ شد).")
    return taken


async def set_ransom(conn, settings, captor_id: int, cap_id: int, amount: int):
    cap = await get_cap(conn, cap_id)
    if cap["captor_id"] != captor_id:
        raise GameError("اجازه نداری.")
    amount = int(clamp(amount, 0, int(settings.get("captivity.ransom_max"))))
    await conn.execute("UPDATE captivities SET ransom=$2 WHERE id=$1", cap_id, amount)
    if amount:
        await notify(conn, cap["captive_id"], "ransom", f"💰 باج آزادی تو: {amount} 🪙")
    return amount


async def set_task(conn, settings, captor_id: int, cap_id: int, idx: int):
    cap = await get_cap(conn, cap_id)
    if cap["captor_id"] != captor_id:
        raise GameError("اجازه نداری.")
    presets = settings.get("captivity.task_presets")
    code, qty = presets[idx]
    d = await inv.def_by_code(conn, code)
    task = {"item": code, "qty": int(qty), "name": d["name"]}
    await conn.execute("UPDATE captivities SET task=$2::jsonb WHERE id=$1", cap_id, json.dumps(task, ensure_ascii=False))
    await notify(conn, cap["captive_id"], "task", f"📋 برای آزادی باید {qty} {d['name']} تحویل بدهی.")
    return task


async def _pay_captors(conn, cap, amount: int, reason: str):
    """باج بین سرگروه و هم‌دستان به‌طور مساوی تقسیم می‌شود (باقیمانده برای سرگروه)."""
    members = [cap["captor_id"]] + [int(x) for x in jload(cap["party"])]
    share = amount // len(members)
    for uid in members[1:]:
        if share:
            await change_coins(conn, uid, share, reason, ref=str(cap["id"]))
    await change_coins(conn, members[0], amount - share * (len(members) - 1), reason, ref=str(cap["id"]))


async def split_items(conn, settings, victim_id: int, members: list):
    """هرچه قربانی همراه داشته (به‌جز لباس پایه) بین اسارت‌گیرها به‌نوبت و بر اساس ارزش تقسیم می‌شود.
    آنچه در ظرفیت کسی جا نشود به خود قربانی برمی‌گردد."""
    exp = now() + timedelta(hours=int(settings.get("drop.expire_hours")))
    drop_id = await conn.fetchval("INSERT INTO drops(kind, victim_user_id, expires_at) VALUES('combat',$1,$2) RETURNING id",
                                  victim_id, exp)
    await inv.drop_carried(conn, victim_id, drop_id, "hostage_split")
    entries = []
    for st in await conn.fetch("SELECT s.def_id, s.qty, d.weight, d.value FROM item_stacks s JOIN item_defs d ON d.id=s.def_id "
                               "WHERE s.holder_type='ground' AND s.holder_id=$1", drop_id):
        entries.append(("s", st))
    for it in await conn.fetch("SELECT i.id, i.def_id, d.weight, d.value FROM item_instances i JOIN item_defs d ON d.id=i.def_id "
                               "WHERE i.holder_type='ground' AND i.holder_id=$1", drop_id):
        entries.append(("i", it))
    entries.sort(key=lambda e: -e[1]["value"])
    free = {u: await inv.free_weight(conn, settings, u, await get_char(conn, u)) for u in members}
    turn = 0
    for kind, row in entries:
        w = float(row["weight"])
        if kind == "s":
            left_qty = row["qty"]
            for i in range(len(members)):         # تعداد را نوبتی بین اعضا پخش می‌کنیم
                if left_qty <= 0:
                    break
                u = members[(turn + i) % len(members)]
                share = max(1, row["qty"] // len(members)) if i < len(members) - 1 else left_qty
                n = inv.fit_qty(w, free[u], min(share, left_qty))
                if n > 0:
                    await inv.move_stack(conn, "ground", drop_id, "backpack", u, row["def_id"], n, "hostage_split")
                    free[u] -= n * w
                    left_qty -= n
        else:
            for i in range(len(members)):
                u = members[(turn + i) % len(members)]
                if inv.fit_qty(w, free[u], 1) >= 1:
                    await inv.move_instance(conn, row["id"], "ground", drop_id, "backpack", u, "hostage_split")
                    await conn.execute("UPDATE item_instances SET owner_user_id=$2 WHERE id=$1", row["id"], u)
                    free[u] -= w
                    break
        turn += 1
    for st in await conn.fetch("SELECT def_id, qty FROM item_stacks WHERE holder_type='ground' AND holder_id=$1", drop_id):
        await inv.move_stack(conn, "ground", drop_id, "backpack", victim_id, st["def_id"], st["qty"], "hostage_return")
    for it in await conn.fetch("SELECT id FROM item_instances WHERE holder_type='ground' AND holder_id=$1", drop_id):
        await inv.move_instance(conn, it["id"], "ground", drop_id, "backpack", victim_id, "hostage_return")
    await conn.execute("DELETE FROM drops WHERE id=$1", drop_id)


async def pay_ransom(conn, settings, captive_id: int, cap_id: int):
    cap = await get_cap(conn, cap_id)
    if cap["captive_id"] != captive_id or cap["ransom"] <= 0:
        raise GameError("باجی تعیین نشده.")
    try:
        await change_coins(conn, captive_id, -cap["ransom"], "ransom_paid", ref=str(cap_id))
    except InsufficientFunds as exc:
        raise GameError("سکه کافی نداری.") from exc
    await _pay_captors(conn, cap, cap["ransom"], "ransom_received")
    await _end(conn, settings, cap, "ransom")


async def offer(conn, captive_id: int, cap_id: int, amount: int):
    cap = await get_cap(conn, cap_id)
    if cap["captive_id"] != captive_id:
        raise GameError("اجازه نداری.")
    await conn.execute("UPDATE captivities SET offer=$2 WHERE id=$1", cap_id, max(0, amount))
    await notify(conn, cap["captor_id"], "offer", f"🤝 اسیرت {amount} 🪙 برای آزادی پیشنهاد داد.")


async def accept_offer(conn, settings, captor_id: int, cap_id: int):
    cap = await get_cap(conn, cap_id)
    if cap["captor_id"] != captor_id or cap["offer"] <= 0:
        raise GameError("پیشنهادی وجود ندارد.")
    try:
        await change_coins(conn, cap["captive_id"], -cap["offer"], "ransom_paid", ref=str(cap_id))
    except InsufficientFunds as exc:
        raise GameError("اسیر دیگر این مقدار سکه ندارد.") from exc
    await _pay_captors(conn, cap, cap["offer"], "ransom_received")
    await _end(conn, settings, cap, "ransom")


async def release(conn, settings, captor_id: int, cap_id: int):
    cap = await get_cap(conn, cap_id)
    if cap["captor_id"] != captor_id:
        raise GameError("اجازه نداری.")
    await _end(conn, settings, cap, "released")


async def fulfill_task(conn, settings, captive_id: int, cap_id: int):
    cap = await get_cap(conn, cap_id)
    if cap["captive_id"] != captive_id or not cap["task"]:
        raise GameError("کاری تعیین نشده.")
    task = jload(cap["task"])
    d = await inv.def_by_code(conn, task["item"])
    need = int(task["qty"])
    hb = await inv.stack_qty(conn, "backpack", captive_id, d["id"])
    hs = await inv.stack_qty(conn, "storage", captive_id, d["id"])
    if hb + hs < need:
        raise GameError(f"{d['name']} کافی نداری (نیاز: {need}).")
    tb = min(hb, need)
    if tb:
        await inv.move_stack(conn, "backpack", captive_id, "storage", cap["captor_id"], d["id"], tb, "captivity_task")
    if need - tb:
        await inv.move_stack(conn, "storage", captive_id, "storage", cap["captor_id"], d["id"], need - tb, "captivity_task")
    await _end(conn, settings, cap, "task")


async def escape(conn, settings, captive_id: int, cap_id: int, rng: random.Random):
    cap = await get_cap(conn, cap_id)
    if cap["captive_id"] != captive_id:
        raise GameError("اجازه نداری.")
    cd = int(settings.get("captivity.escape_cooldown_min"))
    if cap["last_escape_at"] and now() - cap["last_escape_at"] < timedelta(minutes=cd):
        raise GameError(f"هر {cd} دقیقه یک‌بار می‌توانی تلاش کنی.")
    me = await player_stats(conn, settings, captive_id)
    dfn = (await homes.defense_participants(conn, settings, cap["captor_id"]))["info"]["defense_power"]
    chance = clamp(0.2 + me["stealth"] * 0.02 + me["endurance"] * 0.01 - dfn / (dfn + me["power"] + 10) * 0.3, 0.05, 0.8)
    await conn.execute("UPDATE captivities SET last_escape_at=now() WHERE id=$1", cap_id)
    if rng.random() < chance:
        await _end(conn, settings, cap, "escaped")
        return True
    await conn.execute("UPDATE characters SET fatigue=LEAST(100,fatigue+10), health=GREATEST(1, health-5) WHERE user_id=$1", captive_id)
    return False


async def rescue(conn, settings, rescuer_id: int, cap_id: int, rng: random.Random):
    cap = await get_cap(conn, cap_id)
    ra = await conn.fetchval("SELECT tribe_id FROM tribe_members WHERE user_id=$1", rescuer_id)
    rb = await conn.fetchval("SELECT tribe_id FROM tribe_members WHERE user_id=$1", cap["captive_id"])
    if not ra or ra != rb:
        raise GameError("فقط اعضای هم‌قبیله می‌توانند اسیر را نجات دهند.")
    ch = await get_char(conn, rescuer_id)
    if ch["location"] != "home":
        raise GameError("باید در خانه باشی.")
    cost = 10
    if ch["energy"] < cost:
        raise GameError("انرژی کافی نداری.")
    await conn.execute("UPDATE characters SET energy=energy-$2 WHERE user_id=$1", rescuer_id, cost)
    me = await player_stats(conn, settings, rescuer_id, ch)
    dfn = (await homes.defense_participants(conn, settings, cap["captor_id"]))["info"]["defense_power"]
    chance = clamp(float(settings.get("rescue.base_chance")) + (me["power"] - dfn) / (me["power"] + dfn + 1) * 0.4, 0.05, 0.85)
    if rng.random() < chance:
        await _end(conn, settings, cap, "rescued")
        return True
    await conn.execute("UPDATE characters SET health=GREATEST(1, health-$2) WHERE user_id=$1", rescuer_id, rng.randint(5, 15))
    await notify(conn, cap["captor_id"], "rescue_failed", "🛡 یک عملیات نجات اسیرت ناکام ماند.")
    return False
