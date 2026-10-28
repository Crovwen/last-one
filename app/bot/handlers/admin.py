"""پنل مدیریت داخل تلگرام (فقط ADMIN_IDS): محتوا، کاربران، تراکنش‌ها، فصل، رویداد، گزارش‌ها."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from html import escape

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, Message

from app.bot.handlers.groups import post_event
from app.context import AppContext
from app.services import backup as backup_service
from app.services import events
from app.services.economy import change_coins
from app.services.errors import GameError
from app.services.settings import DEFAULTS, SettingsError

router = Router(name="admin")

TABLES = {"items": "item_defs", "activities": "activity_defs", "buildings": "building_defs", "breeds": "animal_breeds",
          "hirelings": "hireling_defs", "missions": "mission_defs"}
LOCKED_COLS = {"id", "code"}


def _is_admin(m: Message, ctx: AppContext) -> bool:
    return bool(m.from_user and m.from_user.id in ctx.config.admin_ids)


def _args(command: CommandObject) -> list:
    return (command.args or "").split()


async def _find_user(ctx: AppContext, key: str):
    if key.lstrip("-").isdigit():
        r = await ctx.pool.fetchrow("SELECT u.* FROM users u WHERE u.telegram_id=$1", int(key))
        if r:
            return r
    return await ctx.pool.fetchrow(
        "SELECT u.* FROM users u JOIN characters c ON c.user_id=u.id WHERE lower(c.name)=lower($1)", key)


@router.message(Command("admin"))
async def cmd_admin(message: Message, ctx: AppContext):
    if not _is_admin(message, ctx):
        return
    users = await ctx.pool.fetchval("SELECT count(*) FROM users")
    chars = await ctx.pool.fetchval("SELECT count(*) FROM characters")
    tribes = await ctx.pool.fetchval("SELECT count(*) FROM tribes")
    rep = await ctx.pool.fetchval("SELECT count(*) FROM violation_reports WHERE status='open'")
    await message.answer(
        "🛠 <b>پنل مدیریت</b>\n"
        f"کاربران: {users} | شخصیت‌ها: {chars} | قبایل: {tribes} | گزارش باز: {rep}\n\n"
        "<b>تنظیمات</b>: /settings — /set کلید مقدار\n"
        "<b>محتوا</b>: /content جدول — /show جدول کد — /edit جدول کد فیلد مقدار — /new جدول {json} — /disable و /enable جدول کد\n"
        "(جدول‌ها: items activities buildings breeds hirelings missions)\n"
        "<b>کاربران</b>: /user نام — /ban /unban نام — /give نام سکه — /giveitem نام کدآیتم تعداد — /tx نام\n"
        "<b>گزارش‌ها</b>: /reports — /resolve شماره\n"
        "<b>فصل</b>: /season_new نام شروع_ساعت مدت_روز ورودیه جایزه۱,جایزه۲,...\n"
        "<b>رویداد/گروه</b>: /setgroup (داخل گروه) — /event cargo|beast (داخل گروه)\n"
        "<b>پشتیبان</b>: /backup")


@router.message(Command("settings"))
async def cmd_settings(message: Message, ctx: AppContext):
    if not _is_admin(message, ctx):
        return
    chunks, cur = [], "⚙️ <b>تنظیمات</b>"
    for key, (_, desc) in DEFAULTS.items():
        line = f"\n<code>{key}</code> = <b>{escape(json.dumps(ctx.settings.get(key), ensure_ascii=False))}</b> — {desc}"
        if len(cur) + len(line) > 3800:
            chunks.append(cur)
            cur = ""
        cur += line
    chunks.append(cur)
    for c in chunks:
        await message.answer(c)


@router.message(Command("set"))
async def cmd_set(message: Message, command: CommandObject, ctx: AppContext):
    if not _is_admin(message, ctx):
        return
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) != 2:
        return await message.answer("مثال: /set economy.start_coins 150")
    try:
        value = json.loads(parts[1])
        await ctx.settings.set(parts[0], value, actor=f"tg:{message.from_user.id}")
    except (json.JSONDecodeError, SettingsError) as exc:
        return await message.answer(f"⚠️ {escape(str(exc))}")
    await message.answer(f"✅ <code>{escape(parts[0])}</code> = <b>{escape(json.dumps(value, ensure_ascii=False))}</b>")


@router.message(Command("backup"))
async def cmd_backup(message: Message, ctx: AppContext):
    if _is_admin(message, ctx):
        await send_backup(ctx, [message.from_user.id])


async def send_backup(ctx: AppContext, chat_ids) -> None:
    data = await backup_service.make_backup(ctx.pool)
    name = "lastone-backup-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M") + ".json.gz"
    for chat_id in chat_ids:
        if len(data) > backup_service.MAX_TELEGRAM_FILE:
            await ctx.bot.send_message(chat_id, "⚠️ فایل پشتیبان از حد تلگرام بزرگ‌تر شد؛ روش پشتیبان‌گیری باید ارتقا یابد.")
        else:
            await ctx.bot.send_document(chat_id, BufferedInputFile(data, filename=name), caption="💾 پشتیبان دیتابیس")


# ───────── محتوا ─────────
def _table(arg: str) -> str:
    if arg not in TABLES:
        raise GameError("جدول نامعتبر: " + " ".join(TABLES))
    return TABLES[arg]


@router.message(Command("content"))
async def cmd_content(message: Message, command: CommandObject, ctx: AppContext):
    if not _is_admin(message, ctx):
        return
    try:
        tbl = _table((_args(command) or [""])[0])
    except GameError as exc:
        return await message.answer(str(exc))
    rows = await ctx.pool.fetch(f"SELECT code, name, is_active FROM {tbl} ORDER BY id")
    text = "\n".join(f"{'✅' if r['is_active'] else '⛔️'} <code>{r['code']}</code> — {escape(r['name'])}" for r in rows)
    await message.answer(text[:4000] or "خالی")


@router.message(Command("show"))
async def cmd_show(message: Message, command: CommandObject, ctx: AppContext):
    if not _is_admin(message, ctx):
        return
    a = _args(command)
    try:
        tbl = _table(a[0])
        row = await ctx.pool.fetchrow(f"SELECT * FROM {tbl} WHERE code=$1", a[1])
    except (GameError, IndexError):
        return await message.answer("مثال: /show items sword")
    if row is None:
        return await message.answer("پیدا نشد.")
    await message.answer("<pre>" + escape(json.dumps({k: (str(v) if isinstance(v, Decimal) else v) for k, v in dict(row).items()},
                                                     ensure_ascii=False, indent=1, default=str))[:3800] + "</pre>")


async def _cols(ctx: AppContext, tbl: str) -> dict:
    rows = await ctx.pool.fetch("SELECT column_name, data_type FROM information_schema.columns WHERE table_name=$1", tbl)
    return {r["column_name"]: r["data_type"] for r in rows}


def _cast(dtype: str, value):
    if dtype == "jsonb":
        return json.dumps(value, ensure_ascii=False), "::jsonb"
    if dtype == "numeric":
        return Decimal(str(value)), ""
    return value, ""


@router.message(Command("edit"))
async def cmd_edit(message: Message, command: CommandObject, ctx: AppContext):
    if not _is_admin(message, ctx):
        return
    parts = (command.args or "").split(maxsplit=3)
    if len(parts) != 4:
        return await message.answer("مثال: /edit items sword power 15")
    try:
        tbl = _table(parts[0])
        cols = await _cols(ctx, tbl)
        if parts[2] not in cols or parts[2] in LOCKED_COLS:
            raise GameError("فیلد نامعتبر یا قفل.")
        val, cast = _cast(cols[parts[2]], json.loads(parts[3]))
        r = await ctx.pool.execute(f"UPDATE {tbl} SET {parts[2]}=$2{cast} WHERE code=$1", parts[1], val)
        await ctx.pool.execute("INSERT INTO audit_log(actor, action, data) VALUES($1,'content_edit',$2::jsonb)",
                               f"tg:{message.from_user.id}", json.dumps({"t": tbl, "code": parts[1], "f": parts[2], "v": parts[3]}))
    except (GameError, json.JSONDecodeError, ValueError) as exc:
        return await message.answer("⚠️ " + escape(str(exc)))
    await message.answer("✅ انجام شد." if not r.endswith("0") else "کدی با این نام پیدا نشد.")


@router.message(Command("new"))
async def cmd_new(message: Message, command: CommandObject, ctx: AppContext):
    if not _is_admin(message, ctx):
        return
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) != 2:
        return await message.answer('مثال: /new items {"code":"club","name":"گرز","kind":"weapon","power":9}')
    try:
        tbl = _table(parts[0])
        data = json.loads(parts[1])
        cols = await _cols(ctx, tbl)
        if "code" not in data or any(k not in cols or k == "id" for k in data):
            raise GameError("فیلدهای نامعتبر یا code ندارد.")
        names, ph, vals = [], [], []
        for k, v in data.items():
            val, cast = _cast(cols[k], v)
            names.append(k)
            vals.append(val)
            ph.append(f"${len(vals)}{cast}")
        await ctx.pool.execute(f"INSERT INTO {tbl} ({', '.join(names)}) VALUES ({', '.join(ph)})", *vals)
    except Exception as exc:  # noqa: BLE001
        return await message.answer("⚠️ " + escape(str(exc)[:300]))
    await message.answer("✅ ساخته شد.")


@router.message(Command("disable"))
@router.message(Command("enable"))
async def cmd_toggle(message: Message, command: CommandObject, ctx: AppContext):
    if not _is_admin(message, ctx):
        return
    a = _args(command)
    on = (message.text or "").startswith("/enable")
    try:
        tbl = _table(a[0])
        await ctx.pool.execute(f"UPDATE {tbl} SET is_active=$2 WHERE code=$1", a[1], on)
    except (GameError, IndexError):
        return await message.answer("مثال: /disable items sword")
    await message.answer("✅ " + ("فعال شد" if on else "غیرفعال شد (برای بازیکنانی که دارند، دست‌نخورده می‌ماند)"))


# ───────── کاربران ─────────
@router.message(Command("user"))
async def cmd_user(message: Message, command: CommandObject, ctx: AppContext):
    if not _is_admin(message, ctx):
        return
    u = await _find_user(ctx, (command.args or "").strip())
    if u is None:
        return await message.answer("پیدا نشد.")
    ch = await ctx.pool.fetchrow("SELECT name, level, location, health FROM characters WHERE user_id=$1", u["id"])
    await message.answer(f"👤 <b>{escape(ch['name'] if ch else '—')}</b> tg:<code>{u['telegram_id']}</code>\n🪙 {u['coins']} (قفل {u['locked_coins']})  "
                         f"سطح حساب {u['account_level']}  {'⛔️ مسدود' if u['is_banned'] else ''}\n"
                         + (f"سطح شخصیت {ch['level']} — {ch['location']} — ❤️ {ch['health']}" if ch else ""))


@router.message(Command("ban"))
@router.message(Command("unban"))
async def cmd_ban(message: Message, command: CommandObject, ctx: AppContext):
    if not _is_admin(message, ctx):
        return
    u = await _find_user(ctx, (command.args or "").strip())
    if u is None:
        return await message.answer("پیدا نشد.")
    ban = (message.text or "").startswith("/ban")
    await ctx.pool.execute("UPDATE users SET is_banned=$2 WHERE id=$1", u["id"], ban)
    await ctx.pool.execute("INSERT INTO audit_log(actor, action, data) VALUES($1,$2,$3::jsonb)", f"tg:{message.from_user.id}",
                           "ban" if ban else "unban", json.dumps({"user": u["id"]}))
    await message.answer("✅ انجام شد.")


@router.message(Command("give"))
async def cmd_give(message: Message, command: CommandObject, ctx: AppContext):
    if not _is_admin(message, ctx):
        return
    a = _args(command)
    if len(a) != 2 or not a[1].lstrip("-").isdigit():
        return await message.answer("مثال: /give نام 100")
    u = await _find_user(ctx, a[0])
    if u is None:
        return await message.answer("پیدا نشد.")
    try:
        async with ctx.pool.acquire() as conn:
            async with conn.transaction():
                bal = await change_coins(conn, u["id"], int(a[1]), "admin_grant", ref=f"tg:{message.from_user.id}")
    except Exception as exc:  # noqa: BLE001
        return await message.answer("⚠️ " + escape(str(exc) or "موجودی کافی نیست"))
    await message.answer(f"✅ موجودی جدید: {bal}")


@router.message(Command("giveitem"))
async def cmd_giveitem(message: Message, command: CommandObject, ctx: AppContext):
    if not _is_admin(message, ctx):
        return
    a = _args(command)
    if len(a) < 2:
        return await message.answer("مثال: /giveitem نام sword 1")
    u = await _find_user(ctx, a[0])
    if u is None:
        return await message.answer("پیدا نشد.")
    qty = int(a[2]) if len(a) > 2 and a[2].isdigit() else 1
    from app.services import inventory as inv
    try:
        async with ctx.pool.acquire() as conn:
            async with conn.transaction():
                d = await inv.def_by_code(conn, a[1])
                if d["stackable"]:
                    await inv.add_stack(conn, "storage", u["id"], d["id"], qty, "admin_grant")
                else:
                    for _ in range(qty):
                        await inv.create_instance(conn, d["id"], u["id"], "storage", u["id"], "admin_grant")
    except GameError as exc:
        return await message.answer("⚠️ " + str(exc))
    await message.answer("✅ به انبار خانه‌اش اضافه شد.")


@router.message(Command("tx"))
async def cmd_tx(message: Message, command: CommandObject, ctx: AppContext):
    if not _is_admin(message, ctx):
        return
    u = await _find_user(ctx, (command.args or "").strip())
    if u is None:
        return await message.answer("پیدا نشد.")
    rows = await ctx.pool.fetch("SELECT delta, balance_after, reason, created_at FROM coin_ledger WHERE user_id=$1 ORDER BY id DESC LIMIT 15", u["id"])
    await message.answer("\n".join(f"{r['created_at']:%m-%d %H:%M} {r['delta']:+d} → {r['balance_after']} ({r['reason']})" for r in rows) or "تراکنشی نیست")


# ───────── گزارش تخلف، فصل، رویداد ─────────
@router.message(Command("reports"))
async def cmd_reports(message: Message, ctx: AppContext):
    if not _is_admin(message, ctx):
        return
    rows = await ctx.pool.fetch("SELECT r.id, r.text, c.name FROM violation_reports r JOIN characters c ON c.user_id=r.reporter_id "
                                "WHERE r.status='open' ORDER BY r.id LIMIT 15")
    await message.answer("\n\n".join(f"#{r['id']} از {escape(r['name'])}:\n{escape(r['text'])}" for r in rows) or "گزارش بازی نیست.")


@router.message(Command("resolve"))
async def cmd_resolve(message: Message, command: CommandObject, ctx: AppContext):
    if _is_admin(message, ctx) and (command.args or "").strip().isdigit():
        await ctx.pool.execute("UPDATE violation_reports SET status='closed' WHERE id=$1", int(command.args.strip()))
        await message.answer("✅ بسته شد.")


@router.message(Command("season_new"))
async def cmd_season(message: Message, command: CommandObject, ctx: AppContext):
    if not _is_admin(message, ctx):
        return
    a = _args(command)
    try:
        name, start, days, fee = a[0].replace("_", " "), float(a[1]), float(a[2]), int(a[3])
        rewards = [int(x) for x in a[4].split(",")] if len(a) > 4 else []
    except (IndexError, ValueError):
        return await message.answer("مثال: /season_new فصل_اول 0 7 50 500,300,100")
    async with ctx.pool.acquire() as conn:
        async with conn.transaction():
            sid = await events.create_season(conn, name, start, days, fee, rewards)
    await message.answer(f"✅ فصل #{sid} ساخته شد.")


@router.message(Command("setgroup"))
async def cmd_setgroup(message: Message, ctx: AppContext):
    if not _is_admin(message, ctx) or message.chat.type not in ("group", "supergroup"):
        return
    await ctx.pool.execute("INSERT INTO chat_groups(chat_id, title) VALUES($1,$2) ON CONFLICT (chat_id) DO UPDATE SET events_enabled=true",
                           message.chat.id, message.chat.title)
    await message.answer("✅ این گروه برای رویدادهای بازی ثبت شد.")
    await ctx.jobs.enqueue("event_auto", {}, unique_key="event_auto:next")


@router.message(Command("event"))
async def cmd_event(message: Message, command: CommandObject, ctx: AppContext):
    if not _is_admin(message, ctx) or message.chat.type not in ("group", "supergroup"):
        return
    kind = (command.args or "").strip() or None
    try:
        await post_event(ctx, message.chat.id, kind if kind in ("cargo", "beast") else None)
    except GameError as exc:
        await message.answer("⚠️ " + str(exc))
