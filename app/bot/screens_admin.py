"""پنل مدیریت کاملاً دکمه‌ای (فقط ADMIN_IDS)."""
from __future__ import annotations

import json
from decimal import Decimal
from html import escape

from app.bot.ui import B, Screen, action, ask, clear_state, esc, fa_num, left, parse_int, text_input
from app.services import events
from app.services import inventory as inv
from app.services.economy import change_coins
from app.services.errors import GameError
from app.services.settings import DEFAULTS, SettingsError

TABLES = {"items": ("item_defs", "آیتم‌ها"), "activities": ("activity_defs", "فعالیت‌ها"),
          "buildings": ("building_defs", "ساختمان‌ها"), "breeds": ("animal_breeds", "نژاد حیوان‌ها"),
          "hirelings": ("hireling_defs", "نیروهای استخدامی"), "missions": ("mission_defs", "مأموریت‌ها")}
NEW_EXAMPLE = {
    "item_defs": '{"code":"club","name":"گرز","kind":"weapon","power":9,"weight":3,"max_durability":90,"value":40}',
    "activity_defs": '{"code":"fish","name":"ماهیگیری","zone":"river","kind":"gather","loot":[{"item":"meat_raw","min":1,"max":3,"chance":1}]}',
    "building_defs": '{"code":"watchtower","scope":"home","kind":"alarm","name":"برج دیده‌بانی","cost":{"wood":20},"effects":{"alarm":2}}',
    "animal_breeds": '{"code":"bear_cub","name":"توله خرس","species":"bear","price":600,"perks":{"guard":2}}',
    "hireling_defs": '{"code":"scout","name":"دیده‌بان","role":"patrol","hp":40,"power":5,"wage":9,"price":150}',
    "mission_defs": '{"code":"m_stone","name":"تأمین سنگ","kind":"deliver","target":"stone","amount":8,"reward_coins":35}',
}
LOCKED = {"id", "code"}


def _need(ctx, user):
    if user["telegram_id"] not in ctx.config.admin_ids:
        raise GameError("این بخش فقط برای ادمین است.")


@action("ad")
async def a_admin(ctx, conn, user, ch, x, y):
    _need(ctx, user)
    return Screen("🛠 <b>پنل مدیریت</b>", [
        [B("📊 آمار", "ad_st"), B("⚙️ تنظیمات بازی", "ad_cfg")],
        [B("📦 محتوا (آیتم، حیوان...)", "ad_ct"), B("👥 کاربران", "ad_us")],
        [B("🚨 گزارش‌های تخلف", "ad_rp"), B("🏆 فصل", "ad_ss")],
        [B("🎪 رویداد و گروه‌ها", "ad_ev"), B("💾 پشتیبان‌گیری", "ad_bk")]], back="main")


STAT_SQL = [
    ("کاربران", "SELECT count(*) FROM users"),
    ("شخصیت‌ها", "SELECT count(*) FROM characters"),
    ("فعال در ۲۴ ساعت", "SELECT count(*) FROM users WHERE last_seen_at > now() - interval '1 day'"),
    ("قبایل", "SELECT count(*) FROM tribes"),
    ("اسیرهای فعال", "SELECT count(*) FROM captivities WHERE status = 'active'"),
    ("نبردهای باز", "SELECT count(*) FROM combats WHERE state <> 'closed'"),
    ("آگهی‌های بازار", "SELECT count(*) FROM market_listings WHERE status = 'active'"),
    ("کل سکه در گردش", "SELECT COALESCE(sum(coins), 0) FROM users"),
]


@action("ad_st")
async def a_stats(ctx, conn, user, ch, x, y):
    _need(ctx, user)
    lines = ["📊 <b>آمار</b>", ""]
    for title, sql in STAT_SQL:
        lines.append(f"{title}: {fa_num(await conn.fetchval(sql))}")
    return Screen("\n".join(lines), [[B("🔄 بروزرسانی", "ad_st")]], back="ad")


# ───────── تنظیمات ─────────
@action("ad_cfg")
async def a_cfg(ctx, conn, user, ch, x, y):
    _need(ctx, user)
    cats = {}
    for k in DEFAULTS:
        cats.setdefault(k.split(".")[0], []).append(k)
    if not x:
        items = sorted(cats.items())
        rows = [[B(f"{c} ({fa_num(len(v))})", "ad_cfg", c) for c, v in items[i:i + 2]] for i in range(0, len(items), 2)]
        return Screen("⚙️ <b>تنظیمات بازی</b>\nدسته را انتخاب کن:", rows, back="ad")
    page = int(y or 0)
    keys = cats.get(x, [])
    chunk = keys[page * 8:(page + 1) * 8]
    lines = [f"⚙️ <b>{esc(x)}</b>", ""]
    rows = []
    for k in chunk:
        val = json.dumps(ctx.settings.get(k), ensure_ascii=False)
        lines.append(f"• {esc(DEFAULTS[k][1])}: <b>{esc(val[:40])}</b>")
        rows.append([B(f"{DEFAULTS[k][1][:30]} = {val[:14]}", "ad_ck", k)])
    nav = []
    if page > 0:
        nav.append(B("◀️", "ad_cfg", x, page - 1))
    if (page + 1) * 8 < len(keys):
        nav.append(B("▶️", "ad_cfg", x, page + 1))
    if nav:
        rows.append(nav)
    return Screen("\n".join(lines), rows, back="ad_cfg")


@action("ad_ck")
async def a_cfg_key(ctx, conn, user, ch, x, y, note=""):
    _need(ctx, user)
    d = DEFAULTS[x]
    text = (f"⚙️ <code>{esc(x)}</code>\n{esc(d[1])}\n\nمقدار فعلی: <b>{esc(json.dumps(ctx.settings.get(x), ensure_ascii=False))}</b>\n"
            f"پیش‌فرض: {esc(json.dumps(d[0], ensure_ascii=False))}")
    return Screen(text, [[B("✏️ تغییر مقدار", "ad_cs", x), B("↩️ بازگشت به پیش‌فرض", "ad_cr", x)]], back="ad_cfg", note=note)


@action("ad_cs")
async def a_cfg_set(ctx, conn, user, ch, x, y):
    _need(ctx, user)
    return await ask(conn, user["id"], "ad_set", {"key": x},
                     f"مقدار جدید «{esc(DEFAULTS[x][1])}» را بنویس (عدد، true/false یا JSON):\nپیش‌فرض: {esc(json.dumps(DEFAULTS[x][0], ensure_ascii=False))}", back="ad_ck")


@text_input("ad_set")
async def i_cfg_set(ctx, conn, user, ch, text, data):
    _need(ctx, user)
    try:
        value = json.loads(text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
        await ctx.settings.set(data["key"], value, actor=f"tg:{user['telegram_id']}")
    except (json.JSONDecodeError, SettingsError) as exc:
        raise GameError(f"مقدار نامعتبر: {exc}") from exc
    await clear_state(conn, user["id"])
    return Screen(f"✅ ذخیره شد: <b>{esc(json.dumps(value, ensure_ascii=False))}</b>", [[B("⚙️ ادامه", "ad_ck", data["key"])]], back="ad_cfg")


@action("ad_cr")
async def a_cfg_reset(ctx, conn, user, ch, x, y):
    _need(ctx, user)
    await ctx.settings.set(x, DEFAULTS[x][0], actor=f"tg:{user['telegram_id']}")
    return await a_cfg_key(ctx, conn, user, ch, x, "", note="↩️ به پیش‌فرض برگشت.")


# ───────── محتوا ─────────
@action("ad_ct")
async def a_content(ctx, conn, user, ch, x, y):
    _need(ctx, user)
    return Screen("📦 <b>محتوا</b>\nبدون دست‌زدن به کد، همه چیز اینجا قابل‌ویرایش است. حذف = غیرفعال‌کردن (برای بازیکنانی که دارند دست‌نخورده می‌ماند).",
                  [[B(t, "ad_cl", k)] for k, (_, t) in TABLES.items()], back="ad")


@action("ad_cl")
async def a_content_list(ctx, conn, user, ch, x, y):
    _need(ctx, user)
    tbl = TABLES[x][0]
    page = int(y or 0)
    rows_db = await conn.fetch(f"SELECT code, name, is_active FROM {tbl} ORDER BY id OFFSET $1 LIMIT 9", page * 8)
    rows = [[B(("✅ " if r["is_active"] else "⛔️ ") + f"{r['name']} ({r['code']})"[:40], "ad_ci", x, r["code"])] for r in rows_db[:8]]
    nav = []
    if page > 0:
        nav.append(B("◀️", "ad_cl", x, page - 1))
    if len(rows_db) > 8:
        nav.append(B("▶️", "ad_cl", x, page + 1))
    if nav:
        rows.append(nav)
    rows.append([B("➕ مورد جدید", "ad_cn", x)])
    return Screen(f"📦 <b>{TABLES[x][1]}</b>", rows, back="ad_ct")


async def _row(conn, x, code):
    r = await conn.fetchrow(f"SELECT * FROM {TABLES[x][0]} WHERE code=$1", code)
    if r is None:
        raise GameError("پیدا نشد.")
    return r


@action("ad_ci")
async def a_content_item(ctx, conn, user, ch, x, y, note=""):
    _need(ctx, user)
    r = await _row(conn, x, y)
    lines = [f"📦 <b>{esc(r['name'])}</b> (<code>{esc(y)}</code>) — {'✅ فعال' if r['is_active'] else '⛔️ غیرفعال'}", ""]
    for k, v in dict(r).items():
        if k in ("id", "code", "name", "is_active", "description"):
            continue
        lines.append(f"{k}: <code>{esc(json.dumps(v if not isinstance(v, Decimal) else float(v), ensure_ascii=False)[:60])}</code>")
    rows = [[B("✏️ ویرایش فیلد", "ad_cf", x, y), B("⛔️ غیرفعال" if r["is_active"] else "✅ فعال‌سازی", "ad_ctg", x, y)]]
    return Screen("\n".join(lines)[:3500], rows, back="ad_cl", note=note)


@action("ad_ctg")
async def a_content_toggle(ctx, conn, user, ch, x, y):
    _need(ctx, user)
    await conn.execute(f"UPDATE {TABLES[x][0]} SET is_active = NOT is_active WHERE code=$1", y)
    await conn.execute("INSERT INTO audit_log(actor, action, data) VALUES($1,'content_toggle',$2::jsonb)",
                       f"tg:{user['telegram_id']}", json.dumps({"t": x, "code": y}))
    return await a_content_item(ctx, conn, user, ch, x, y, note="انجام شد.")


async def _cols(conn, tbl):
    rows = await conn.fetch("SELECT column_name, data_type FROM information_schema.columns WHERE table_name=$1 ORDER BY ordinal_position", tbl)
    return {r["column_name"]: r["data_type"] for r in rows}


@action("ad_cf")
async def a_content_fields(ctx, conn, user, ch, x, y):
    _need(ctx, user)
    cols = [c for c in await _cols(conn, TABLES[x][0]) if c not in LOCKED and c != "is_active"]
    return Screen("کدام فیلد را ویرایش کنم؟", [[B(c, "ad_cfs", x, f"{y}:{c}")] for c in cols], back="ad_ci")


@action("ad_cfs")
async def a_content_field_pick(ctx, conn, user, ch, x, y):
    _need(ctx, user)
    code, col = y.split(":", 1)
    r = await _row(conn, x, code)
    cur = r[col]
    return await ask(conn, user["id"], "ad_edit", {"t": x, "code": code, "col": col},
                     f"مقدار جدید <code>{esc(col)}</code> را بنویس (عدد، متن داخل \" \" یا JSON).\nفعلی: <code>{esc(json.dumps(cur if not isinstance(cur, Decimal) else float(cur), ensure_ascii=False)[:200])}</code>",
                     back="ad_ci")


@text_input("ad_edit")
async def i_content_edit(ctx, conn, user, ch, text, data):
    _need(ctx, user)
    tbl = TABLES[data["t"]][0]
    cols = await _cols(conn, tbl)
    raw = text.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    try:
        val = json.loads(raw)
    except json.JSONDecodeError:
        val = raw        # متن ساده بدون گیومه
    dtype = cols[data["col"]]
    cast = ""
    if dtype == "jsonb":
        val, cast = json.dumps(val, ensure_ascii=False), "::jsonb"
    elif dtype == "numeric":
        val = Decimal(str(val))
    try:
        await conn.execute(f"UPDATE {tbl} SET {data['col']}=$2{cast} WHERE code=$1", data["code"], val)
    except Exception as exc:  # noqa: BLE001
        raise GameError(f"مقدار نامعتبر برای این فیلد: {str(exc)[:80]}") from exc
    await conn.execute("INSERT INTO audit_log(actor, action, data) VALUES($1,'content_edit',$2::jsonb)",
                       f"tg:{user['telegram_id']}", json.dumps({"t": tbl, "code": data["code"], "f": data["col"], "v": raw[:100]}))
    await clear_state(conn, user["id"])
    return Screen("✅ ذخیره شد.", [[B("📦 ادامه", "ad_ci", data["t"], data["code"])]], back="ad_cl")


@action("ad_cn")
async def a_content_new(ctx, conn, user, ch, x, y):
    _need(ctx, user)
    return await ask(conn, user["id"], "ad_new", {"t": x},
                     f"مورد جدید را به‌صورت JSON بنویس. مثال:\n<code>{esc(NEW_EXAMPLE[TABLES[x][0]])}</code>", back="ad_cl")


@text_input("ad_new")
async def i_content_new(ctx, conn, user, ch, text, data):
    _need(ctx, user)
    tbl = TABLES[data["t"]][0]
    cols = await _cols(conn, tbl)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GameError("JSON نامعتبر است.") from exc
    if not isinstance(obj, dict) or "code" not in obj or any(k not in cols or k == "id" for k in obj):
        raise GameError("باید یک شیء JSON با فیلد code و فقط فیلدهای معتبر باشد.")
    names, ph, vals = [], [], []
    for k, v in obj.items():
        if cols[k] == "jsonb":
            vals.append(json.dumps(v, ensure_ascii=False))
            ph.append(f"${len(vals)}::jsonb")
        elif cols[k] == "numeric":
            vals.append(Decimal(str(v)))
            ph.append(f"${len(vals)}")
        else:
            vals.append(v)
            ph.append(f"${len(vals)}")
        names.append(k)
    try:
        await conn.execute(f"INSERT INTO {tbl} ({', '.join(names)}) VALUES ({', '.join(ph)})", *vals)
    except Exception as exc:  # noqa: BLE001
        raise GameError(f"ساخته نشد: {str(exc)[:100]}") from exc
    await clear_state(conn, user["id"])
    return Screen("✅ ساخته شد.", [[B("📦 مشاهده", "ad_ci", data["t"], obj["code"])]], back="ad_cl")


# ───────── کاربران ─────────
@action("ad_us")
async def a_users(ctx, conn, user, ch, x, y):
    _need(ctx, user)
    return await ask(conn, user["id"], "ad_usq", {}, "نام شخصیت یا آیدی عددی تلگرام کاربر را بنویس:", back="ad")


async def _find(conn, q: str):
    q = q.strip()
    if q.lstrip("-").isdigit():
        r = await conn.fetchrow("SELECT id FROM users WHERE telegram_id=$1", int(q))
        if r:
            return r["id"]
    r = await conn.fetchrow("SELECT user_id FROM characters WHERE lower(name)=lower($1)", q)
    if r is None:
        raise GameError("کاربری پیدا نشد.")
    return r["user_id"]


async def _user_card(ctx, conn, uid: int, note: str = "") -> Screen:
    u = await conn.fetchrow("SELECT * FROM users WHERE id=$1", uid)
    c = await conn.fetchrow("SELECT name, level, location, health FROM characters WHERE user_id=$1", uid)
    text = (f"👤 <b>{esc(c['name'] if c else '—')}</b> — tg:<code>{u['telegram_id']}</code> @{esc(u['username'] or '—')}\n"
            f"🪙 {fa_num(u['coins'])} (قفل: {fa_num(u['locked_coins'])})   سطح حساب {fa_num(u['account_level'])}\n"
            + (f"سطح شخصیت {fa_num(c['level'])} — {c['location']} — ❤️ {fa_num(c['health'])}\n" if c else "")
            + ("⛔️ مسدود" if u["is_banned"] else "✅ فعال"))
    rows = [[B("✅ رفع مسدودی" if u["is_banned"] else "⛔️ مسدودسازی", "ad_ub", uid), B("💰 تغییر سکه", "ad_uc", uid)],
            [B("🎁 دادن آیتم", "ad_ui", uid), B("📜 تراکنش‌ها", "ad_ux", uid)]]
    return Screen(text, rows, back="ad", note=note)


@text_input("ad_usq")
async def i_user_query(ctx, conn, user, ch, text, data):
    _need(ctx, user)
    uid = await _find(conn, text)
    await clear_state(conn, user["id"])
    return await _user_card(ctx, conn, uid)


@action("ad_ud")
async def a_user_detail(ctx, conn, user, ch, x, y):
    _need(ctx, user)
    return await _user_card(ctx, conn, int(x))


@action("ad_ub")
async def a_user_ban(ctx, conn, user, ch, x, y):
    _need(ctx, user)
    cur = await conn.fetchval("SELECT is_banned FROM users WHERE id=$1", int(x))
    await conn.execute("UPDATE users SET is_banned=$2 WHERE id=$1", int(x), not cur)
    await conn.execute("INSERT INTO audit_log(actor, action, data) VALUES($1,$2,$3::jsonb)", f"tg:{user['telegram_id']}",
                       "unban" if cur else "ban", json.dumps({"user": int(x)}))
    return await _user_card(ctx, conn, int(x), "انجام شد.")


@action("ad_uc")
async def a_user_coins(ctx, conn, user, ch, x, y):
    _need(ctx, user)
    return await ask(conn, user["id"], "ad_coins", {"uid": int(x)}, "مقدار سکه را بنویس (برای کم‌کردن عدد منفی، مثل -50):", back="ad")


@text_input("ad_coins")
async def i_user_coins(ctx, conn, user, ch, text, data):
    _need(ctx, user)
    t = text.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    if not t.lstrip("-").isdigit():
        raise GameError("یک عدد بفرست.")
    try:
        bal = await change_coins(conn, data["uid"], int(t), "admin_grant", ref=f"tg:{user['telegram_id']}")
    except Exception as exc:  # noqa: BLE001
        raise GameError("موجودی کافی نیست.") from exc
    await clear_state(conn, user["id"])
    return await _user_card(ctx, conn, data["uid"], f"✅ موجودی جدید: {bal}")


@action("ad_ui")
async def a_user_item(ctx, conn, user, ch, x, y):
    _need(ctx, user)
    return await ask(conn, user["id"], "ad_item", {"uid": int(x)}, "کد آیتم و تعداد را بنویس، مثل: <code>sword 1</code>", back="ad")


@text_input("ad_item")
async def i_user_item(ctx, conn, user, ch, text, data):
    _need(ctx, user)
    parts = text.split()
    qty = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
    d = await inv.def_by_code(conn, parts[0])
    if d["stackable"]:
        await inv.add_stack(conn, "storage", data["uid"], d["id"], qty, "admin_grant")
    else:
        for _ in range(min(qty, 20)):
            await inv.create_instance(conn, d["id"], data["uid"], "storage", data["uid"], "admin_grant")
    await clear_state(conn, user["id"])
    return await _user_card(ctx, conn, data["uid"], f"✅ {esc(d['name'])} به انبار خانه‌اش اضافه شد.")


@action("ad_ux")
async def a_user_tx(ctx, conn, user, ch, x, y):
    _need(ctx, user)
    rows = await conn.fetch("SELECT delta, balance_after, reason, created_at FROM coin_ledger WHERE user_id=$1 ORDER BY id DESC LIMIT 15", int(x))
    text = "\n".join(f"{r['created_at']:%m-%d %H:%M} {r['delta']:+d} → {r['balance_after']} ({esc(r['reason'])})" for r in rows) or "تراکنشی نیست"
    return Screen("📜 <b>تراکنش‌ها</b>\n" + text, [], back="ad_ud")


# ───────── گزارش تخلف ─────────
@action("ad_rp")
async def a_reports(ctx, conn, user, ch, x, y):
    _need(ctx, user)
    rows_db = await conn.fetch("SELECT r.id, r.text, c.name FROM violation_reports r JOIN characters c ON c.user_id=r.reporter_id "
                               "WHERE r.status='open' ORDER BY r.id LIMIT 8")
    lines = [f"#{r['id']} از {esc(r['name'])}:\n{esc(r['text'][:200])}" for r in rows_db] or ["گزارش بازی نیست."]
    return Screen("🚨 <b>گزارش‌های تخلف</b>\n\n" + "\n\n".join(lines), [[B(f"✅ بستن #{r['id']}", "ad_rr", r["id"])] for r in rows_db], back="ad")


@action("ad_rr")
async def a_report_close(ctx, conn, user, ch, x, y):
    _need(ctx, user)
    await conn.execute("UPDATE violation_reports SET status='closed' WHERE id=$1", int(x))
    return await a_reports(ctx, conn, user, ch, "", "")


# ───────── فصل ─────────
@action("ad_ss")
async def a_season(ctx, conn, user, ch, x, y):
    _need(ctx, user)
    s = await events.current_season(conn)
    text = "🏆 <b>فصل</b>\n" + (f"فعلی: {esc(s['name'])} — پایان: {left(s['ends_at'])}" if s else "فصل فعالی نیست.")
    return Screen(text, [[B("➕ فصل جدید", "ad_sn")]], back="ad")


@action("ad_sn")
async def a_season_new(ctx, conn, user, ch, x, y):
    _need(ctx, user)
    return await ask(conn, user["id"], "ad_season", {},
                     "فصل جدید را در یک خط بنویس:\n<code>نام شروع_ساعت مدت_روز ورودیه جایزه۱,جایزه۲,جایزه۳</code>\nمثال: <code>فصل_اول 0 7 50 500,300,100</code>\n(شروع ۰ یعنی همین الان)", back="ad_ss")


@text_input("ad_season")
async def i_season(ctx, conn, user, ch, text, data):
    _need(ctx, user)
    a = text.split()
    try:
        name, start, days, fee = a[0].replace("_", " "), float(a[1]), float(a[2]), int(a[3])
        rewards = [int(v) for v in a[4].split(",")] if len(a) > 4 else []
    except (IndexError, ValueError) as exc:
        raise GameError("قالب نادرست؛ مثال: فصل_اول 0 7 50 500,300,100") from exc
    sid = await events.create_season(conn, name, start, days, fee, rewards)
    await clear_state(conn, user["id"])
    return Screen(f"✅ فصل #{sid} ساخته شد.", [], back="ad_ss")


# ───────── رویداد و گروه‌ها ─────────
@action("ad_ev")
async def a_events(ctx, conn, user, ch, x, y, note=""):
    _need(ctx, user)
    groups = await conn.fetch("SELECT chat_id, title FROM chat_groups ORDER BY added_at")
    rows = []
    for g in groups:
        rows.append([B(f"📦 محموله — {g['title'] or g['chat_id']}"[:40], "ad_evs", g["chat_id"], "cargo"),
                     B("🐗 جانور", "ad_evs", g["chat_id"], "beast")])
    text = ("🎪 <b>رویداد و گروه‌ها</b>\n" + (f"{len(groups)} گروه ثبت شده است." if groups else
            "هنوز گروهی ثبت نشده. ربات را به گروه اضافه کن و داخل گروه بنویس «پنل» تا دکمه ثبت گروه بیاید."))
    return Screen(text, rows, back="ad", note=note)


@action("ad_evs")
async def a_event_spawn(ctx, conn, user, ch, x, y):
    _need(ctx, user)
    from app.bot.handlers.groups import post_event
    await post_event(ctx, int(x), y)
    return await a_events(ctx, conn, user, ch, "", "", note="✅ رویداد در گروه ارسال شد.")


@action("ad_g")
async def a_group_tools(ctx, conn, user, ch, x, y):
    _need(ctx, user)
    return Screen("🛠 <b>ابزار ادمین گروه</b>", [[B("📌 ثبت این گروه برای رویدادها", "ad_gr", x)],
                                                   [B("📦 رویداد محموله", "ad_evs2", x, "cargo"), B("🐗 رویداد جانور", "ad_evs2", x, "beast")]], back=None)


@action("ad_gr")
async def a_group_register(ctx, conn, user, ch, x, y):
    _need(ctx, user)
    await conn.execute("INSERT INTO chat_groups(chat_id) VALUES($1) ON CONFLICT (chat_id) DO UPDATE SET events_enabled=true", int(x))
    await ctx.jobs.enqueue("event_auto", {}, unique_key="event_auto:next")
    return Screen("✅ این گروه برای رویدادهای بازی ثبت شد.", [], back=None)


@action("ad_evs2")
async def a_group_event(ctx, conn, user, ch, x, y):
    _need(ctx, user)
    from app.bot.handlers.groups import post_event
    await post_event(ctx, int(x), y)
    return Screen("✅ رویداد ارسال شد.", [], back=None)


@action("ad_bk")
async def a_backup(ctx, conn, user, ch, x, y):
    _need(ctx, user)
    from app.bot.handlers.admin import send_backup
    await send_backup(ctx, [user["telegram_id"]])
    return Screen("💾 فایل پشتیبان برایت فرستاده شد. آن را جای امنی نگه دار.", [], back="ad")
