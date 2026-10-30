"""متن‌های داخل گروه: دستورهای ریپلای (حمله، حمله به خانه، گروگانگیری، خفتگیری)، کلمه‌های منو، پاسخ‌های ورودی."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.bot import combat_ui
from app.bot.commands import KEYWORDS, SLASH, norm, open_action
from app.bot.ui import INPUTS, render
from app.context import AppContext
from app.runtime import with_user
from app.services import combat, mug, party
from app.services.chars import jload
from app.services.errors import GameError

log = logging.getLogger(__name__)
router = Router(name="group_text")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))


def parse_combat_command(t: str):
    """متن نرمال‌شده → (نوع، فهرست یوزرنیم‌ها) یا None."""
    if t in ("حمله", "/attack"):
        return "attack", []
    if t == "خفتگیری":
        return "mug", []
    for prefix, kind in (("حمله به خانه", "raid"), ("گروگانگیری", "hostage")):
        if t.startswith(prefix):
            rest = t[len(prefix):].strip()
            if rest and not rest.startswith("با") and not rest.startswith("@"):
                return None
            return kind, party.parse_mentions(rest)
    return None


async def _combat_command(message: Message, ctx: AppContext, kind: str, names: list):
    target = message.reply_to_message.from_user if message.reply_to_message else None
    if target is None or target.is_bot:
        return await message.reply("روی پیام بازیکن هدف ریپلای کن.")

    async def fn(conn, user, ch):
        t = await conn.fetchrow("SELECT id FROM users WHERE telegram_id=$1", target.id)
        if t is None:
            raise GameError("این بازیکن هنوز در بازی ثبت‌نام نکرده است.")
        if kind == "mug":
            return await mug.mug(conn, ctx.settings, user["id"], t["id"])
        if kind == "attack":
            return await combat.start_attack(conn, ctx.settings, user["id"], t["id"], message.chat.id, None)
        return await party.start(conn, ctx.settings, user["id"], t["id"], kind, names, message.chat.id)

    try:
        res, msgs = await with_user(ctx, message.from_user.id, fn)
    except GameError as exc:
        return await message.reply("⚠️ " + str(exc))
    except Exception:  # noqa: BLE001
        log.exception("group command failed")
        return await message.reply("⚠️ خطای داخلی؛ دوباره تلاش کن.")
    if kind == "mug":
        return await message.reply(res)
    await combat_ui.send_new(ctx, res["id"], message.chat.id, reply_to=message.reply_to_message.message_id)


@router.message(Command(*SLASH.keys()))
async def on_slash(message: Message, command: CommandObject, ctx: AppContext):
    await _menu(message, ctx, SLASH[command.command.lower()], "")


async def _menu(message: Message, ctx: AppContext, action: str, x: str):
    if action == "ad":      # در گروه، ابزار ادمین گروه نشان داده می‌شود
        action, x = "ad_g", str(message.chat.id)
    try:
        _, text, markup = await open_action(ctx, message.from_user.id, action, x)
    except GameError as exc:
        return await message.reply("⚠️ " + str(exc))
    await message.reply(text, reply_markup=markup)


@router.message(F.text)
async def on_group_text(message: Message, ctx: AppContext):
    t = norm(message.text)
    cmd = parse_combat_command(t)
    if cmd:
        return await _combat_command(message, ctx, cmd[0], cmd[1])
    if t in KEYWORDS:
        action, x = KEYWORDS[t]
        return await _menu(message, ctx, action, x)
    rep = message.reply_to_message
    if rep is not None and rep.from_user is not None and rep.from_user.is_bot:
        async def fn(conn, u, c):
            st = u["state"] or ""
            if c is not None and st.startswith("in:") and st[3:] in INPUTS:
                return await INPUTS[st[3:]](ctx, conn, u, c, message.text, jload(u["state_data"]))
            return None

        try:
            screen, msgs = await with_user(ctx, message.from_user.id, fn, need_char=False)
        except GameError as exc:
            return await message.reply("⚠️ " + str(exc))
        except Exception:  # noqa: BLE001
            log.exception("group input failed")
            return
        if screen is not None:
            text, markup = render(screen, message.from_user.id, msgs)
            await message.reply(text, reply_markup=markup)
