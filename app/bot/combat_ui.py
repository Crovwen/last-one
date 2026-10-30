"""رابط تلگرام مبارزه: پیام گروه، دکمه‌ها، ریپلای «حمله»."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.ui import btn, style_for
from app.context import AppContext
from app.runtime import with_user
from app.services import combat
from app.services.errors import GameError

log = logging.getLogger(__name__)
_GREEN_ACTS = {"start", "pj", "defend", "finish"}
_RED_ACTS = {"cancel", "pn", "surrender"}


def _combat_style(act: str, label: str) -> str:
    return "success" if act in _GREEN_ACTS else "danger" if act in _RED_ACTS else style_for(label)


router = Router(name="combat")


class Cb(CallbackData, prefix="c"):
    c: int      # شناسه نبرد
    v: int      # نسخه پیام (دکمه قدیمی رد می‌شود)
    a: str      # اکشن


def markup(c: dict, buttons: list) -> InlineKeyboardMarkup:
    btns = [btn(label, Cb(c=c["id"], v=c["version"], a=act).pack(), _combat_style(act, label)) for label, act in buttons]
    return InlineKeyboardMarkup(inline_keyboard=[btns[i:i + 2] for i in range(0, len(btns), 2)])


async def _load(ctx: AppContext, cid: int) -> dict:
    async with ctx.pool.acquire() as conn:
        return await combat.get(conn, cid, lock=False)


async def refresh(ctx: AppContext, cid: int) -> None:
    c = await _load(ctx, cid)
    if not c["chat_id"] or not c["message_id"]:
        return
    text, buttons = combat.describe(c)
    try:
        await ctx.bot.edit_message_text(text[:4000], chat_id=c["chat_id"], message_id=c["message_id"],
                                        reply_markup=markup(c, buttons) if buttons else None)
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc):
            log.warning("combat edit failed: %s", exc)


async def send_new(ctx: AppContext, cid: int, chat_id: int, reply_to: int = None) -> None:
    c = await _load(ctx, cid)
    text, buttons = combat.describe(c)
    sent = await ctx.bot.send_message(chat_id, text[:4000], reply_markup=markup(c, buttons) if buttons else None,
                                      reply_to_message_id=reply_to)
    await ctx.pool.execute("UPDATE combats SET chat_id=$2, message_id=$3 WHERE id=$1", cid, chat_id, sent.message_id)


@router.callback_query(Cb.filter())
async def on_cb(cb: CallbackQuery, callback_data: Cb, ctx: AppContext):
    async def fn(conn, user, ch):
        return await combat.perform(conn, ctx.settings, callback_data.c, callback_data.v, user["id"], callback_data.a)

    try:
        await with_user(ctx, cb.from_user.id, fn)
    except GameError as exc:
        return await cb.answer(str(exc), show_alert=True)
    except Exception:  # noqa: BLE001
        log.exception("combat callback failed")
        return await cb.answer("⚠️ خطای داخلی؛ دوباره تلاش کن.", show_alert=True)
    await cb.answer()
    await refresh(ctx, callback_data.c)
