"""منوی اصلی، شخصیت، کوله‌پشتی/انبار، جنگل، خانه، ساخت وسایل."""
from __future__ import annotations

import json

from app.bot import texts_fa as t
from app.bot.ui import (B, LOC_FA, Screen, action, ask, esc, fa_num, left, parse_int, text_input, clear_state)
from app.services import animals as an
from app.services import captivity, death, events, homes, hirelings, market, world
from app.services import inventory as inv
from app.services.chars import is_protected, jload, player_stats, skills_of
from app.services.errors import GameError
from app.services.settings import SKILLS


# ───────── منوی اصلی ─────────
async def main_screen(ctx, conn, user, ch, note: str = "") -> Screen:
    cap = await captivity.active_for(conn, user["id"])
    held = await captivity.held_by(conn, user["id"])
    head = f"🌲 <b>LAST ONE</b> — {esc(ch['name'])}\n{LOC_FA.get(ch['location'], ch['location'])}   🪙 {fa_num(user['coins'])}\n"
    if is_protected(user):
        head += f"🛡 محافظ تازه‌وارد فعال است ({left(user['protected_until'])} دیگر)\n"
    if ch["location"] == "dead":
        cost = ctx.settings.get("death.fast_respawn_cost")
        return Screen(head + "\n☠️ کشته شدی. به‌زودی در خانه برمی‌گردی.\n" + (f"بازگشت عادی: {left(ch['dead_until'])}" if ch["dead_until"] else ""),
                      [[B(f"⚡️ بازگشت سریع ({fa_num(cost)} 🪙)", "frespawn")], [B("👤 شخصیت", "pf")]], back=None, note=note)
    if ch["location"] == "captive" and cap:
        return Screen(head + "\n⛓ در اسارتی؛ از منوی اسارت می‌توانی تصمیم بگیری.",
                      [[B("⛓ وضعیت اسارت", "cp")], [B("👤 شخصیت", "pf"), B("📜 گزارش‌ها", "rp")]], back=None, note=note)
    m = await events.active_mission(conn, user["id"])
    if m:
        head += f"🎯 مأموریت: {esc(m['name'])} ({fa_num(m['progress'])}/{fa_num(m['amount'])})\n"
    rows = [[B("👤 شخصیت", "pf"), B("🎒 کوله‌پشتی", "inv")], [B("🌲 جنگل", "fo"), B("🏠 خانه", "ho")],
            [B("🐺 حیوانات", "an"), B("🛡 نیروها", "hr")], [B("🏕 قبیله", "tb"), B("🛒 مارکت", "mk")],
            [B("📜 گزارش‌ها", "rp"), B("⚙️ تنظیمات", "st")], [B("📖 راهنما", "help")]]
    if held:
        rows.append([B(f"⛓ اسرای من ({fa_num(len(held))})", "cap_l")])
    return Screen(head, rows, back=None, note=note)


@action("main")
async def a_main(ctx, conn, user, ch, x, y):
    await clear_state(conn, user["id"]) if user["state"] and user["state"].startswith("in:") else None
    return await main_screen(ctx, conn, user, ch)


@action("cancel_in")
async def a_cancel_in(ctx, conn, user, ch, x, y):
    await clear_state(conn, user["id"])
    return await main_screen(ctx, conn, user, ch)


@action("frespawn")
async def a_frespawn(ctx, conn, user, ch, x, y):
    msg = await death.fast_respawn(conn, ctx.settings, user["id"], ch)
    user = await conn.fetchrow("SELECT * FROM users WHERE id=$1", user["id"])
    ch = await conn.fetchrow("SELECT * FROM characters WHERE user_id=$1", user["id"])
    return await main_screen(ctx, conn, user, ch, msg)


# ───────── شخصیت ─────────
@action("pf")
async def a_profile(ctx, conn, user, ch, x, y):
    st = await player_stats(conn, ctx.settings, user["id"], ch)
    text = t.profile_text(user, ch)
    text += f"\n\n⚔️ قدرت مبارزه: {fa_num(int(st['power']))}   🛡 دفاع: {fa_num(int(st['defense']))}"
    text += f"\n🧭 وضعیت: {LOC_FA.get(ch['location'])}   رفتار: {world.STANCE_FA[ch['stance']]}"
    rows = [[B("🍖 مصرف", "inv_use"), B("😴 استراحت", "fo_rest")], [B("⚙️ حالت رفتار", "st")]]
    if ch["location"] == "home" and await death.last_drop(conn, user["id"]):
        rows.append([B("🧭 رفتن به محل مرگ", "recov", "n"),
                     B(f"⚡️ سریع ({fa_num(ctx.settings.get('death.return_fast_cost'))}🪙)", "recov", "f")])
    return Screen(text, rows)


@action("recov")
async def a_recov(ctx, conn, user, ch, x, y):
    msg = await death.start_recovery(conn, ctx.settings, user["id"], ch, x == "f")
    return Screen(msg, [], back="main")


@action("st")
async def a_settings(ctx, conn, user, ch, x, y):
    if x in world.STANCE_FA:
        await world.set_stance(conn, user["id"], x)
        ch = await conn.fetchrow("SELECT * FROM characters WHERE user_id=$1", user["id"])
    s = ctx.settings
    text = ("⚙️ <b>تنظیمات رفتار</b>\n\nقبل از هر فعالیت، رفتارت را انتخاب کن:\n"
            f"• محتاط: خطر کمتر و مخفی‌کاری بیشتر، اما انرژی بیشتر (×{fa_num(s.get('stance.cautious_energy_mult'))})\n"
            "• عادی: حالت متعادل\n"
            f"• سریع: زمان کوتاه‌تر (×{fa_num(s.get('stance.fast_time_mult'))}) ولی ریسک بیشتر\n\n"
            f"رفتار فعلی: <b>{world.STANCE_FA[ch['stance']]}</b>")
    rows = [[B(("✅ " if k == ch["stance"] else "") + v, "st", k) for k, v in world.STANCE_FA.items()],
            [B("🛡 محافظ تازه‌وارد", "prot"), B("📖 راهنما", "help")]]
    return Screen(text, rows)


# ───────── کوله‌پشتی و انبار ─────────
SLOT_KINDS = ("weapon", "armor", "shield", "bag", "clothing", "tool")
KIND_FA = {"weapon": "سلاح", "armor": "زره", "shield": "سپر", "bag": "کوله", "clothing": "لباس", "tool": "ابزار",
           "food": "خوراکی", "drink": "نوشیدنی", "medicine": "دارو", "resource": "منبع", "misc": "متفرقه"}


async def _carry_line(ctx, conn, user, ch) -> str:
    sk = skills_of(ch)
    lo = await inv.loadout(conn, user["id"])
    cap = inv.carry_capacity(sk["carry"], lo["bag_bonus"], ctx.settings.get)
    w = await inv.carried_weight(conn, user["id"])
    return f"⚖️ وزن: {fa_num(round(w, 1))} / {fa_num(round(cap, 1))}"


@action("inv")
async def a_inv(ctx, conn, user, ch, x, y, note=""):
    lo = await inv.loadout(conn, user["id"])
    lines = ["🎒 <b>کوله‌پشتی و تجهیزات</b>", await _carry_line(ctx, conn, user, ch), "", "<b>پوشیده‌ها</b>"]
    for it in lo["items"]:
        d = f" ({fa_num(it['durability'])}/{fa_num(it['max_durability'])})" if it["max_durability"] else ""
        lines.append(f"• {inv.SLOT_FA.get(it['slot'], '')}: {esc(it['name'])}{d}")
    if not lo["items"]:
        lines.append("— هیچ —")
    stacks = await inv.stacks_of(conn, "backpack", user["id"])
    insts = [i for i in await inv.instances_of(conn, "backpack", user["id"])]
    lines += ["", "<b>داخل کوله</b>"]
    lines += [f"• {esc(s['name'])} ×{fa_num(s['qty'])}" for s in stacks] or []
    lines += [f"• {esc(i['name'])}" for i in insts]
    if not stacks and not insts:
        lines.append("— خالی —")
    rows = [[B(f"{esc(s['name'])} ×{fa_num(s['qty'])}", "sk", s["def_id"])] for s in stacks[:10]]
    rows += [[B(f"🗡 {i['name']}", "io", str(i["id"]))] for i in insts[:10]]
    rows += [[B(f"✅ {it['name']}", "io", str(it["iid"]))] for it in lo["items"]]
    rows.append([B("📦 انبار خانه", "sto"), B("🔨 ساخت وسایل", "cr")])
    return Screen("\n".join(lines), rows, note=note)


@action("inv_use")
async def a_inv_use(ctx, conn, user, ch, x, y):
    stacks = [s for s in await inv.stacks_of(conn, "backpack", user["id"]) if s["kind"] in ("food", "drink", "medicine")]
    if not stacks:
        raise GameError("چیز قابل مصرفی در کوله‌پشتی نداری.")
    return Screen("🍖 چه چیزی مصرف کنی؟", [[B(f"{s['name']} ×{fa_num(s['qty'])}", "use", s["def_id"])] for s in stacks], back="pf")


@action("use")
async def a_use(ctx, conn, user, ch, x, y):
    name = await inv.consume(conn, user["id"], int(x))
    return await a_inv(ctx, conn, user, ch, "", "", note=f"✅ {name} مصرف شد.")


@action("sk")
async def a_stack(ctx, conn, user, ch, x, y):
    d = await inv.def_by_id(conn, int(x))
    qb = await inv.stack_qty(conn, "backpack", user["id"], d["id"])
    qs = await inv.stack_qty(conn, "storage", user["id"], d["id"])
    text = (f"<b>{esc(d['name'])}</b> ({KIND_FA.get(d['kind'], d['kind'])})\nکوله: {fa_num(qb)}   انبار: {fa_num(qs)}\n"
            f"وزن هر عدد: {fa_num(d['weight'])}   ارزش: {fa_num(d['value'])} 🪙")
    rows = []
    if d["kind"] in ("food", "drink", "medicine") and qb:
        rows.append([B("🍖 مصرف", "use", d["id"])])
    if qb:
        rows.append([B("📦 یکی به انبار", "dep", d["id"], "1"), B("📦 همه به انبار", "dep", d["id"], "a")])
    if qs:
        rows.append([B("🎒 یکی به کوله", "wd", d["id"], "1"), B("🎒 همه به کوله", "wd", d["id"], "a")])
    if d["tradable"] and (qb or qs):
        rows.append([B("💰 فروش سیستمی ×۱", "sells", d["id"], "1"), B("💰 فروش همه", "sells", d["id"], "a")])
        rows.append([B("📋 آگهی در بازار", "lst", d["id"]), B("🎁 هدیه", "gift", d["id"])])
    return Screen(text, rows, back="inv")


async def _qty(conn, ht, uid, def_id, y):
    have = await inv.stack_qty(conn, ht, uid, def_id)
    return have if y == "a" else min(int(y or 1), have)


@action("dep")
async def a_dep(ctx, conn, user, ch, x, y):
    n = await _qty(conn, "backpack", user["id"], int(x), y)
    if n <= 0:
        raise GameError("چیزی برای انتقال نیست.")
    await homes.deposit(conn, ctx.settings, user["id"], int(x), n)
    return await a_stack(ctx, conn, user, ch, x, "")


@action("wd")
async def a_wd(ctx, conn, user, ch, x, y):
    n = await _qty(conn, "storage", user["id"], int(x), y)
    if n <= 0:
        raise GameError("چیزی در انبار نیست.")
    await homes.withdraw(conn, ctx.settings, user["id"], ch, int(x), n)
    return await a_stack(ctx, conn, user, ch, x, "")


@action("sells")
async def a_sells(ctx, conn, user, ch, x, y):
    src = "backpack" if await inv.stack_qty(conn, "backpack", user["id"], int(x)) else "storage"
    n = await _qty(conn, src, user["id"], int(x), y)
    price = await market.sell_npc(conn, ctx.settings, user["id"], int(x), n)
    return Screen(f"💰 فروخته شد: +{fa_num(price)} 🪙", [], back="inv")


@action("io")
async def a_inst(ctx, conn, user, ch, x, y):
    r = await conn.fetchrow(
        "SELECT i.*, d.name, d.kind, d.power, d.defense, d.max_durability, d.value, d.tradable, d.weight FROM item_instances i "
        "JOIN item_defs d ON d.id=i.def_id WHERE i.id=$1::uuid", x)
    if r is None or r["holder_id"] != user["id"] or r["holder_type"] not in ("backpack", "storage", "equipped"):
        raise GameError("این وسیله در اختیار تو نیست.")
    dur = f"{fa_num(r['durability'])}/{fa_num(r['max_durability'])}" if r["max_durability"] else "—"
    text = (f"<b>{esc(r['name'])}</b> ({KIND_FA.get(r['kind'], r['kind'])})\nقدرت: {fa_num(r['power'])}  دفاع: {fa_num(r['defense'])}  "
            f"دوام: {dur}\nوزن: {fa_num(r['weight'])}  ارزش: {fa_num(r['value'])} 🪙" + ("\n🔒 اختصاصی (غیرقابل انتقال)" if r["bound"] else "")
            + (f"\nمحل: {inv_loc(r['holder_type'])}"))
    rows = []
    if r["holder_type"] == "equipped":
        rows.append([B("🧥 درآوردن", "ue", x)])
    else:
        if r["kind"] in SLOT_KINDS:
            rows.append([B("🧥 تجهیز", "eq", x)])
        rows.append([B("📦 به انبار" if r["holder_type"] == "backpack" else "🎒 به کوله", "idep" if r["holder_type"] == "backpack" else "iwd", x)])
        if r["max_durability"] and r["durability"] < r["max_durability"]:
            c = inv.repair_cost(r["value"], r["durability"], r["max_durability"])
            rows.append([B(f"🔧 تعمیر ({fa_num(c)} 🪙)", "rep", x, c)])
        if r["tradable"] and not r["bound"]:
            rows.append([B("💰 فروش سیستمی", "selli", x), B("📋 آگهی در بازار", "lst", r["def_id"], x)])
            rows.append([B("🎁 هدیه", "gift", r["def_id"], x)])
        if r["kind"] in hirelings.GEAR_SLOTS and not r["bound"]:
            rows.append([B("🛡 دادن به نیرو", "hgsel", x)])
    return Screen(text, rows, back="inv")


def inv_loc(ht: str) -> str:
    return {"backpack": "کوله‌پشتی", "storage": "انبار خانه", "equipped": "پوشیده"}.get(ht, ht)


@action("eq")
async def a_eq(ctx, conn, user, ch, x, y):
    await inv.equip(conn, user["id"], x)
    return await a_inv(ctx, conn, user, ch, "", "", note="✅ تجهیز شد.")


@action("ue")
async def a_ue(ctx, conn, user, ch, x, y):
    await inv.unequip(conn, user["id"], x)
    return await a_inv(ctx, conn, user, ch, "", "", note="✅ درآورده شد.")


@action("idep")
async def a_idep(ctx, conn, user, ch, x, y):
    r = await conn.fetchrow("SELECT def_id FROM item_instances WHERE id=$1::uuid", x)
    await homes.deposit(conn, ctx.settings, user["id"], r["def_id"], 1, iid=x)
    return await a_inv(ctx, conn, user, ch, "", "", note="📦 به انبار رفت.")


@action("iwd")
async def a_iwd(ctx, conn, user, ch, x, y):
    r = await conn.fetchrow("SELECT def_id FROM item_instances WHERE id=$1::uuid", x)
    await homes.withdraw(conn, ctx.settings, user["id"], ch, r["def_id"], 1, iid=x)
    return await a_inv(ctx, conn, user, ch, "", "", note="🎒 به کوله آمد.")


@action("rep")
async def a_rep(ctx, conn, user, ch, x, y):
    await inv.repair(conn, user["id"], x, int(y))
    return await a_inv(ctx, conn, user, ch, "", "", note="🔧 تعمیر شد.")


@action("selli")
async def a_selli(ctx, conn, user, ch, x, y):
    r = await conn.fetchrow("SELECT def_id FROM item_instances WHERE id=$1::uuid", x)
    price = await market.sell_npc(conn, ctx.settings, user["id"], r["def_id"], 1, iid=x)
    return Screen(f"💰 فروخته شد: +{fa_num(price)} 🪙", [], back="inv")


# ───────── انبار خانه ─────────
@action("sto")
async def a_storage(ctx, conn, user, ch, x, y):
    st = await homes.home_stats(conn, ctx.settings, user["id"])
    used = await homes.storage_used(conn, user["id"])
    stacks = await inv.stacks_of(conn, "storage", user["id"])
    insts = await inv.instances_of(conn, "storage", user["id"])
    lines = [f"📦 <b>انبار خانه</b>\nظرفیت: {fa_num(round(used, 1))} / {fa_num(round(st['storage'], 1))}", ""]
    lines += [f"• {esc(s['name'])} ×{fa_num(s['qty'])}" for s in stacks] + [f"• {esc(i['name'])}" for i in insts]
    if not stacks and not insts:
        lines.append("— خالی —")
    rows = [[B(f"{s['name']} ×{fa_num(s['qty'])}", "sk", s["def_id"])] for s in stacks[:10]]
    rows += [[B(f"🗡 {i['name']}", "io", str(i["id"]))] for i in insts[:10]]
    return Screen("\n".join(lines), rows, back="ho")


# ───────── ساخت وسایل ─────────
@action("cr")
async def a_craft(ctx, conn, user, ch, x, y):
    rows, lines = [], ["🔨 <b>ساخت وسایل</b>", "(مواد از انبار و کوله برداشته می‌شود)", ""]
    names = {r["code"]: r["name"] for r in await conn.fetch("SELECT code, name FROM item_defs")}
    for d in await market.craftable(conn):
        rec = jload(d["recipe"])
        need = "، ".join(f"{v} {names.get(k, k)}" for k, v in rec.get("inputs", {}).items())
        sk = f" (مهارت {SKILLS.get(rec['skill'], '')} {rec.get('min_skill', 1)})" if rec.get("skill") else ""
        lines.append(f"• {esc(d['name'])}: {need}{sk}")
        rows.append([B(f"🔨 {d['name']}", "mkit", d["code"])])
    return Screen("\n".join(lines), rows, back="inv")


@action("mkit")
async def a_mkit(ctx, conn, user, ch, x, y):
    name, qty, msgs = await market.craft(conn, ctx.settings, user["id"], ch, x, 1)
    return Screen("\n".join([f"✅ {esc(name)} ساخته شد و در کوله‌پشتی‌ات است."] + msgs), [[B("🔨 ساخت دیگر", "cr")]], back="inv")


# ───────── خانه ─────────
@action("ho")
async def a_home(ctx, conn, user, ch, x, y):
    st = await homes.home_stats(conn, ctx.settings, user["id"])
    used = await homes.storage_used(conn, user["id"])
    gl = await hirelings.slots_left(conn, ctx.settings, user["id"])
    kl = await an.capacity_left(conn, ctx.settings, user["id"])
    text = (f"🏠 <b>{esc(st['house'] or 'بدون خانه')}</b> (سطح {fa_num(st['house_level'])})\n"
            f"🛡 دفاع: {fa_num(int(st['defense']))}   🪤 تله: {fa_num(int(st['trap']))}   🚨 هشدار: {fa_num(int(st['alarm']))}   🫥 پنهان‌کاری: {fa_num(int(st['conceal']))}\n"
            f"📦 انبار: {fa_num(round(used, 1))} / {fa_num(round(st['storage'], 1))}\n"
            f"🛡 جای نیرو: {fa_num(st['guard_slots'])} (آزاد: {fa_num(gl)})   🐾 جای حیوان: {fa_num(st['kennel_slots'])} (آزاد: {fa_num(kl)})\n")
    for b in await homes.user_buildings(conn, "home", user["id"]):
        tag = f" — در حال ساخت (سطح {fa_num(b['target_level'])}) تا {left(b['ready_at'])}" if b["status"] == "building" else ""
        text += f"\n• {esc(b['name'])} سطح {fa_num(b['level'])}{tag}"
    return Screen(text, [[B("🏗 ساخت و ارتقا", "bl"), B("📦 انبار", "sto")], [B("🔨 ساخت وسایل", "cr")]])


@action("bl")
async def a_build_list(ctx, conn, user, ch, x, y, note=""):
    names = {r["code"]: r["name"] for r in await conn.fetch("SELECT code, name FROM item_defs")}
    have = {b["code"]: b for b in await homes.user_buildings(conn, "home", user["id"])}
    rows, lines = [], ["🏗 <b>ساخت و ارتقا</b>", ""]
    for d in await conn.fetch("SELECT * FROM building_defs WHERE scope='home' AND is_active ORDER BY kind, build_minutes"):
        b = have.get(d["code"])
        lvl = b["level"] if b else 0
        if lvl >= d["max_level"]:
            lines.append(f"• {esc(d['name'])}: حداکثر سطح ✅")
            continue
        c = homes.cost_at(jload(d["cost"]), d["cost_mult"], lvl + 1)
        lines.append(f"• {esc(d['name'])} (سطح {fa_num(lvl)}→{fa_num(lvl + 1)}): {homes.cost_text(c, names)}")
        rows.append([B(f"{'⬆️' if lvl else '🔨'} {d['name']}", "bld", d["code"])])
    return Screen("\n".join(lines), rows, back="ho", note=note)


@action("bld")
async def a_build(ctx, conn, user, ch, x, y):
    from app.services.chars import require_home
    require_home(ch, "ساخت‌وساز")
    st = await player_stats(conn, ctx.settings, user["id"], ch)
    msg, _ = await homes.start_build(conn, ctx.settings, user["id"], x, st["build_speed"])
    return await a_build_list(ctx, conn, user, ch, "", "", note=msg)
