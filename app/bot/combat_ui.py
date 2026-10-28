"""رابط تلگرام مبارزه: پیام گروه، دکمه‌ها، ریپلای «حمله»."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.context import AppContext
from app.runtime import with_user
from app.services import combat
from app.services.errors import GameError

log = logging.getLogger(__name__)
router = Router(name="combat")


class Cb(CallbackData, prefix="c"):
    c: int      # شناسه نبرد
    v: int      # نسخه پیام (دکمه قدیمی رد می‌شود)
    a: str      # اکشن


def markup(c: dict, buttons: list) -> InlineKeyboardMarkup:
    btns = [InlineKeyboardButton(text=label, callback_data=Cb(c=c["id"], v=c["version"], a=act).pack())
            for label, act in buttons]
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


group_msgs = Router(name="group_msgs")
group_msgs.message.filter(F.chat.type.in_({"group", "supergroup"}))


@group_msgs.message(F.text.func(lambda t: t.strip() in ("حمله", "/attack")), F.reply_to_message)
async def on_attack(message: Message, ctx: AppContext):
    target = message.reply_to_message.from_user
    if target is None or target.is_bot:
        return await message.reply("هدف باید یک بازیکن باشد.")

    async def fn(conn, user, ch):
        t = await conn.fetchrow("SELECT id FROM users WHERE telegram_id=$1", target.id)
        if t is None:
            raise GameError("این بازیکن هنوز در بازی ثبت‌نام نکرده است.")
        return await combat.start_attack(conn, ctx.settings, user["id"], t["id"], message.chat.id, None)

    try:
        c, _ = await with_user(ctx, message.from_user.id, fn)
    except GameError as exc:
        return await message.reply("⚠️ " + str(exc))
    await send_new(ctx, c["id"], message.chat.id, reply_to=message.reply_to_message.message_id)


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
