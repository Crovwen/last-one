-- به‌روزرسانی ۱: محافظ قابل خاموش/روشن، خفتگیری، گروگانگیری و حمله گروهی

ALTER TABLE users ADD COLUMN protection_off BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE users ADD COLUMN last_mug_at TIMESTAMPTZ;        -- آخرین خفتگیری این بازیکن
ALTER TABLE users ADD COLUMN last_mugged_at TIMESTAMPTZ;     -- آخرین باری که از او خفتگیری شد
CREATE INDEX users_username_idx ON users (lower(username));

ALTER TABLE combats ADD COLUMN kind TEXT NOT NULL DEFAULT 'attack';          -- attack hostage raid
ALTER TABLE combats ADD COLUMN party JSONB NOT NULL DEFAULT '[]'::jsonb;     -- شناسه کاربران طرف مهاجم
ALTER TABLE captivities ADD COLUMN party JSONB NOT NULL DEFAULT '[]'::jsonb; -- هم‌دستان اسارت‌گیر
