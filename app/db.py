"""اتصال به PostgreSQL و اجرای مهاجرت‌ها (فایل‌های migrations/*.sql به ترتیب نام)."""
from __future__ import annotations

import logging
import pathlib

import asyncpg

log = logging.getLogger(__name__)
MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"
_MIGRATION_LOCK = 727001


async def create_pool(dsn: str) -> asyncpg.Pool:
    # اتصال‌های بیکار زود بسته می‌شوند تا دیتابیس خارجی بتواند بخوابد
    return await asyncpg.create_pool(
        dsn,
        min_size=1,
        max_size=5,
        statement_cache_size=0,
        max_inactive_connection_lifetime=30,
        command_timeout=30,
    )


async def run_migrations(pool: asyncpg.Pool) -> list:
    applied_now = []
    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1)", _MIGRATION_LOCK)
        try:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            done = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if path.name in done:
                    continue
                log.info("applying migration %s", path.name)
                async with conn.transaction():
                    await conn.execute(path.read_text(encoding="utf-8"))
                    await conn.execute("INSERT INTO schema_migrations(version) VALUES($1)", path.name)
                applied_now.append(path.name)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _MIGRATION_LOCK)
    return applied_now
