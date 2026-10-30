"""راهنما، فهرست دستورها و محافظ تازه‌وارد."""
from __future__ import annotations

from app.bot.help_fa import COMMANDS, SECTIONS
from app.bot.ui import B, Screen, action, fa_num, left
from app.services.chars import is_protected, now
from app.services.errors import GameError


@action("help")
async def a_help(ctx, conn, user, ch, x, y):
    rows, items = [], list(SECTIONS.items())
    for i in range(0, len(items), 2):
        rows.append([B(t, "help_s", k) for k, (t, _) in items[i:i + 2]])
    rows.append([B("📋 لیست دستورات", "help_cmds")])
    return Screen("📖 <b>راهنمای LAST ONE</b>\nیک بخش را انتخاب کن:", rows)


@action("help_s")
async def a_help_section(ctx, conn, user, ch, x, y):
    if x not in SECTIONS:
        return await a_help(ctx, conn, user, ch, "", "")
    title, text = SECTIONS[x]
    return Screen(f"<b>{title}</b>\n\n{text}", [], back="help")


@action("help_cmds")
async def a_help_cmds(ctx, conn, user, ch, x, y):
    return Screen(COMMANDS, [], back="help")


@action("prot")
async def a_protection(ctx, conn, user, ch, x, y):
    active_period = bool(user["protected_until"] and user["protected_until"] > now())
    if x in ("on", "off"):
        if not active_period:
            raise GameError("محافظ تازه‌وارد تو تمام شده است.")
        await conn.execute("UPDATE users SET protection_off=$2 WHERE id=$1", user["id"], x == "off")
        user = await conn.fetchrow("SELECT * FROM users WHERE id=$1", user["id"])
    if not active_period:
        return Screen("🛡 محافظ تازه‌وارد تو تمام شده است.", [], back="st")
    state = "✅ روشن" if is_protected(user) else "❌ خاموش"
    text = (f"🛡 <b>محافظ تازه‌وارد</b>\nوضعیت: {state}\nزمان باقی‌مانده: {left(user['protected_until'])}\n\n"
            "تا وقتی روشن است کسی نمی‌تواند به تو حمله، گروگانگیری یا خفتگیری کند. اگر خودت حمله کنی برای همیشه تمام می‌شود.")
    rows = [[B("🛡 روشن کردن", "prot", "on")] if user["protection_off"] else [B("⛔️ خاموش کردن", "prot", "off")]]
    return Screen(text, rows, back="st")
