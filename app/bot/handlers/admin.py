"""ورود به پنل مدیریت (همه‌چیز دکمه‌ای است؛ فایل screens_admin.py) و ارسال پشتیبان."""
from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message

from app.bot.commands import open_action
from app.context import AppContext
from app.services import backup as backup_service
from app.services.errors import GameError

router = Router(name="admin")


@router.message(Command("admin"))
async def cmd_admin(message: Message, ctx: AppContext):
    if not message.from_user or message.from_user.id not in ctx.config.admin_ids:
        return
    in_group = message.chat.type in ("group", "supergroup")
    try:
        _, text, markup = await open_action(ctx, message.from_user.id, "ad_g" if in_group else "ad",
                                            str(message.chat.id) if in_group else "")
    except GameError as exc:
        return await message.answer("⚠️ " + str(exc))
    await message.reply(text, reply_markup=markup)


async def send_backup(ctx: AppContext, chat_ids) -> None:
    data = await backup_service.make_backup(ctx.pool)
    name = "lastone-backup-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M") + ".json.gz"
    for chat_id in chat_ids:
        if len(data) > backup_service.MAX_TELEGRAM_FILE:
            await ctx.bot.send_message(chat_id, "⚠️ فایل پشتیبان از حد تلگرام بزرگ‌تر شد؛ روش پشتیبان‌گیری باید ارتقا یابد.")
        else:
            await ctx.bot.send_document(chat_id, BufferedInputFile(data, filename=name), caption="💾 پشتیبان دیتابیس")
