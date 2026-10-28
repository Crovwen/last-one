"""تنظیمات محیطی (توکن، دیتابیس، ...). هیچ اطلاعات حساسی داخل کد نیست."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    bot_token: str
    database_url: str
    admin_ids: frozenset
    webhook_base_url: str
    webhook_secret: str
    port: int

    @property
    def webhook_path(self) -> str:
        # بخشی از هش توکن در مسیر، تا آدرس وب‌هوک حدس‌زدنی نباشد
        return "/tg/" + hashlib.sha256(self.bot_token.encode()).hexdigest()[:24]

    @property
    def webhook_url(self) -> str:
        return self.webhook_base_url.rstrip("/") + self.webhook_path


def parse_ids(raw: str) -> frozenset:
    out = set()
    for part in raw.replace(" ", "").split(","):
        if part:
            out.add(int(part))
    return frozenset(out)


def load_config(env=None) -> Config:
    env = os.environ if env is None else env
    token = env.get("BOT_TOKEN", "").strip()
    dsn = env.get("DATABASE_URL", "").strip()
    if not token:
        raise ConfigError("BOT_TOKEN تنظیم نشده است.")
    if not dsn:
        raise ConfigError("DATABASE_URL تنظیم نشده است.")
    base = (env.get("WEBHOOK_BASE_URL") or env.get("RENDER_EXTERNAL_URL") or "").strip()
    if not base:
        raise ConfigError("WEBHOOK_BASE_URL یا RENDER_EXTERNAL_URL تنظیم نشده است.")
    secret = env.get("WEBHOOK_SECRET", "").strip() or hashlib.sha256(
        ("secret:" + token).encode()
    ).hexdigest()
    try:
        admins = parse_ids(env.get("ADMIN_IDS", ""))
    except ValueError as exc:
        raise ConfigError("ADMIN_IDS باید اعداد جداشده با کاما باشد.") from exc
    return Config(
        bot_token=token,
        database_url=dsn,
        admin_ids=admins,
        webhook_base_url=base,
        webhook_secret=secret,
        port=int(env.get("PORT", "10000")),
    )
