"""زمان‌بندی کارها (پل بین سرویس‌ها و JobRunner)."""
from __future__ import annotations

_fn = None


def set_scheduler(fn) -> None:
    global _fn
    _fn = fn


async def schedule(kind: str, payload: dict, run_at, unique_key: str = None) -> None:
    if _fn:
        await _fn(kind, payload, run_at, unique_key)
