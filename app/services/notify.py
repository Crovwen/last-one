"""اعلان‌ها: ذخیره در دیتابیس + ارسال پیام خصوصی بعد از پایان تراکنش (بهترین تلاش)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar

log = logging.getLogger(__name__)
_pusher = None            # async fn(telegram_id:int, text:str)
_queue: ContextVar = ContextVar("push_queue", default=None)


def set_pusher(fn) -> None:
    global _pusher
    _pusher = fn


@asynccontextmanager
async def collecting():
    q = []
    tok = _queue.set(q)
    try:
        yield q
    finally:
        _queue.reset(tok)


async def flush(q: list) -> None:
    for tg, text in q:
        await _send(tg, text)


async def _send(tg: int, text: str) -> None:
    if _pusher and tg:
        try:
            await _pusher(tg, text)
        except Exception:  # noqa: BLE001
            log.warning("push failed for %s", tg)


async def notify(conn, user_id: int, kind: str, text: str, push: bool = True) -> None:
    await conn.execute("INSERT INTO notifications(user_id, kind, text) VALUES($1,$2,$3)", user_id, kind, text)
    if not push:
        return
    tg = await conn.fetchval("SELECT telegram_id FROM users WHERE id=$1", user_id)
    q = _queue.get()
    if q is not None:
        q.append((tg, text))
    else:
        await _send(tg, text)
