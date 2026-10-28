"""تست‌های یکپارچگی با PostgreSQL واقعی. فقط وقتی TEST_DATABASE_URL تنظیم باشد اجرا می‌شوند.
هشدار: روی یک دیتابیس خالی/آزمایشی اجرا کن، نه دیتابیس واقعی بازی."""
import asyncio
import os

import pytest

DSN = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL تنظیم نشده")


@pytest.fixture()
def pool():
    from app.db import create_pool, run_migrations

    loop = asyncio.new_event_loop()
    p = loop.run_until_complete(create_pool(DSN))
    loop.run_until_complete(run_migrations(p))
    yield loop, p
    loop.run_until_complete(p.close())
    loop.close()


def test_double_spend_is_impossible(pool):
    loop, p = pool
    from app.services.economy import InsufficientFunds, change_coins

    async def scenario():
        uid = await p.fetchval(
            "INSERT INTO users(telegram_id, coins) VALUES((random()*1e12)::bigint, 100) RETURNING id")

        async def spend():
            async with p.acquire() as c:
                async with c.transaction():
                    await change_coins(c, uid, -60, "test")

        results = await asyncio.gather(spend(), spend(), return_exceptions=True)
        ok = [r for r in results if not isinstance(r, Exception)]
        failed = [r for r in results if isinstance(r, InsufficientFunds)]
        assert len(ok) == 1 and len(failed) == 1
        assert await p.fetchval("SELECT coins FROM users WHERE id=$1", uid) == 40

    loop.run_until_complete(scenario())


def test_job_unique_key(pool):
    loop, p = pool
    from app.services.jobs import JobRunner

    async def scenario():
        r = JobRunner(p)
        key = "t:" + str(os.getpid())
        assert await r.enqueue("noop", unique_key=key) is True
        assert await r.enqueue("noop", unique_key=key) is False
        await p.execute("DELETE FROM jobs WHERE unique_key=$1", key)

    loop.run_until_complete(scenario())
