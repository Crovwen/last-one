"""اجرای هر تعامل بازیکن: یک تراکنش، قفل ردیف کاربر، تسویه تنبل، سپس اکشن."""
from __future__ import annotations

from app.context import AppContext
from app.services import notify
from app.services.chars import get_char
from app.services.errors import GameError
from app.services.settle import settle


async def with_user(ctx: AppContext, tg_id: int, fn, need_char: bool = True):
    async with notify.collecting() as q:
        async with ctx.pool.acquire() as conn:
            async with conn.transaction():
                user = await conn.fetchrow("SELECT * FROM users WHERE telegram_id=$1", tg_id)
                if user is None:
                    raise GameError("اول در چت خصوصی ربات /start بزن.")
                if user["is_banned"]:
                    raise GameError("⛔️ حساب تو مسدود شده است.")
                msgs = await settle(conn, ctx.settings, user["id"])
                user = await conn.fetchrow("SELECT * FROM users WHERE id=$1", user["id"])
                ch = await get_char(conn, user["id"])
                if need_char and ch is None:
                    raise GameError("اول شخصیت بساز: /start")
                result = await fn(conn, user, ch)
    await notify.flush([(tg, t) for tg, t in q if tg != tg_id])
    return result, msgs


async def settle_uid(ctx: AppContext, user_id: int) -> None:
    """تسویه یک بازیکن از طرف کار زمان‌دار (و ارسال اعلان‌ها)."""
    async with notify.collecting() as q:
        async with ctx.pool.acquire() as conn:
            async with conn.transaction():
                await settle(conn, ctx.settings, user_id)
    await notify.flush(q)
