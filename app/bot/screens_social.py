"""مارکت، معامله، انتقال، قبیله، اسارت، گزارش‌ها و فصل."""
from __future__ import annotations

from app.bot.screens_core import KIND_FA, main_screen  # noqa: F401
from app.bot.ui import B, Screen, action, ask, clear_state, esc, fa_num, left, parse_int, text_input
from app.services import captivity, combat, events, homes, market, tribes
from app.services import inventory as inv
from app.services.chars import jload
from app.services.errors import GameError


async def find_user(conn, name: str):
    r = await conn.fetchrow("SELECT user_id, name FROM characters WHERE lower(name)=lower($1)", " ".join(name.split()))
    if r is None:
        raise GameError("بازیکنی با این نام پیدا نشد.")
    return r


# ───────── مارکت ─────────
@action("mk")
async def a_market(ctx, conn, user, ch, x, y):
    return Screen("🛒 <b>مارکت</b>", [
        [B("🏪 فروشگاه سیستمی", "shop"), B("📋 بازار بازیکنان", "pm")],
        [B("🛡 استخدام نیرو", "hrb"), B("🐾 خرید حیوان", "anb")],
        [B("🤝 معامله امن", "tr"), B("💸 انتقال سکه", "xf")],
        [B("📦 آگهی‌های من", "pmy"), B("🔨 ساخت وسایل", "cr")]])


@action("shop")
async def a_shop(ctx, conn, user, ch, x, y, note=""):
    lines, rows = [f"🏪 <b>فروشگاه</b>   🪙 {fa_num(user['coins'])}", ""], []
    for d in await market.shop_items(conn):
        lines.append(f"• {esc(d['name'])} ({KIND_FA.get(d['kind'], d['kind'])}) — {fa_num(d['shop_price'])} 🪙")
        r = [B(f"{d['name']} ×۱", "shopb", d["code"], "1")]
        if d["stackable"]:
            r.append(B("×۵", "shopb", d["code"], "5"))
        rows.append(r)
    return Screen("\n".join(lines), rows, back="mk", note=note)


@action("shopb")
async def a_shop_buy(ctx, conn, user, ch, x, y):
    name, added, over = await market.buy_npc(conn, ctx.settings, user["id"], ch, x, int(y))
    user = await conn.fetchrow("SELECT * FROM users WHERE id=$1", user["id"])
    return await a_shop(ctx, conn, user, ch, "", "", note=f"✅ {name} خریداری شد" + (f" ({over} عدد به‌دلیل پربودن کوله در انبار)" if over else ""))


@action("pm")
async def a_player_market(ctx, conn, user, ch, x, y, note=""):
    off = int(x or 0)
    rows_db = await market.browse(conn, off, 8)
    lines = ["📋 <b>بازار بازیکنان</b>", ""]
    rows = []
    for l in rows_db:
        lines.append(f"• {esc(l['name'])} ×{fa_num(l['qty'])} — {fa_num(l['unit_price'])}🪙 هر عدد — {esc(l['seller'])}")
        if l["seller_id"] != user["id"]:
            r = [B(f"{l['name']} ×۱ ({fa_num(l['unit_price'])}🪙)", "pmb", l["id"], "1")]
            if l["qty"] > 1:
                r.append(B("همه", "pmb", l["id"], str(l["qty"])))
            rows.append(r)
    if not rows_db:
        lines.append("آگهی‌ای نیست.")
    nav = []
    if off > 0:
        nav.append(B("◀️", "pm", max(0, off - 8)))
    if len(rows_db) == 8:
        nav.append(B("▶️", "pm", off + 8))
    if nav:
        rows.append(nav)
    return Screen("\n".join(lines), rows, back="mk", note=note)


@action("pmb")
async def a_pm_buy(ctx, conn, user, ch, x, y):
    name, qty, total = await market.buy_listing(conn, ctx.settings, user["id"], ch, int(x), int(y))
    return await a_player_market(ctx, conn, user, ch, "", "", note=f"✅ {qty} عدد {name} خریدی ({fa_num(total)} 🪙)")


@action("pmy")
async def a_my_listings(ctx, conn, user, ch, x, y, note=""):
    ls = await market.my_listings(conn, user["id"])
    lines = ["📦 <b>آگهی‌های من</b>", ""] + [f"• {esc(l['name'])} ×{fa_num(l['qty'])} — {fa_num(l['unit_price'])}🪙" for l in ls]
    if not ls:
        lines.append("آگهی فعالی نداری. از کوله‌پشتی روی یک آیتم بزن «آگهی در بازار».")
    return Screen("\n".join(lines), [[B(f"❌ لغو {l['name']}", "pmc", l["id"])] for l in ls], back="mk", note=note)


@action("pmc")
async def a_pm_cancel(ctx, conn, user, ch, x, y):
    await market.cancel_listing(conn, user["id"], int(x))
    return await a_my_listings(ctx, conn, user, ch, "", "", note="آگهی لغو شد و وسیله به انبارت برگشت.")


@action("lst")
async def a_list_start(ctx, conn, user, ch, x, y):
    d = await inv.def_by_id(conn, int(x))
    lo, hi = market.price_range(d["value"], ctx.settings)
    if y:
        return await ask(conn, user["id"], "list_price", {"def": d["id"], "iid": y, "qty": 1},
                         f"💰 قیمت هر عدد «{esc(d['name'])}» را بنویس ({fa_num(lo)} تا {fa_num(hi)} سکه):", back="inv")
    have = await inv.stack_qty(conn, "backpack", user["id"], d["id"]) + await inv.stack_qty(conn, "storage", user["id"], d["id"])
    return Screen(f"📋 چند عدد {esc(d['name'])} آگهی شود؟ (موجود: {fa_num(have)})",
                  [[B("۱ عدد", "lstq", d["id"], "1"), B("همه", "lstq", d["id"], "a")]], back="inv")


@action("lstq")
async def a_list_qty(ctx, conn, user, ch, x, y):
    d = await inv.def_by_id(conn, int(x))
    have = await inv.stack_qty(conn, "backpack", user["id"], d["id"]) + await inv.stack_qty(conn, "storage", user["id"], d["id"])
    qty = have if y == "a" else 1
    lo, hi = market.price_range(d["value"], ctx.settings)
    return await ask(conn, user["id"], "list_price", {"def": d["id"], "iid": "", "qty": qty},
                     f"💰 قیمت هر عدد «{esc(d['name'])}» (×{fa_num(qty)}) را بنویس ({fa_num(lo)} تا {fa_num(hi)} سکه):", back="inv")


@text_input("list_price")
async def i_list_price(ctx, conn, user, ch, text, data):
    price = parse_int(text, 1, 10**7)
    await market.list_item(conn, ctx.settings, user["id"], data["def"], int(data["qty"]), price, data["iid"] or None)
    await clear_state(conn, user["id"])
    return Screen(f"✅ آگهی ثبت شد ({fa_num(price)} 🪙 هر عدد).", [[B("📦 آگهی‌های من", "pmy")]], back="mk")


# ───────── هدیه / انتقال ─────────
@action("gift")
async def a_gift(ctx, conn, user, ch, x, y):
    d = await inv.def_by_id(conn, int(x))
    if y:
        return await ask(conn, user["id"], "gift_target", {"def": d["id"], "iid": y, "qty": 1},
                         f"🎁 نام شخصیت گیرنده «{esc(d['name'])}» را بنویس:", back="inv")
    return Screen(f"🎁 چند عدد {esc(d['name'])}؟", [[B("۱ عدد", "giftq", d["id"], "1"), B("همه", "giftq", d["id"], "a")]], back="inv")


@action("giftq")
async def a_gift_q(ctx, conn, user, ch, x, y):
    have = await inv.stack_qty(conn, "backpack", user["id"], int(x)) + await inv.stack_qty(conn, "storage", user["id"], int(x))
    qty = have if y == "a" else 1
    return await ask(conn, user["id"], "gift_target", {"def": int(x), "iid": "", "qty": qty}, "🎁 نام شخصیت گیرنده را بنویس:", back="inv")


@text_input("gift_target")
async def i_gift_target(ctx, conn, user, ch, text, data):
    tgt = await find_user(conn, text)
    await market.give_item(conn, user["id"], tgt["user_id"], data["def"], int(data["qty"]), data["iid"] or None)
    await clear_state(conn, user["id"])
    return Screen(f"🎁 برای {esc(tgt['name'])} فرستاده شد (در انبار خانه‌اش).", [], back="inv")


@action("xf")
async def a_xfer(ctx, conn, user, ch, x, y):
    free = user["coins"] - user["locked_coins"]
    return await ask(conn, user["id"], "xf_target", {}, f"💸 انتقال سکه\nقابل‌انتقال: {fa_num(free)} 🪙 (سکه‌های اولیه قفل‌اند)\nنام شخصیت گیرنده را بنویس:", back="mk")


@text_input("xf_target")
async def i_xf_target(ctx, conn, user, ch, text, data):
    tgt = await find_user(conn, text)
    if tgt["user_id"] == user["id"]:
        raise GameError("به خودت نمی‌توانی بفرستی.")
    return await ask(conn, user["id"], "xf_amount", {"to": tgt["user_id"], "name": tgt["name"]},
                     f"چه مقدار سکه برای {esc(tgt['name'])} بفرستم؟", back="mk")


@text_input("xf_amount")
async def i_xf_amount(ctx, conn, user, ch, text, data):
    amt = parse_int(text, 1, 10**9)
    await market.transfer_coins(conn, ctx.settings, user["id"], data["to"], amt)
    await clear_state(conn, user["id"])
    return Screen(f"✅ {fa_num(amt)} 🪙 برای {esc(data['name'])} فرستاده شد.", [], back="mk")


# ───────── معامله امن ─────────
async def _trade_screen(ctx, conn, user, t, note="") -> Screen:
    me_a = user["id"] == t["a_user"]
    names = {r["user_id"]: r["name"] for r in await conn.fetch("SELECT user_id, name FROM characters WHERE user_id = ANY($1::bigint[])",
                                                               [t["a_user"], t["b_user"]])}
    items = await market.trade_items(conn, t["id"])

    def side(s, uid, coins, ok):
        its = [f"{esc(i['name'])} ×{fa_num(i['qty'])}" for i in items if i["side"] == s]
        return f"<b>{esc(names[uid])}</b> {'✅' if ok else '⏳'}\n  🪙 {fa_num(coins)}\n  " + ("، ".join(its) or "—")

    text = (f"🤝 <b>معامله امن</b> (مهلت: {left(t['expires_at'])})\n\n{side('a', t['a_user'], t['a_coins'], t['a_ok'])}\n\n"
            f"{side('b', t['b_user'], t['b_coins'], t['b_ok'])}\n\nمعامله فقط وقتی هر دو تأیید کنند انجام می‌شود. با هر تغییر، تأییدها پاک می‌شود.")
    rows = [[B("🪙 تعیین سکه من", "trc", t["id"]), B("➕ افزودن آیتم", "trl", t["id"])],
            [B("🗑 پاک‌کردن آیتم‌های من", "trx", t["id"]), B("✅ تأیید", "tro", t["id"])],
            [B("❌ لغو معامله", "trk", t["id"]), B("🔄 بروزرسانی", "tr")]]
    return Screen(text, rows, back="mk", note=note)


@action("tr")
async def a_trade(ctx, conn, user, ch, x, y, note=""):
    t = await market.trade_of(conn, user["id"])
    if t is None:
        return await ask(conn, user["id"], "trade_target", {}, "🤝 نام شخصیتی که می‌خواهی با او معامله کنی را بنویس:", back="mk")
    return await _trade_screen(ctx, conn, user, t, note)


@text_input("trade_target")
async def i_trade_target(ctx, conn, user, ch, text, data):
    tgt = await find_user(conn, text)
    await market.trade_open(conn, ctx.settings, user["id"], tgt["user_id"])
    await clear_state(conn, user["id"])
    t = await market.trade_of(conn, user["id"])
    return await _trade_screen(ctx, conn, user, t, "معامله ساخته شد.")


@action("trc")
async def a_trade_coins(ctx, conn, user, ch, x, y):
    return await ask(conn, user["id"], "trade_coins", {"tid": int(x)}, "چند سکه در معامله بگذاری؟ (۰ برای حذف)", back="tr")


@text_input("trade_coins")
async def i_trade_coins(ctx, conn, user, ch, text, data):
    await market.trade_set_coins(conn, user["id"], data["tid"], parse_int(text, 0, 10**9))
    await clear_state(conn, user["id"])
    t = await market.trade_of(conn, user["id"])
    return await _trade_screen(ctx, conn, user, t, "ثبت شد.")


@action("trl")
async def a_trade_list(ctx, conn, user, ch, x, y):
    rows = []
    for s in (await inv.stacks_of(conn, "backpack", user["id"]) + await inv.stacks_of(conn, "storage", user["id"]))[:10]:
        rows.append([B(f"{s['name']} ×۱", "tra", x, f"s{s['def_id']}:1"), B("×۵", "tra", x, f"s{s['def_id']}:5")])
    for ht in ("backpack", "storage"):
        for i in (await inv.instances_of(conn, ht, user["id"]))[:8]:
            if i["tradable"] and not i["bound"] and i["owner_user_id"] == user["id"]:
                rows.append([B(f"🗡 {i['name']}", "tra", x, f"i{i['id']}")])
    if not rows:
        raise GameError("وسیله قابل‌معامله‌ای نداری.")
    return Screen("➕ کدام آیتم را اضافه کنم؟", rows, back="tr")


@action("tra")
async def a_trade_add(ctx, conn, user, ch, x, y):
    if y.startswith("s"):
        did, q = y[1:].split(":")
        await market.trade_add_item(conn, user["id"], int(x), int(did), int(q))
    else:
        r = await conn.fetchrow("SELECT def_id FROM item_instances WHERE id=$1::uuid", y[1:])
        await market.trade_add_item(conn, user["id"], int(x), r["def_id"], 1, y[1:])
    t = await market.trade_of(conn, user["id"])
    return await _trade_screen(ctx, conn, user, t, "اضافه شد.")


@action("trx")
async def a_trade_clear(ctx, conn, user, ch, x, y):
    await market.trade_clear(conn, user["id"], int(x))
    return await _trade_screen(ctx, conn, user, await market.trade_of(conn, user["id"]), "آیتم‌هایت پاک شد.")


@action("tro")
async def a_trade_ok(ctx, conn, user, ch, x, y):
    done = await market.trade_confirm(conn, ctx.settings, user["id"], int(x))
    if done:
        return Screen("🤝 معامله با موفقیت انجام شد؛ وسایل در انبار خانه‌ات است.", [], back="mk")
    return await _trade_screen(ctx, conn, user, await market.trade_of(conn, user["id"]), "تأیید شد؛ منتظر طرف مقابل.")


@action("trk")
async def a_trade_cancel(ctx, conn, user, ch, x, y):
    await market.trade_cancel(conn, user["id"], int(x))
    return Screen("معامله لغو شد.", [], back="mk")


# ───────── قبیله ─────────
@action("tb")
async def a_tribe(ctx, conn, user, ch, x, y, note=""):
    m = await tribes.membership(conn, user["id"])
    if m is None:
        inv_n = len(await tribes.my_invites(conn, user["id"]))
        return Screen(f"🏕 <b>قبیله</b>\nعضو هیچ قبیله‌ای نیستی.\nهزینه تأسیس: {fa_num(ctx.settings.get('tribe.found_cost'))} 🪙",
                      [[B("➕ تأسیس قبیله", "tbc"), B("🔎 قبایل باز", "tbl")], [B(f"📨 دعوت‌ها ({fa_num(inv_n)})", "tbi")]], note=note)
    n = await tribes.count_members(conn, m["tribe_id"])
    cap = await tribes.member_cap(conn, ctx.settings, m["tribe_id"])
    ts = await tribes.tribe_stats(conn, m["tribe_id"])
    text = (f"🏕 <b>{esc(m['name'])}</b> — سطح {fa_num(m['level'])}\nنقش تو: <b>{tribes.ROLE_FA[m['role']]}</b>\n"
            f"👥 اعضا: {fa_num(n)}/{fa_num(cap)}   🔐 عضویت: {tribes.MEMBERSHIP_FA[m['membership']]}\n"
            f"🪙 خزانه: {fa_num(m['coins'])}   🛡 دفاع پایگاه: {fa_num(int(ts['defense']))}")
    rows = [[B("👥 اعضا", "tbm"), B("💰 خزانه و انبار", "tbs")], [B("🏗 ساختمان‌ها", "tbb"), B("🤝 دیپلماسی", "tbd")],
            [B("⚔️ جنگ", "tbw"), B("⚙️ مدیریت", "tbg")]]
    if tribes.allowed(m["role"], "invite"):
        rows.append([B("📨 درخواست‌ها", "tbr"), B("➕ دعوت", "tbv")])
    return Screen(text, rows, note=note)


@action("tbc")
async def a_tribe_create(ctx, conn, user, ch, x, y):
    return await ask(conn, user["id"], "tribe_name", {}, f"🏕 نام قبیله را بنویس (هزینه {fa_num(ctx.settings.get('tribe.found_cost'))} 🪙):", back="tb")


@text_input("tribe_name")
async def i_tribe_name(ctx, conn, user, ch, text, data):
    await tribes.create(conn, ctx.settings, user["id"], text)
    await clear_state(conn, user["id"])
    return Screen("🏕 قبیله تأسیس شد و تو رهبر آن هستی.", [[B("🏕 قبیله من", "tb")]])


@action("tbl")
async def a_tribe_list(ctx, conn, user, ch, x, y):
    rows, lines = [], ["🔎 <b>قبایل</b>", ""]
    for t in await tribes.list_open(conn):
        lines.append(f"• {esc(t['name'])} — سطح {fa_num(t['level'])} — {fa_num(t['n'])} عضو — {tribes.MEMBERSHIP_FA[t['membership']]}")
        rows.append([B(f"پیوستن به {t['name']}", "tbj", t["id"])])
    return Screen("\n".join(lines), rows, back="tb")


@action("tbj")
async def a_tribe_join(ctx, conn, user, ch, x, y):
    msg = await tribes.join(conn, ctx.settings, user["id"], int(x))
    return Screen(msg, [[B("🏕 قبیله", "tb")]], back="main")


@action("tbi")
async def a_tribe_invites(ctx, conn, user, ch, x, y):
    inv_l = await tribes.my_invites(conn, user["id"])
    return Screen("📨 <b>دعوت‌ها</b>" + ("" if inv_l else "\nدعوتی نداری."), [[B(f"پذیرفتن {i['name']}", "tbia", i["tribe_id"])] for i in inv_l], back="tb")


@action("tbia")
async def a_tribe_inv_accept(ctx, conn, user, ch, x, y):
    name = await tribes.accept_invite(conn, ctx.settings, user["id"], int(x))
    return Screen(f"به قبیله «{esc(name)}» پیوستی.", [[B("🏕 قبیله", "tb")]], back="main")


@action("tbm")
async def a_tribe_members(ctx, conn, user, ch, x, y, note=""):
    m = await tribes.need(conn, user["id"], "defense") if False else await tribes.membership(conn, user["id"])
    if m is None:
        raise GameError("عضو قبیله‌ای نیستی.")
    caps = {c["captive_id"]: c["id"] for c in await conn.fetch(
        "SELECT c.id, c.captive_id FROM captivities c JOIN tribe_members t ON t.user_id=c.captive_id WHERE t.tribe_id=$1 AND c.status='active'",
        m["tribe_id"])}
    lines, rows = ["👥 <b>اعضا</b>", ""], []
    for r in await tribes.members(conn, m["tribe_id"]):
        tag = " ⛓" if r["user_id"] in caps else ""
        lines.append(f"• {esc(r['name'])} — {tribes.ROLE_FA[r['role']]}{tag}")
        if r["user_id"] in caps and r["user_id"] != user["id"]:
            rows.append([B(f"🛡 نجات {r['name']}", "rescue", caps[r["user_id"]])])
        if r["user_id"] != user["id"] and (tribes.allowed(m["role"], "kick") or m["role"] == "leader"):
            rows.append([B(f"⚙️ {r['name']}", "tbmm", r["user_id"])])
    return Screen("\n".join(lines), rows, back="tb", note=note)


@action("tbmm")
async def a_tribe_member(ctx, conn, user, ch, x, y):
    m = await tribes.membership(conn, user["id"])
    r = await conn.fetchrow("SELECT m.*, c.name FROM tribe_members m JOIN characters c ON c.user_id=m.user_id WHERE m.user_id=$1 AND m.tribe_id=$2",
                            int(x), m["tribe_id"])
    if r is None:
        raise GameError("عضو پیدا نشد.")
    rows = []
    if m["role"] == "leader":
        rows += [[B(fa, "tbrole", x, k)] for k, fa in tribes.ROLE_FA.items() if k != r["role"]]
    if tribes.allowed(m["role"], "kick"):
        rows.append([B("🚪 اخراج", "tbkick", x)])
    return Screen(f"⚙️ {esc(r['name'])} — {tribes.ROLE_FA[r['role']]}", rows, back="tbm")


@action("tbrole")
async def a_tribe_role(ctx, conn, user, ch, x, y):
    await tribes.set_role(conn, user["id"], int(x), y)
    return await a_tribe_members(ctx, conn, user, ch, "", "", note="✅ نقش تغییر کرد.")


@action("tbkick")
async def a_tribe_kick(ctx, conn, user, ch, x, y):
    await tribes.kick(conn, user["id"], int(x))
    return await a_tribe_members(ctx, conn, user, ch, "", "", note="🚪 عضو اخراج شد.")


@action("tbr")
async def a_tribe_requests(ctx, conn, user, ch, x, y):
    m = await tribes.need(conn, user["id"], "invite")
    reqs = await tribes.requests_of(conn, m["tribe_id"])
    rows = [[B(f"✅ {r['name']}", "tbrq", r["user_id"], "1"), B("❌", "tbrq", r["user_id"], "0")] for r in reqs]
    return Screen("📨 <b>درخواست‌های عضویت</b>" + ("" if reqs else "\nدرخواستی نیست."), rows, back="tb")


@action("tbrq")
async def a_tribe_req_do(ctx, conn, user, ch, x, y):
    await tribes.decide_request(conn, ctx.settings, user["id"], int(x), y == "1")
    return await a_tribe_requests(ctx, conn, user, ch, "", "")


@action("tbv")
async def a_tribe_invite(ctx, conn, user, ch, x, y):
    await tribes.need(conn, user["id"], "invite")
    return await ask(conn, user["id"], "tribe_invite", {}, "نام شخصیت دعوت‌شونده را بنویس:", back="tb")


@text_input("tribe_invite")
async def i_tribe_invite(ctx, conn, user, ch, text, data):
    tgt = await find_user(conn, text)
    await tribes.invite(conn, ctx.settings, user["id"], tgt["user_id"])
    await clear_state(conn, user["id"])
    return Screen(f"📨 دعوت برای {esc(tgt['name'])} ارسال شد.", [], back="tb")


@action("tbs")
async def a_tribe_store(ctx, conn, user, ch, x, y, note=""):
    m = await tribes.membership(conn, user["id"])
    if m is None:
        raise GameError("عضو قبیله‌ای نیستی.")
    stacks, inst = await tribes.pool_items(conn, m["tribe_id"])
    ts = await tribes.tribe_stats(conn, m["tribe_id"])
    lines = [f"💰 <b>خزانه و انبار {esc(m['name'])}</b>", f"🪙 خزانه: {fa_num(m['coins'])}", f"📦 ظرفیت انبار: {fa_num(int(ts['storage']))}", "",
             "<b>دارایی عمومی</b>"]
    lines += [f"• {esc(s['name'])} ×{fa_num(s['qty'])}" for s in stacks] + [f"• {esc(i['name'])}" + (" (امانی)" if i["owner_user_id"] else "") for i in inst]
    if not stacks and not inst:
        lines.append("— خالی —")
    loans = await tribes.my_loans(conn, user["id"])
    if loans:
        lines += ["", "<b>امانی‌های من</b>"] + [f"• {esc(l['name'])} ×{fa_num(l['qty'])}" for l in loans]
    rows = [[B("🪙 واریز سکه", "tbdc"), B("🪙 برداشت سکه", "tbwc")],
            [B("🎁 اهدای آیتم", "tbdn", "d"), B("📥 امانت‌گذاری", "tbdn", "l")]]
    if tribes.allowed(m["role"], "withdraw"):
        rows.append([B("📤 برداشت آیتم", "tbwi")])
    rows += [[B(f"↩️ پس‌گرفتن {l['name']}", "tbrc", l["id"])] for l in loans[:5]]
    rows.append([B("📜 دفتر تراکنش‌ها", "tbled")])
    return Screen("\n".join(lines), rows, back="tb", note=note)


@action("tbdc")
async def a_tb_donate_coins(ctx, conn, user, ch, x, y):
    return await ask(conn, user["id"], "tb_donate", {}, "چند سکه به خزانه قبیله واریز کنم؟", back="tbs")


@text_input("tb_donate")
async def i_tb_donate(ctx, conn, user, ch, text, data):
    await tribes.donate_coins(conn, user["id"], parse_int(text))
    await clear_state(conn, user["id"])
    return Screen("✅ واریز شد و در دفتر ثبت گردید.", [], back="tbs")


@action("tbwc")
async def a_tb_withdraw_coins(ctx, conn, user, ch, x, y):
    await tribes.need(conn, user["id"], "withdraw")
    return await ask(conn, user["id"], "tb_withdraw", {}, "چند سکه از خزانه برداشت شود؟", back="tbs")


@text_input("tb_withdraw")
async def i_tb_withdraw(ctx, conn, user, ch, text, data):
    await tribes.withdraw_coins(conn, user["id"], parse_int(text))
    await clear_state(conn, user["id"])
    return Screen("✅ برداشت شد و در دفتر ثبت گردید.", [], back="tbs")


@action("tbdn")
async def a_tb_donate_list(ctx, conn, user, ch, x, y):
    rows = []
    for s in (await inv.stacks_of(conn, "backpack", user["id"]) + await inv.stacks_of(conn, "storage", user["id"]))[:10]:
        rows.append([B(f"{s['name']} ×۱", "tbdi", x, f"s{s['def_id']}:1"), B("×۵", "tbdi", x, f"s{s['def_id']}:5")])
    for ht in ("backpack", "storage"):
        for i in (await inv.instances_of(conn, ht, user["id"]))[:8]:
            if i["tradable"] and not i["bound"] and i["owner_user_id"] == user["id"]:
                rows.append([B(f"🗡 {i['name']}", "tbdi", x, f"i{i['id']}")])
    if not rows:
        raise GameError("چیزی برای واگذاری نداری.")
    title = "🎁 اهدای عمومی (مالکیت به قبیله می‌رسد)" if x == "d" else "📥 امانت‌گذاری (مالکیت تو حفظ می‌شود)"
    return Screen(title, rows, back="tbs")


@action("tbdi")
async def a_tb_donate_item(ctx, conn, user, ch, x, y):
    fn = tribes.donate_item if x == "d" else tribes.loan_item
    if y.startswith("s"):
        did, q = y[1:].split(":")
        await fn(conn, ctx.settings, user["id"], int(did), int(q))
    else:
        r = await conn.fetchrow("SELECT def_id FROM item_instances WHERE id=$1::uuid", y[1:])
        await fn(conn, ctx.settings, user["id"], r["def_id"], 1, y[1:])
    return await a_tribe_store(ctx, conn, user, ch, "", "", note="✅ ثبت شد.")


@action("tbwi")
async def a_tb_withdraw_list(ctx, conn, user, ch, x, y):
    m = await tribes.need(conn, user["id"], "withdraw")
    stacks, inst = await tribes.pool_items(conn, m["tribe_id"])
    rows = [[B(f"{s['name']} ×۱", "tbwd", f"s{s['def_id']}:1"), B("×۵", "tbwd", f"s{s['def_id']}:5")] for s in stacks[:10]]
    rows += [[B(f"🗡 {i['name']}", "tbwd", f"i{i['id']}")] for i in inst[:10]]
    return Screen("📤 چه چیزی برداشت شود؟ (دارایی عمومی به مالکیت تو درمی‌آید؛ امانی‌ها فقط قرض گرفته می‌شوند)", rows, back="tbs")


@action("tbwd")
async def a_tb_withdraw_item(ctx, conn, user, ch, x, y):
    if x.startswith("s"):
        did, q = x[1:].split(":")
        await tribes.withdraw_item(conn, ctx.settings, user["id"], ch, int(did), int(q))
    else:
        r = await conn.fetchrow("SELECT def_id FROM item_instances WHERE id=$1::uuid", x[1:])
        await tribes.withdraw_item(conn, ctx.settings, user["id"], ch, r["def_id"], 1, x[1:])
    return await a_tribe_store(ctx, conn, user, ch, "", "", note="✅ برداشت ثبت شد.")


@action("tbrc")
async def a_tb_recall(ctx, conn, user, ch, x, y):
    await tribes.recall_loan(conn, user["id"], int(x))
    return await a_tribe_store(ctx, conn, user, ch, "", "", note="↩️ امانت به انبار خانه‌ات برگشت.")


@action("tbled")
async def a_tb_ledger(ctx, conn, user, ch, x, y):
    m = await tribes.membership(conn, user["id"])
    lines = ["📜 <b>دفتر تراکنش‌های قبیله</b>", ""]
    for l in await tribes.ledger(conn, m["tribe_id"]):
        what = (f"{fa_num(l['coins'])}🪙" if l["coins"] else f"{esc(l['dname'] or '')} ×{fa_num(l['qty'])}")
        lines.append(f"• {esc(l['uname'] or '—')}: {l['kind']} {what}")
    return Screen("\n".join(lines), [], back="tbs")


@action("tbb")
async def a_tribe_buildings(ctx, conn, user, ch, x, y, note=""):
    m = await tribes.membership(conn, user["id"])
    names = {r["code"]: r["name"] for r in await conn.fetch("SELECT code, name FROM item_defs")}
    have = {b["code"]: b for b in await homes.user_buildings(conn, "tribe", m["tribe_id"])}
    lines, rows = ["🏗 <b>ساختمان‌های قبیله</b> (هزینه از خزانه و انبار قبیله)", ""], []
    for d in await conn.fetch("SELECT * FROM building_defs WHERE scope='tribe' AND is_active ORDER BY build_minutes"):
        b = have.get(d["code"])
        lvl = b["level"] if b else 0
        req = jload(d["requires"]).get("tribe_level", 1)
        if b and b["status"] == "building":
            lines.append(f"• {esc(d['name'])}: در حال ساخت تا {left(b['ready_at'])}")
            continue
        if lvl >= d["max_level"]:
            lines.append(f"• {esc(d['name'])}: حداکثر سطح ✅")
            continue
        c = homes.cost_at(jload(d["cost"]), d["cost_mult"], lvl + 1)
        lock = f" 🔒 قبیله سطح {fa_num(req)}" if req > m["level"] else ""
        lines.append(f"• {esc(d['name'])} (سطح {fa_num(lvl)}→{fa_num(lvl + 1)}): {homes.cost_text(c, names)}{lock}")
        if not lock:
            rows.append([B(f"{'⬆️' if lvl else '🔨'} {d['name']}", "tbbl", d["code"])])
    return Screen("\n".join(lines), rows, back="tb", note=note)


@action("tbbl")
async def a_tribe_build(ctx, conn, user, ch, x, y):
    msg, _ = await tribes.build(conn, ctx.settings, user["id"], x)
    return await a_tribe_buildings(ctx, conn, user, ch, "", "", note=msg)


@action("tbg")
async def a_tribe_manage(ctx, conn, user, ch, x, y, note=""):
    m = await tribes.membership(conn, user["id"])
    rows = []
    if tribes.allowed(m["role"], "membership"):
        rows.append([B(("✅ " if m["membership"] == k else "") + v, "tbmode", k) for k, v in tribes.MEMBERSHIP_FA.items()])
    nxt = str(m["level"] + 1)
    lv = ctx.settings.get("tribe.levels")
    if tribes.allowed(m["role"], "upgrade") and nxt in lv:
        rows.append([B(f"⬆️ ارتقا به سطح {fa_num(nxt)} ({fa_num(lv[nxt]['cost'])}🪙 از خزانه)", "tbup")])
    rows.append([B("🚪 ترک قبیله", "tbleave")])
    return Screen("⚙️ <b>مدیریت قبیله</b>\nترک قبیله به دارایی شخصی‌ات آسیبی نمی‌زند؛ امانی‌هایت به انبار خانه‌ات برمی‌گردد.", rows, back="tb", note=note)


@action("tbmode")
async def a_tribe_mode(ctx, conn, user, ch, x, y):
    await tribes.set_membership(conn, user["id"], x)
    return await a_tribe_manage(ctx, conn, user, ch, "", "", note="✅ حالت عضویت تغییر کرد.")


@action("tbup")
async def a_tribe_upgrade(ctx, conn, user, ch, x, y):
    lvl = await tribes.upgrade(conn, ctx.settings, user["id"])
    return await a_tribe_manage(ctx, conn, user, ch, "", "", note=f"⬆️ قبیله به سطح {fa_num(lvl)} رسید.")


@action("tbleave")
async def a_tribe_leave(ctx, conn, user, ch, x, y):
    await tribes.leave(conn, user["id"])
    return Screen("از قبیله خارج شدی.", [], back="main")


@action("tbd")
async def a_tribe_diplo(ctx, conn, user, ch, x, y, note=""):
    m = await tribes.membership(conn, user["id"])
    lines, rows = ["🤝 <b>دیپلماسی</b>", ""], []
    for r in await tribes.relations(conn, m["tribe_id"]):
        other = r["b_name"] if r["tribe_a"] == m["tribe_id"] else r["a_name"]
        kind = "هم‌پیمان" if r["kind"] == "ally" else "رقیب"
        lines.append(f"• {esc(other)} — {kind} ({'فعال' if r['status'] == 'active' else 'در انتظار'})")
        if r["kind"] == "ally" and r["status"] == "pending" and r["tribe_b"] == m["tribe_id"] and tribes.allowed(m["role"], "diplomacy"):
            rows.append([B(f"✅ پذیرش {other}", "tbal", r["tribe_a"], "1"), B("❌", "tbal", r["tribe_a"], "0")])
    if tribes.allowed(m["role"], "diplomacy"):
        rows.append([B("🤝 پیشنهاد اتحاد", "tbdp", "ally"), B("⚔️ اعلام رقابت", "tbdp", "rival")])
    return Screen("\n".join(lines), rows, back="tb", note=note)


@action("tbal")
async def a_tribe_alliance(ctx, conn, user, ch, x, y):
    await tribes.respond_alliance(conn, user["id"], int(x), y == "1")
    return await a_tribe_diplo(ctx, conn, user, ch, "", "", note="ثبت شد.")


@action("tbdp")
async def a_tribe_diplo_propose(ctx, conn, user, ch, x, y):
    return await ask(conn, user["id"], "tribe_diplo", {"kind": x}, "نام قبیله مقابل را بنویس:", back="tbd")


@text_input("tribe_diplo")
async def i_tribe_diplo(ctx, conn, user, ch, text, data):
    st = await tribes.propose(conn, user["id"], text, data["kind"])
    await clear_state(conn, user["id"])
    return Screen("✅ ثبت شد." if st == "active" else "✅ پیشنهاد اتحاد ارسال شد.", [], back="tbd")


@action("tbw")
async def a_tribe_war(ctx, conn, user, ch, x, y):
    m = await tribes.need(conn, user["id"], "attack")
    rows, lines = [], ["⚔️ <b>جنگ قبیله‌ای</b>", "حمله به پایگاه قبیله دیگر با همان سیستم نبرد مرحله‌ای انجام می‌شود.", ""]
    for t in await tribes.other_tribes(conn, m["tribe_id"]):
        lines.append(f"• {esc(t['name'])} — سطح {fa_num(t['level'])}")
        rows.append([B(f"⚔️ حمله به {t['name']}", "tbwa", t["id"])])
    return Screen("\n".join(lines), rows, back="tb")


@action("tbwa")
async def a_tribe_attack(ctx, conn, user, ch, x, y):
    await tribes.need(conn, user["id"], "attack")
    c = await combat.start_tribe_attack(conn, ctx.settings, user["id"], int(x))
    return Screen("⚔️ شناسایی پایگاه شروع شد؛ پیام نبرد برایت ارسال می‌شود.", [], back="tb", combat_cid=c["id"])


# ───────── اسارت ─────────
@action("cp")
async def a_captive(ctx, conn, user, ch, x, y, note=""):
    cap = await captivity.active_for(conn, user["id"])
    if cap is None:
        return await main_screen(ctx, conn, user, ch)
    captor = await conn.fetchval("SELECT name FROM characters WHERE user_id=$1", cap["captor_id"])
    text = (f"⛓ <b>در اسارت {esc(captor)}</b>\nپایان خودکار اسارت: {left(cap['ends_at'])} دیگر\n"
            f"💰 باج تعیین‌شده: {fa_num(cap['ransom'])} 🪙   پیشنهاد تو: {fa_num(cap['offer'])} 🪙\n")
    rows = []
    if cap["task"]:
        task = jload(cap["task"])
        text += f"📋 کار آزادی: تحویل {fa_num(task['qty'])} {esc(task['name'])}\n"
        rows.append([B("📋 تحویل کار آزادی", "cp_task")])
    if cap["ransom"] > 0:
        rows.append([B(f"💰 پرداخت باج ({fa_num(cap['ransom'])})", "cp_pay")])
    rows.append([B("🤝 پیشنهاد باج", "cp_offer"), B("🏃 تلاش برای فرار", "cp_esc")])
    return Screen(text, rows, back=None, note=note)


@action("cp_pay")
async def a_cp_pay(ctx, conn, user, ch, x, y):
    cap = await captivity.active_for(conn, user["id"])
    await captivity.pay_ransom(conn, ctx.settings, user["id"], cap["id"])
    return Screen("🔓 باج پرداخت شد و آزاد شدی.", [], back="main")


@action("cp_task")
async def a_cp_task(ctx, conn, user, ch, x, y):
    cap = await captivity.active_for(conn, user["id"])
    await captivity.fulfill_task(conn, ctx.settings, user["id"], cap["id"])
    return Screen("🔓 کار آزادی انجام شد و آزاد شدی.", [], back="main")


@action("cp_offer")
async def a_cp_offer(ctx, conn, user, ch, x, y):
    cap = await captivity.active_for(conn, user["id"])
    return await ask(conn, user["id"], "ransom_offer", {"cap": cap["id"]}, "چه مبلغی برای آزادی پیشنهاد می‌دهی؟", back="cp")


@text_input("ransom_offer")
async def i_ransom_offer(ctx, conn, user, ch, text, data):
    amt = parse_int(text, 0, int(ctx.settings.get("captivity.ransom_max")))
    await captivity.offer(conn, user["id"], data["cap"], amt)
    await clear_state(conn, user["id"])
    return Screen("🤝 پیشنهادت برای اسیرگیر فرستاده شد.", [], back="cp")


@action("cp_esc")
async def a_cp_escape(ctx, conn, user, ch, x, y):
    import random
    cap = await captivity.active_for(conn, user["id"])
    ok = await captivity.escape(conn, ctx.settings, user["id"], cap["id"], random.Random())
    return Screen("🏃 موفق شدی از اسارت فرار کنی!" if ok else "❌ فرارت ناکام ماند.", [], back="cp" if not ok else "main")


@action("cap_l")
async def a_captives(ctx, conn, user, ch, x, y, note=""):
    held = await captivity.held_by(conn, user["id"])
    lines = ["⛓ <b>اسرای من</b>", ""] + [f"• {esc(h['captive_name'])} — {left(h['ends_at'])} تا پایان" for h in held]
    if not held:
        lines.append("اسیری نداری.")
    return Screen("\n".join(lines), [[B(h["captive_name"], "capd", h["id"])] for h in held], note=note)


@action("capd")
async def a_captive_detail(ctx, conn, user, ch, x, y, note=""):
    cap = await captivity.get_cap(conn, int(x), lock=False)
    if cap["captor_id"] != user["id"]:
        raise GameError("اجازه نداری.")
    name = await conn.fetchval("SELECT name FROM characters WHERE user_id=$1", cap["captive_id"])
    text = (f"⛓ <b>{esc(name)}</b>\nپایان خودکار: {left(cap['ends_at'])}\nباج تعیین‌شده: {fa_num(cap['ransom'])} 🪙   "
            f"پیشنهاد اسیر: {fa_num(cap['offer'])} 🪙" + (f"\nکار آزادی: {esc(jload(cap['task'])['name'])} ×{fa_num(jload(cap['task'])['qty'])}" if cap["task"] else ""))
    rows = [[B("🧳 مصادره وسایل همراه", "capc", x), B("💰 تعیین باج", "caps", x)]]
    presets = ctx.settings.get("captivity.task_presets")
    rows.append([B(f"📋 کار: {fa_num(q)} {code}", "capt", x, i) for i, (code, q) in enumerate(presets[:3])])
    if cap["offer"] > 0:
        rows.append([B(f"✅ قبول پیشنهاد ({fa_num(cap['offer'])})", "capa", x)])
    rows.append([B("🔓 آزادسازی", "capr", x)])
    return Screen(text, rows, back="cap_l", note=note)


@action("capc")
async def a_cap_confiscate(ctx, conn, user, ch, x, y):
    taken = await captivity.confiscate(conn, ctx.settings, user["id"], int(x))
    return await a_captive_detail(ctx, conn, user, ch, x, "", note=("🧳 مصادره شد: " + "، ".join(f"{n} ×{q}" for n, q in taken)) if taken else "چیزی برای مصادره نبود (یا کوله‌ات جا نداشت).")


@action("caps")
async def a_cap_ransom(ctx, conn, user, ch, x, y):
    return await ask(conn, user["id"], "ransom_set", {"cap": int(x)}, f"باج آزادی را بنویس (حداکثر {fa_num(ctx.settings.get('captivity.ransom_max'))}):", back="cap_l")


@text_input("ransom_set")
async def i_ransom_set(ctx, conn, user, ch, text, data):
    amt = await captivity.set_ransom(conn, ctx.settings, user["id"], data["cap"], parse_int(text, 0, 10**9))
    await clear_state(conn, user["id"])
    return Screen(f"💰 باج {fa_num(amt)} 🪙 تعیین شد.", [], back="cap_l")


@action("capt")
async def a_cap_task(ctx, conn, user, ch, x, y):
    await captivity.set_task(conn, ctx.settings, user["id"], int(x), int(y))
    return await a_captive_detail(ctx, conn, user, ch, x, "", note="📋 کار آزادی تعیین شد.")


@action("capa")
async def a_cap_accept(ctx, conn, user, ch, x, y):
    await captivity.accept_offer(conn, ctx.settings, user["id"], int(x))
    return await a_captives(ctx, conn, user, ch, "", "", note="🤝 پیشنهاد پذیرفته شد و اسیر آزاد شد.")


@action("capr")
async def a_cap_release(ctx, conn, user, ch, x, y):
    await captivity.release(conn, ctx.settings, user["id"], int(x))
    return await a_captives(ctx, conn, user, ch, "", "", note="🔓 آزاد شد.")


@action("rescue")
async def a_rescue(ctx, conn, user, ch, x, y):
    import random
    ok = await captivity.rescue(conn, ctx.settings, user["id"], int(x), random.Random())
    return Screen("🛡 عملیات نجات موفق بود!" if ok else "❌ عملیات نجات ناکام ماند و کمی زخمی شدی.", [], back="tbm")


# ───────── گزارش‌ها و فصل ─────────
@action("rp")
async def a_reports(ctx, conn, user, ch, x, y, note=""):
    rows_db = await conn.fetch("SELECT id, kind, text, is_read, created_at FROM notifications WHERE user_id=$1 ORDER BY id DESC LIMIT 12", user["id"])
    await conn.execute("UPDATE notifications SET is_read=true WHERE user_id=$1 AND NOT is_read", user["id"])
    lines = ["📜 <b>گزارش اتفاقات</b>", ""]
    for r in rows_db:
        lines.append(("🆕 " if not r["is_read"] else "• ") + esc(r["text"]))
    if not rows_db:
        lines.append("هنوز اتفاقی ثبت نشده.")
    return Screen("\n".join(lines), [[B("🏆 فصل رقابتی", "ss"), B("🚨 گزارش تخلف", "rpt")]], note=note)


@action("ss")
async def a_season(ctx, conn, user, ch, x, y, note=""):
    s = await events.current_season(conn)
    if s is None:
        return Screen("🏆 فصل رقابتی فعالی وجود ندارد. جهان اصلی بدون فصل هم ادامه دارد.", [], back="rp")
    entered = await conn.fetchval("SELECT 1 FROM season_entries WHERE season_id=$1 AND user_id=$2", s["id"], user["id"])
    lines = [f"🏆 <b>{esc(s['name'])}</b>", f"شروع: {left(s['starts_at']) if s['starts_at'] > __import__('datetime').datetime.now(__import__('datetime').timezone.utc) else 'شده'}   پایان: {left(s['ends_at'])}",
             f"ورودیه: {fa_num(s['entry_fee'])} 🪙   حداقل سطح: {fa_num(s['min_level'])}", ""]
    for i, r in enumerate(await events.leaderboard(conn, s["id"]), 1):
        lines.append(f"{fa_num(i)}. {esc(r['name'])} — {fa_num(r['score'])}")
    rows = [] if entered else [[B("✅ ثبت‌نام در فصل", "ssj")]]
    return Screen("\n".join(lines), rows, back="rp", note=note)


@action("ssj")
async def a_season_join(ctx, conn, user, ch, x, y):
    name = await events.join_season(conn, ctx.settings, user["id"], ch)
    return await a_season(ctx, conn, user, ch, "", "", note=f"ثبت‌نام در «{name}» انجام شد.")


@action("rpt")
async def a_report_violation(ctx, conn, user, ch, x, y):
    return await ask(conn, user["id"], "report_text", {}, "🚨 گزارش تخلف را بنویس (نام شخصیت خاطی و توضیح):", back="rp")


@text_input("report_text")
async def i_report_text(ctx, conn, user, ch, text, data):
    n = await conn.fetchval("SELECT count(*) FROM violation_reports WHERE reporter_id=$1 AND status='open'", user["id"])
    if n >= int(ctx.settings.get("report.max_open")):
        raise GameError("گزارش‌های بازت زیاد است؛ تا بررسی منتظر بمان.")
    await conn.execute("INSERT INTO violation_reports(reporter_id, text) VALUES($1,$2)", user["id"], text[:1000])
    await clear_state(conn, user["id"])
    return Screen("✅ گزارش ثبت شد و مدیران بررسی می‌کنند.", [], back="rp")
