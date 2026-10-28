"""جنگل (فعالیت، استراحت، مأموریت)، حیوانات، نیروها."""
from __future__ import annotations

from app.bot.screens_core import a_inv, main_screen  # noqa: F401
from app.bot.ui import B, Screen, action, esc, fa_num, left
from app.services import animals as an
from app.services import events, hirelings, rules, world
from app.services import inventory as inv
from app.services.chars import jload, skills_of
from app.services.errors import GameError
from app.services.settings import SKILLS

ZONE_FA = {"dense": "🌳 جنگل انبوه", "river": "🌊 رودخانه", "mountain": "⛰ کوهستان", "cave": "🕳 غار",
           "ruins": "🏚 مناطق متروکه", "hunting": "🏹 محل شکار"}


# ───────── جنگل ─────────
@action("fo")
async def a_forest(ctx, conn, user, ch, x, y, note=""):
    cur = await world.current_activity(conn, user["id"])
    if cur:
        return Screen(f"🌲 <b>در حال انجام: {esc(cur['name'])}</b>\nپایان: {left(cur['ends_at'])} دیگر\n\n"
                      "لازم نیست آنلاین بمانی؛ بعداً نتیجه را می‌گیری.", [[B("🔄 بروزرسانی", "fo")]], note=note)
    if ch["location"] == "resting":
        cost = int(ctx.settings.get("rest.fast_cost_per_hour"))
        return Screen(f"😴 در حال استراحت هستی؛ {left(ch['rest_until'])} دیگر.",
                      [[B("⚡️ استراحت سریع (با سکه)", "frest")], [B("🔄 بروزرسانی", "fo")]], note=note)
    sk = skills_of(ch)
    env = rules.environment(__import__("datetime").datetime.now(__import__("datetime").timezone.utc), ctx.settings.get)
    lines = [f"🌲 <b>جنگل</b>   {'🌙 شب' if env['night'] else '☀️ روز'} — {rules.WEATHER_FA[env['weather']]}",
             f"رفتار: {world.STANCE_FA[ch['stance']]}   ⚡️ انرژی: {fa_num(ch['energy'])}   (شب و هوای بد انرژی و ریسک را بیشتر می‌کند)", ""]
    rows, zone = [], None
    for a in await world.list_activities(conn):
        if a["zone"] != zone:
            zone = a["zone"]
            lines.append(f"\n<b>{ZONE_FA.get(zone, zone)}</b>")
        ok = sk.get(a["skill"], 1) >= a["min_skill"]
        lines.append(f"• {esc(a['name'])} — {fa_num(a['duration_min'])} دقیقه، انرژی {fa_num(a['energy_cost'])}"
                     + ("" if ok else f" 🔒 {SKILLS.get(a['skill'], '')} {fa_num(a['min_skill'])}"))
        if ok:
            rows.append([B(a["name"], "go", a["code"])])
    rows.append([B("😴 استراحت", "fo_rest"), B("🎯 مأموریت", "ms")])
    return Screen("\n".join(lines), rows, note=note)


@action("go")
async def a_go(ctx, conn, user, ch, x, y):
    msg, ends = await world.start_activity(conn, ctx.settings, user["id"], ch, x)
    return Screen(f"🌲 {esc(msg)}.\nنتیجه را بعداً می‌گیری؛ لازم نیست آنلاین بمانی.", [[B("🔄 وضعیت", "fo")]])


@action("fo_rest")
async def a_rest_menu(ctx, conn, user, ch, x, y):
    mx = int(ctx.settings.get("rest.max_hours"))
    return Screen("😴 <b>استراحت</b>\nانرژی و خستگی بازیابی می‌شود. بعدش لازم نیست آنلاین باشی.",
                  [[B(f"{fa_num(h)} ساعت", "rest", h) for h in (1, 2, 4, mx) if h <= mx]], back="fo")


@action("rest")
async def a_rest(ctx, conn, user, ch, x, y):
    msg = await world.start_rest(conn, ctx.settings, user["id"], ch, int(x))
    return Screen(msg, [[B("⚡️ استراحت سریع", "frest")]], back="main")


@action("frest")
async def a_frest(ctx, conn, user, ch, x, y):
    msg = await world.fast_rest(conn, ctx.settings, user["id"], ch)
    return Screen(msg, [], back="main")


# ───────── مأموریت ─────────
@action("ms")
async def a_mission(ctx, conn, user, ch, x, y, note=""):
    m = await events.active_mission(conn, user["id"])
    if m is None:
        return Screen("🎯 <b>مأموریت</b>\nمأموریت فعالی نداری.", [[B("🎲 دریافت مأموریت", "ms_new")]], back="fo", note=note)
    text = (f"🎯 <b>{esc(m['name'])}</b>\n{esc(m['description'])}\nپیشرفت: {fa_num(m['progress'])}/{fa_num(m['amount'])}\n"
            f"پاداش: {fa_num(m['reward_coins'])} 🪙   مهلت: {left(m['expires_at'])}")
    rows = [[B("📦 تحویل", "ms_dl")]] if m["kind"] == "deliver" else []
    return Screen(text, rows, back="fo", note=note)


@action("ms_new")
async def a_ms_new(ctx, conn, user, ch, x, y):
    name = await events.new_mission(conn, ctx.settings, user["id"])
    return await a_mission(ctx, conn, user, ch, "", "", note=f"مأموریت «{name}» دریافت شد.")


@action("ms_dl")
async def a_ms_dl(ctx, conn, user, ch, x, y):
    msgs = await events.deliver_mission(conn, ctx.settings, user["id"])
    return Screen("\n".join(msgs), [], back="fo")


# ───────── حیوانات ─────────
@action("an")
async def a_animals(ctx, conn, user, ch, x, y, note=""):
    items = await an.list_animals(conn, user["id"])
    cap = await an.capacity_left(conn, ctx.settings, user["id"])
    lines = [f"🐺 <b>حیوانات</b>   جای خالی: {fa_num(max(cap, 0))}", ""]
    rows = []
    for a in items:
        lines.append(f"• {esc(a['name'])} (سطح {fa_num(a['level'])}) — {an.ROLE_FA[a['role']]} — ❤️ {fa_num(a['hp'])}/{fa_num(a['max_hp'])}")
        rows.append([B(f"{a['name']}", "ani", a["id"])])
    if not items:
        lines.append("هنوز حیوانی نداری.")
    rows.append([B("🛒 خرید حیوان", "anb")])
    return Screen("\n".join(lines), rows, note=note)


@action("ani")
async def a_animal(ctx, conn, user, ch, x, y, note=""):
    a = next((i for i in await an.list_animals(conn, user["id"]) if i["id"] == int(x)), None)
    if a is None:
        raise GameError("حیوان پیدا نشد.")
    perks = jload(a["perks"])
    fa_perk = {"guard": "نگهبانی", "alert": "هشدار", "track": "ردیابی", "forage": "جست‌وجوی منابع", "hunt": "کمک شکار",
               "carry": "حمل بار", "speed": "سرعت", "scout": "اکتشاف"}
    pk = "، ".join(f"{fa_perk[k]} {fa_num(v)}" for k, v in perks.items() if k in fa_perk) or "—"
    train = f"\n🎯 در حال تمرین تا {left(a['train_until'])}" if a["train_until"] and a["train_until"] > __import__("datetime").datetime.now(__import__("datetime").timezone.utc) else ""
    text = (f"<b>{esc(a['name'])}</b> — {esc(a['breed'])}\nسطح {fa_num(a['level'])}   ❤️ {fa_num(a['hp'])}/{fa_num(a['max_hp'])}   "
            f"⚔️ {fa_num(a['power'])}   💪 {fa_num(a['endurance'])}   🤝 وفاداری {fa_num(a['loyalty'])}\n"
            f"ویژگی‌ها: {pk}\nوظیفه: <b>{an.ROLE_FA[a['role']]}</b>{train}")
    rows = [[B(("✅ " if a["role"] == r else "") + fa, "anr", a["id"], r) for r, fa in an.ROLE_FA.items()],
            [B("🍖 غذا دادن", "anf", a["id"]), B("🎯 تمرین", "ant", a["id"])]]
    return Screen(text, rows, back="an", note=note)


@action("anr")
async def a_an_role(ctx, conn, user, ch, x, y):
    await an.set_role(conn, ctx.settings, user["id"], int(x), y)
    return await a_animal(ctx, conn, user, ch, x, "", note="✅ وظیفه تغییر کرد.")


@action("anf")
async def a_an_feed(ctx, conn, user, ch, x, y):
    return await a_animal(ctx, conn, user, ch, x, "", note=await an.feed(conn, user["id"], int(x)))


@action("ant")
async def a_an_train(ctx, conn, user, ch, x, y):
    return await a_animal(ctx, conn, user, ch, x, "", note=await an.train(conn, ctx.settings, user["id"], ch, int(x)))


@action("anb")
async def a_an_buy(ctx, conn, user, ch, x, y):
    lines, rows = ["🛒 <b>خرید حیوان</b>", ""], []
    for b in await conn.fetch("SELECT * FROM animal_breeds WHERE is_active AND price > 0 ORDER BY price"):
        lines.append(f"• {esc(b['name'])} — {fa_num(b['price'])} 🪙 — {esc(b['description'])}")
        rows.append([B(f"{b['name']} ({fa_num(b['price'])}🪙)", "anbuy", b["code"])])
    return Screen("\n".join(lines), rows, back="mk")


@action("anbuy")
async def a_an_buy_do(ctx, conn, user, ch, x, y):
    msg = await an.buy(conn, ctx.settings, user["id"], x)
    return await a_animals(ctx, conn, user, ch, "", "", note=msg)


# ───────── نیروها ─────────
@action("hr")
async def a_hirelings(ctx, conn, user, ch, x, y, note=""):
    items = await hirelings.list_hirelings(conn, user["id"])
    left_slots = await hirelings.slots_left(conn, ctx.settings, user["id"])
    lines = [f"🛡 <b>نیروها</b>   جای خالی: {fa_num(max(left_slots, 0))}", ""]
    rows = []
    for h in items:
        st = "" if h["status"] == "active" else " ⚠️ دستمزد پرداخت‌نشده"
        lines.append(f"• {esc(h['name'])} — {hirelings.POST_FA[h['post']]} — ❤️ {fa_num(h['hp'])}/{fa_num(h['max_hp'])}{st}")
        rows.append([B(h["name"] + f" #{h['id']}", "hri", h["id"])])
    if not items:
        lines.append("هنوز نیرویی استخدام نکرده‌ای.")
    rows.append([B("➕ استخدام نیرو", "hrb")])
    return Screen("\n".join(lines), rows, note=note)


@action("hrb")
async def a_hire_list(ctx, conn, user, ch, x, y):
    lines, rows = ["🛡 <b>استخدام نیرو</b>", f"جای خالی: {fa_num(max(await hirelings.slots_left(conn, ctx.settings, user['id']), 0))}", ""], []
    for d in await conn.fetch("SELECT * FROM hireling_defs WHERE is_active ORDER BY price"):
        lines.append(f"• {esc(d['name'])}: قدرت {fa_num(d['power'])}، سلامتی {fa_num(d['hp'])}، دستمزد روزانه {fa_num(d['wage'])} — {fa_num(d['price'])} 🪙")
        rows.append([B(f"{d['name']} ({fa_num(d['price'])}🪙)", "hrh", d["code"])])
    return Screen("\n".join(lines), rows, back="mk")


@action("hrh")
async def a_hire(ctx, conn, user, ch, x, y):
    msg = await hirelings.hire(conn, ctx.settings, user["id"], x)
    return await a_hirelings(ctx, conn, user, ch, "", "", note=msg)


@action("hri")
async def a_hireling(ctx, conn, user, ch, x, y, note=""):
    h = next((i for i in await hirelings.list_hirelings(conn, user["id"]) if i["id"] == int(x)), None)
    if h is None:
        raise GameError("نیرو پیدا نشد.")
    gear = await hirelings.gear_of(conn, h["id"])
    gl = "، ".join(f"{g['name']} ({fa_num(g['durability'])})" for g in gear) or "ندارد"
    text = (f"<b>{esc(h['name'])}</b> #{h['id']}\nسطح {fa_num(h['level'])}   ❤️ {fa_num(h['hp'])}/{fa_num(h['max_hp'])}   ⚔️ {fa_num(h['power'])}\n"
            f"دستمزد روزانه: {fa_num(h['wage'])} 🪙   وضعیت: {'فعال' if h['status'] == 'active' else '⚠️ پرداخت‌نشده'}\n"
            f"استقرار: <b>{hirelings.POST_FA[h['post']]}</b>   دستور: <b>{hirelings.STANCE_FA[h['stance']]}</b>\nتجهیزات: {esc(gl)}")
    rows = [[B(("✅ " if h["post"] == p else "") + fa, "hrp", h["id"], p)] for p, fa in hirelings.POST_FA.items()]
    rows = [[b[0] for b in rows[i:i + 2]] for i in range(0, len(rows), 2)]
    rows.append([B(("✅ " if h["stance"] == s else "") + fa, "hrs", h["id"], s) for s, fa in hirelings.STANCE_FA.items()])
    rows.append([B("🗡 تجهیز از انبار", "hge", h["id"])] + ([B("↩️ پس‌گرفتن تجهیز", "hgu", h["id"])] if gear else []))
    rows.append([B("🚪 اخراج", "hrd", h["id"])])
    return Screen(text, rows, back="hr", note=note)


@action("hrp")
async def a_hr_post(ctx, conn, user, ch, x, y):
    if y == "tribe_defense":
        from app.services import tribes
        await tribes.set_defense_post(conn, ctx.settings, user["id"], int(x), True)
    else:
        await hirelings.set_post(conn, ctx.settings, user["id"], int(x), y)
    return await a_hireling(ctx, conn, user, ch, x, "", note="✅ استقرار تغییر کرد.")


@action("hrs")
async def a_hr_stance(ctx, conn, user, ch, x, y):
    await hirelings.set_stance(conn, user["id"], int(x), y)
    return await a_hireling(ctx, conn, user, ch, x, "", note="✅ دستور تغییر کرد.")


@action("hge")
async def a_hr_gear_list(ctx, conn, user, ch, x, y):
    rows = []
    for ht in ("storage", "backpack"):
        for i in await inv.instances_of(conn, ht, user["id"]):
            if i["kind"] in hirelings.GEAR_SLOTS and not i["bound"] and i["owner_user_id"] == user["id"]:
                rows.append([B(f"{i['name']} ({'انبار' if ht == 'storage' else 'کوله'})", "hgi", x, str(i["id"]))])
    if not rows:
        raise GameError("وسیله مناسبی (سلاح/زره/سپر) در انبار یا کوله نداری.")
    return Screen("🗡 کدام وسیله را به نیرو بدهم؟ (تا وقتی دست نیروست، خودت نمی‌توانی از آن استفاده کنی)", rows[:12], back="hri")


@action("hgsel")
async def a_hr_gear_pick(ctx, conn, user, ch, x, y):
    items = await hirelings.list_hirelings(conn, user["id"])
    if not items:
        raise GameError("نیرویی نداری.")
    return Screen("وسیله را به کدام نیرو بدهم؟", [[B(f"{h['name']} #{h['id']}", "hgi", h["id"], x)] for h in items], back="inv")


@action("hgi")
async def a_hr_gear_give(ctx, conn, user, ch, x, y):
    await hirelings.equip(conn, user["id"], int(x), y)
    return await a_hireling(ctx, conn, user, ch, x, "", note="✅ تجهیز به نیرو داده شد.")


@action("hgu")
async def a_hr_gear_take_list(ctx, conn, user, ch, x, y):
    gear = await hirelings.gear_of(conn, int(x))
    return Screen("↩️ کدام وسیله را پس بگیرم؟", [[B(g["name"], "hgt", x, str(g["id"]))] for g in gear], back="hri")


@action("hgt")
async def a_hr_gear_take(ctx, conn, user, ch, x, y):
    await hirelings.unequip(conn, user["id"], int(x), y)
    return await a_hireling(ctx, conn, user, ch, x, "", note="✅ تجهیز به انبار برگشت.")


@action("hrd")
async def a_hr_dismiss(ctx, conn, user, ch, x, y):
    await hirelings.dismiss(conn, user["id"], int(x))
    return await a_hirelings(ctx, conn, user, ch, "", "", note="🚪 نیرو اخراج شد و تجهیزاتش به انبارت برگشت.")
