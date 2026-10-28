"""گروه‌ها: راهنما، رویدادهای گروهی (محموله/جانور)."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot import texts_fa as t
from app.context import AppContext
from app.runtime import with_user
from app.services import events
from app.services.errors import GameError

log = logging.getLogger(__name__)
router = Router(name="groups")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))


class Ev(CallbackData, prefix="e"):
    e: int


@router.message(CommandStart())
async def group_start(message: Message):
    await message.answer(t.GROUP_HINT)


async def _claims(conn, eid: int):
    return await conn.fetch("SELECT * FROM event_claims WHERE event_id=$1", eid)


async def render_event(ctx: AppContext, eid: int) -> tuple:
    async with ctx.pool.acquire() as conn:
        e = await events.event_row(conn, eid)
        claims = await _claims(conn, eid)
    text, btns = events.event_text(e, claims)
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=Ev(e=eid).pack())
                                                    for label, _ in btns]]) if btns else None
    return e, text, markup


async def post_event(ctx: AppContext, chat_id: int, kind: str = None):
    async with ctx.pool.acquire() as conn:
        async with conn.transaction():
            e = await events.spawn_event(conn, ctx.settings, chat_id, kind)
    _, text, markup = await render_event(ctx, e["id"])
    sent = await ctx.bot.send_message(chat_id, text, reply_markup=markup)
    await ctx.pool.execute("UPDATE events SET message_id=$2 WHERE id=$1", e["id"], sent.message_id)
    return e


async def refresh_event(ctx: AppContext, eid: int, extra: str = "") -> None:
    e, text, markup = await render_event(ctx, eid)
    if not e["message_id"]:
        return
    try:
        await ctx.bot.edit_message_text((text + ("\n\n" + extra if extra else ""))[:4000], chat_id=e["chat_id"],
                                        message_id=e["message_id"], reply_markup=markup)
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc):
            log.warning("event edit failed: %s", exc)


@router.callback_query(Ev.filter())
async def on_event(cb: CallbackQuery, callback_data: Ev, ctx: AppContext):
    async def fn(conn, user, ch):
        return await events.claim_event(conn, ctx.settings, user["id"], callback_data.e)

    try:
        msg, _ = await with_user(ctx, cb.from_user.id, fn)
    except GameError as exc:
        return await cb.answer(str(exc), show_alert=True)
    await cb.answer(msg, show_alert=True)
    await refresh_event(ctx, callback_data.e)
