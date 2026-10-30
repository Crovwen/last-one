"""چت خصوصی: ثبت‌نام، منوی اصلی و همه اکشن‌های منو."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from app.bot import screens_admin, screens_core, screens_help, screens_social, screens_world  # noqa: F401  (ثبت اکشن‌ها)
from app.bot import texts_fa as t
from app.bot.combat_ui import send_new
from app.bot.commands import KEYWORDS, SLASH, norm, open_action
from app.bot.ui import ACTIONS, INPUTS, Act, Screen, keyboard, render
from app.context import AppContext
from app.runtime import with_user
from app.services import accounts
from app.services.chars import jload
from app.services.errors import GameError

log = logging.getLogger(__name__)
router = Router(name="private")
router.message.filter(F.chat.type == "private")


_render = render


async def _edit(cb: CallbackQuery, text: str, markup):
    try:
        await cb.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc):
            raise


@router.message(CommandStart())
async def cmd_start(message: Message, ctx: AppContext):
    user, created = await accounts.get_or_create_user(ctx.pool, ctx.settings, message.from_user)
    if user["is_banned"]:
        return await message.answer(t.BANNED)
    ch = await accounts.get_character(ctx.pool, user["id"])
    if ch is None:
        if not created:
            await ctx.pool.execute("UPDATE users SET state=$2 WHERE id=$1", user["id"], accounts.STATE_AWAITING_NAME)
        return await message.answer(t.WELCOME_NEW if created else t.ASK_NAME_AGAIN)

    async def fn(conn, u, c):
        return await screens_core.main_screen(ctx, conn, u, c)

    try:
        screen, msgs = await with_user(ctx, message.from_user.id, fn)
    except GameError as exc:
        return await message.answer("⚠️ " + str(exc))
    text, markup = _render(screen, message.from_user.id, msgs)
    await message.answer(text, reply_markup=markup)


@router.message(Command(*SLASH.keys()))
async def on_slash(message: Message, command: CommandObject, ctx: AppContext):
    try:
        _, text, markup = await open_action(ctx, message.from_user.id, SLASH[command.command.lower()])
    except GameError as exc:
        return await message.answer("⚠️ " + str(exc))
    await message.answer(text, reply_markup=markup)


@router.message(F.text & ~F.text.startswith("/"))
async def on_text(message: Message, ctx: AppContext):
    uid = message.from_user.id
    row = await accounts.get_user(ctx.pool, uid)
    if row is None:
        return await message.answer("/start رو بزن.")

    async def fn(conn, u, c):
        st = u["state"] or ""
        if c is None:
            return None
        if st.startswith("in:") and st[3:] in INPUTS:
            return await INPUTS[st[3:]](ctx, conn, u, c, message.text, jload(u["state_data"]))
        kw = KEYWORDS.get(norm(message.text))
        if kw:
            return await ACTIONS[kw[0]](ctx, conn, u, c, kw[1], "")
        return await screens_core.main_screen(ctx, conn, u, c)

    if (row["state"] or "") == accounts.STATE_AWAITING_NAME and await accounts.get_character(ctx.pool, row["id"]) is None:
        ok, err = await accounts.create_character(ctx.pool, ctx.settings, row["id"], message.text)
        if not ok:
            return await message.answer("⚠️ " + err)
        fresh = await accounts.get_user(ctx.pool, uid)
        ch = await accounts.get_character(ctx.pool, row["id"])
        await message.answer(t.CHARACTER_CREATED.format(name=ch["name"], coins=t.fa_num(fresh["coins"])))

        async def first(conn, u, c):
            return await screens_core.main_screen(ctx, conn, u, c)

        screen, _ = await with_user(ctx, uid, first)
        text, markup = _render(screen, uid)
        return await message.answer(text, reply_markup=markup)
    try:
        screen, msgs = await with_user(ctx, uid, fn, need_char=False)
    except GameError as exc:
        return await message.answer("⚠️ " + str(exc))
    except Exception:  # noqa: BLE001
        log.exception("text input failed")
        return await message.answer("⚠️ خطای داخلی؛ دوباره تلاش کن.")
    if screen is None:
        return await message.answer(t.NO_CHARACTER)
    text, markup = _render(screen, uid, msgs)
    await message.answer(text, reply_markup=markup)
    if screen.combat_cid:
        await send_new(ctx, screen.combat_cid, uid)


@router.callback_query(Act.filter())
async def on_act(cb: CallbackQuery, callback_data: Act, ctx: AppContext):
    if cb.from_user.id != callback_data.u:
        return await cb.answer(t.STALE_BUTTON, show_alert=True)
    fn = ACTIONS.get(callback_data.a, ACTIONS["main"])

    async def run(conn, user, ch):
        return await fn(ctx, conn, user, ch, callback_data.x, callback_data.y)

    try:
        screen, msgs = await with_user(ctx, cb.from_user.id, run)
    except GameError as exc:
        return await cb.answer(str(exc)[:190], show_alert=True)
    except Exception:  # noqa: BLE001
        log.exception("action %s failed", callback_data.a)
        return await cb.answer("⚠️ خطای داخلی؛ دوباره تلاش کن.", show_alert=True)
    await cb.answer()
    text, markup = _render(screen, cb.from_user.id, msgs)
    await _edit(cb, text, markup)
    if screen.combat_cid:
        await send_new(ctx, screen.combat_cid, cb.from_user.id)
