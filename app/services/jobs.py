"""صف کار زمان‌دار روی PostgreSQL.

- با ری‌استارت چیزی از بین نمی‌رود (همه‌چیز در جدول jobs است).
- بین کارها، فقط تا زمان سررسید بعدی می‌خوابد و بیهوده دیتابیس را بیدار نگه نمی‌دارد.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import asyncpg

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 5
MAX_SLEEP_SECONDS = 6 * 3600


def backoff_seconds(attempt: int) -> int:
    return min(30 * (2 ** max(attempt - 1, 0)), 3600)


class JobRunner:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool
        self._handlers = {}
        self._wake = asyncio.Event()
        self._task = None
        self._stopping = False

    def register(self, kind: str, handler) -> None:
        self._handlers[kind] = handler

    async def enqueue(self, kind: str, payload=None, run_at=None, unique_key: str = None) -> bool:
        run_at = run_at or datetime.now(timezone.utc)
        row = await self._pool.fetchrow(
            "INSERT INTO jobs(kind, payload, run_at, unique_key) VALUES($1, $2::jsonb, $3, $4) "
            "ON CONFLICT (unique_key) DO NOTHING RETURNING id",
            kind, json.dumps(payload or {}), run_at, unique_key,
        )
        self._wake.set()
        return row is not None

    async def start(self) -> None:
        # در شروع، هر کاری که نیمه‌کاره مانده را دوباره در صف می‌گذاریم
        await self._pool.execute("UPDATE jobs SET status = 'pending' WHERE status = 'running'")
        self._task = asyncio.create_task(self._loop(), name="job-runner")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _claim(self):
        return await self._pool.fetchrow(
            "WITH c AS (SELECT id FROM jobs WHERE status = 'pending' AND run_at <= now() "
            "ORDER BY run_at LIMIT 1 FOR UPDATE SKIP LOCKED) "
            "UPDATE jobs SET status = 'running', locked_at = now(), attempts = attempts + 1 "
            "FROM c WHERE jobs.id = c.id RETURNING jobs.*"
        )

    async def _run_one(self, job) -> None:
        handler = self._handlers.get(job["kind"])
        try:
            if handler is None:
                raise RuntimeError(f"handler for {job['kind']} not registered")
            await handler(json.loads(job["payload"]))
        except Exception as exc:  # noqa: BLE001
            log.exception("job %s failed", job["id"])
            if job["attempts"] >= MAX_ATTEMPTS:
                await self._pool.execute(
                    "UPDATE jobs SET status = 'failed', last_error = $2, unique_key = NULL WHERE id = $1",
                    job["id"], str(exc)[:500],
                )
            else:
                run_at = datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds(job["attempts"]))
                await self._pool.execute(
                    "UPDATE jobs SET status = 'pending', last_error = $2, run_at = $3 WHERE id = $1",
                    job["id"], str(exc)[:500], run_at,
                )
        else:
            await self._pool.execute(
                "UPDATE jobs SET status = 'done', unique_key = NULL WHERE id = $1", job["id"]
            )

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                self._wake.clear()
                job = await self._claim()
                if job:
                    await self._run_one(job)
                    continue
                due = await self._pool.fetchval("SELECT min(run_at) FROM jobs WHERE status = 'pending'")
                if due is None:
                    timeout = MAX_SLEEP_SECONDS
                else:
                    timeout = min(max((due - datetime.now(timezone.utc)).total_seconds(), 1), MAX_SLEEP_SECONDS)
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("job loop error; retrying in 30s")
                await asyncio.sleep(30)
