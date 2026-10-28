-- LAST ONE — فاز ۱: زیرساخت، حساب، شخصیت، تنظیمات، دفتر سکه، صف کار، لاگ

CREATE TABLE users (
    id            BIGSERIAL PRIMARY KEY,
    telegram_id   BIGINT NOT NULL UNIQUE,
    username      TEXT,
    first_name    TEXT,
    account_level INT    NOT NULL DEFAULT 1,
    account_xp    BIGINT NOT NULL DEFAULT 0,
    coins         BIGINT NOT NULL DEFAULT 0 CHECK (coins >= 0),
    -- وضعیت گفتگو (مثلاً در انتظار نام شخصیت) در دیتابیس است تا با ری‌استارت از بین نرود
    state         TEXT,
    state_data    JSONB  NOT NULL DEFAULT '{}'::jsonb,
    is_banned     BOOLEAN NOT NULL DEFAULT false,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE characters (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL UNIQUE REFERENCES users(id),
    name        TEXT   NOT NULL,
    level       INT    NOT NULL DEFAULT 1,
    xp          BIGINT NOT NULL DEFAULT 0,
    health      INT    NOT NULL,
    hunger      INT    NOT NULL,
    thirst      INT    NOT NULL,
    energy      INT    NOT NULL,
    fatigue     INT    NOT NULL,
    injury      TEXT   NOT NULL DEFAULT 'none',
    -- موقعیت: home | hunting | gathering | operation | tribe_base | resting | captive | dead
    location    TEXT   NOT NULL DEFAULT 'home',
    skills      JSONB  NOT NULL,
    vitals_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX characters_name_lower_uq ON characters (lower(name));

-- همه اعداد قابل تنظیم بازی (مقدار پیش‌فرض در کد، مقدار فعلی اینجا)
CREATE TABLE game_settings (
    key         TEXT PRIMARY KEY,
    value       JSONB NOT NULL,
    description TEXT  NOT NULL DEFAULT '',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- دفتر کل سکه: هر تغییر موجودی یک ردیف دارد
CREATE TABLE coin_ledger (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT NOT NULL REFERENCES users(id),
    delta         BIGINT NOT NULL,
    balance_after BIGINT NOT NULL CHECK (balance_after >= 0),
    reason        TEXT   NOT NULL,
    ref           TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX coin_ledger_user_idx ON coin_ledger (user_id, id DESC);

-- صف کارهای زمان‌دار (جایگزین Redis/Celery؛ با ری‌استارت از بین نمی‌رود)
CREATE TABLE jobs (
    id         BIGSERIAL PRIMARY KEY,
    kind       TEXT NOT NULL,
    payload    JSONB NOT NULL DEFAULT '{}'::jsonb,
    run_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    status     TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','done','failed')),
    attempts   INT  NOT NULL DEFAULT 0,
    locked_at  TIMESTAMPTZ,
    last_error TEXT,
    unique_key TEXT UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX jobs_due_idx ON jobs (run_at) WHERE status = 'pending';

CREATE TABLE audit_log (
    id         BIGSERIAL PRIMARY KEY,
    actor      TEXT NOT NULL,
    action     TEXT NOT NULL,
    data       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_idx ON audit_log (created_at DESC);

-- اعلان‌ها/گزارش اتفاقات برای نمایش بعد از بازگشت بازیکن
CREATE TABLE notifications (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id),
    kind       TEXT NOT NULL,
    text       TEXT NOT NULL,
    is_read    BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX notifications_user_idx ON notifications (user_id, is_read, id DESC);
