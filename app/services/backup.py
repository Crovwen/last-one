"""پشتیبان‌گیری: همه جدول‌ها به‌صورت JSON فشرده (برای فرستادن به مدیر در تلگرام)."""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone

import asyncpg

MAX_TELEGRAM_FILE = 45 * 1024 * 1024


def _default(o):
    return str(o)


async def make_backup(pool: asyncpg.Pool) -> bytes:
    data = {"created_at": datetime.now(timezone.utc).isoformat(), "tables": {}}
    async with pool.acquire() as conn:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            tables = await conn.fetch(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name"
            )
            for t in tables:
                name = t["table_name"]
                rows = await conn.fetch('SELECT * FROM "' + name.replace('"', '""') + '"')
                data["tables"][name] = [dict(r) for r in rows]
    return gzip.compress(json.dumps(data, default=_default, ensure_ascii=False).encode("utf-8"))
