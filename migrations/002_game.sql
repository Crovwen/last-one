-- LAST ONE — فازهای ۲ تا ۱۲: تمام جدول‌های بازی

ALTER TABLE users ADD COLUMN protected_until TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN captive_immune_until TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN locked_coins BIGINT NOT NULL DEFAULT 0;
ALTER TABLE characters ADD COLUMN skill_xp JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE characters ADD COLUMN stance TEXT NOT NULL DEFAULT 'normal';
ALTER TABLE characters ADD COLUMN rest_until TIMESTAMPTZ;
ALTER TABLE characters ADD COLUMN dead_until TIMESTAMPTZ;

-- ───────── آیتم‌ها ─────────
CREATE TABLE item_defs (
    id BIGSERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,            -- weapon armor shield clothing bag tool food drink medicine resource misc
    tier INT NOT NULL DEFAULT 1,
    power INT NOT NULL DEFAULT 0,
    defense INT NOT NULL DEFAULT 0,
    weight NUMERIC(8,2) NOT NULL DEFAULT 1,
    max_durability INT NOT NULL DEFAULT 0,
    value INT NOT NULL DEFAULT 1,
    stackable BOOLEAN NOT NULL DEFAULT false,
    effects JSONB NOT NULL DEFAULT '{}'::jsonb,   -- خوراکی: hunger thirst health energy | کوله: capacity
    recipe JSONB NOT NULL DEFAULT '{}'::jsonb,    -- {"inputs":{"wood":3},"coins":0,"skill":"building","min_skill":1}
    tradable BOOLEAN NOT NULL DEFAULT true,
    lootable BOOLEAN NOT NULL DEFAULT true,
    in_shop BOOLEAN NOT NULL DEFAULT false,
    shop_price INT NOT NULL DEFAULT 0,
    description TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT true
);

-- منابع و خوراکی‌های قابل‌دسته‌بندی
CREATE TABLE item_stacks (
    holder_type TEXT NOT NULL,      -- backpack storage tribe ground market
    holder_id BIGINT NOT NULL,
    def_id BIGINT NOT NULL REFERENCES item_defs(id),
    qty BIGINT NOT NULL CHECK (qty >= 0),
    PRIMARY KEY (holder_type, holder_id, def_id)
);

-- تجهیزات یکتا (هر کدام شناسه یکتا دارند؛ تکثیر ممکن نیست)
CREATE TABLE item_instances (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    def_id BIGINT NOT NULL REFERENCES item_defs(id),
    owner_user_id BIGINT REFERENCES users(id),
    owner_tribe_id BIGINT,
    holder_type TEXT NOT NULL,      -- backpack storage equipped guard loaned tribe market trade ground
    holder_id BIGINT NOT NULL,
    slot TEXT,
    durability INT NOT NULL DEFAULT 0,
    bound BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX item_inst_holder_idx ON item_instances (holder_type, holder_id);
CREATE INDEX item_inst_owner_idx ON item_instances (owner_user_id);

CREATE TABLE item_events (
    id BIGSERIAL PRIMARY KEY,
    item_id UUID,
    def_id BIGINT,
    qty BIGINT NOT NULL DEFAULT 1,
    from_type TEXT, from_id BIGINT,
    to_type TEXT, to_id BIGINT,
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE drops (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL,             -- death combat
    victim_user_id BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);

-- ───────── فعالیت‌های جنگل ─────────
CREATE TABLE activity_defs (
    id BIGSERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    zone TEXT NOT NULL,             -- dense river mountain cave ruins hunting
    kind TEXT NOT NULL,             -- hunt gather explore
    duration_min INT NOT NULL DEFAULT 10,
    energy_cost INT NOT NULL DEFAULT 10,
    fatigue_gain INT NOT NULL DEFAULT 5,
    hunger_cost INT NOT NULL DEFAULT 3,
    thirst_cost INT NOT NULL DEFAULT 4,
    skill TEXT NOT NULL DEFAULT 'gathering',
    min_skill INT NOT NULL DEFAULT 1,
    loot JSONB NOT NULL DEFAULT '[]'::jsonb,
    risk JSONB NOT NULL DEFAULT '{}'::jsonb,
    xp INT NOT NULL DEFAULT 5,
    description TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT true
);
CREATE TABLE activities (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    def_id BIGINT NOT NULL REFERENCES activity_defs(id),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ends_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',     -- running done
    result JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE UNIQUE INDEX activities_one_running ON activities (user_id) WHERE status = 'running';

-- ───────── ساختمان‌ها (خانه و قبیله) ─────────
CREATE TABLE building_defs (
    id BIGSERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    scope TEXT NOT NULL,            -- home tribe
    kind TEXT NOT NULL,             -- house wall fence barbed trap gate guard_post kennel storage alarm | treasury tower barracks workshop
    name TEXT NOT NULL,
    max_level INT NOT NULL DEFAULT 3,
    cost JSONB NOT NULL DEFAULT '{}'::jsonb,    -- {"coins":50,"wood":10}
    cost_mult NUMERIC(5,2) NOT NULL DEFAULT 1.6,
    build_minutes INT NOT NULL DEFAULT 5,
    effects JSONB NOT NULL DEFAULT '{}'::jsonb, -- هر سطح: defense storage guard_slots kennel_slots trap alarm conceal
    requires JSONB NOT NULL DEFAULT '{}'::jsonb,
    description TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT true
);
CREATE TABLE buildings (
    id BIGSERIAL PRIMARY KEY,
    scope TEXT NOT NULL,
    owner_id BIGINT NOT NULL,       -- user.id یا tribe.id
    def_id BIGINT NOT NULL REFERENCES building_defs(id),
    level INT NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'built',       -- built building
    target_level INT,
    ready_at TIMESTAMPTZ,
    UNIQUE (scope, owner_id, def_id)
);

-- ───────── حیوانات و نیروها ─────────
CREATE TABLE animal_breeds (
    id BIGSERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    species TEXT NOT NULL,          -- dog wolf horse bird
    base_hp INT NOT NULL DEFAULT 40,
    base_power INT NOT NULL DEFAULT 5,
    base_endurance INT NOT NULL DEFAULT 5,
    perks JSONB NOT NULL DEFAULT '{}'::jsonb,   -- guard alert track forage hunt carry speed
    price INT NOT NULL DEFAULT 100,
    description TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT true
);
CREATE TABLE animals (
    id BIGSERIAL PRIMARY KEY,
    owner_user_id BIGINT NOT NULL REFERENCES users(id),
    breed_id BIGINT NOT NULL REFERENCES animal_breeds(id),
    name TEXT NOT NULL,
    level INT NOT NULL DEFAULT 1,
    xp BIGINT NOT NULL DEFAULT 0,
    hp INT NOT NULL,
    max_hp INT NOT NULL,
    power INT NOT NULL,
    endurance INT NOT NULL,
    loyalty INT NOT NULL DEFAULT 50,
    role TEXT NOT NULL DEFAULT 'idle',          -- idle guard companion
    status TEXT NOT NULL DEFAULT 'ok',          -- ok dead
    hp_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    train_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX animals_owner_idx ON animals (owner_user_id);

CREATE TABLE hireling_defs (
    id BIGSERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    role TEXT NOT NULL,             -- guard veteran archer patrol companion
    hp INT NOT NULL DEFAULT 50,
    power INT NOT NULL DEFAULT 6,
    wage INT NOT NULL DEFAULT 10,   -- دستمزد روزانه
    price INT NOT NULL DEFAULT 100,
    description TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT true
);
CREATE TABLE hirelings (
    id BIGSERIAL PRIMARY KEY,
    owner_user_id BIGINT NOT NULL REFERENCES users(id),
    def_id BIGINT NOT NULL REFERENCES hireling_defs(id),
    level INT NOT NULL DEFAULT 1,
    xp BIGINT NOT NULL DEFAULT 0,
    hp INT NOT NULL,
    max_hp INT NOT NULL,
    power INT NOT NULL,
    post TEXT NOT NULL DEFAULT 'idle',          -- idle entrance storage companion tribe_defense
    stance TEXT NOT NULL DEFAULT 'defend',      -- defend hold capture
    status TEXT NOT NULL DEFAULT 'active',      -- active unpaid dead dismissed
    wage_paid_until TIMESTAMPTZ NOT NULL DEFAULT now(),
    hp_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX hirelings_owner_idx ON hirelings (owner_user_id);

-- ───────── قبایل ─────────
CREATE TABLE tribes (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    leader_id BIGINT NOT NULL REFERENCES users(id),
    level INT NOT NULL DEFAULT 1,
    coins BIGINT NOT NULL DEFAULT 0 CHECK (coins >= 0),
    membership TEXT NOT NULL DEFAULT 'public',  -- public private locked
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX tribes_name_uq ON tribes (lower(name));
CREATE TABLE tribe_members (
    user_id BIGINT PRIMARY KEY REFERENCES users(id),
    tribe_id BIGINT NOT NULL REFERENCES tribes(id),
    role TEXT NOT NULL DEFAULT 'member',        -- leader deputy commander guard resources member
    joined_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX tribe_members_tribe_idx ON tribe_members (tribe_id);
CREATE TABLE tribe_requests (
    tribe_id BIGINT NOT NULL REFERENCES tribes(id),
    user_id BIGINT NOT NULL REFERENCES users(id),
    kind TEXT NOT NULL DEFAULT 'request',       -- request (درخواست عضو) invite (دعوت رهبر)
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tribe_id, user_id)
);
CREATE TABLE tribe_ledger (
    id BIGSERIAL PRIMARY KEY,
    tribe_id BIGINT NOT NULL,
    user_id BIGINT,
    kind TEXT NOT NULL,             -- donate_coins withdraw_coins donate_item withdraw_item loan return build upgrade
    def_id BIGINT,
    qty BIGINT NOT NULL DEFAULT 0,
    coins BIGINT NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE tribe_loans (
    id BIGSERIAL PRIMARY KEY,
    tribe_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL REFERENCES users(id),
    def_id BIGINT NOT NULL REFERENCES item_defs(id),
    qty BIGINT NOT NULL DEFAULT 1,
    instance_id UUID,
    status TEXT NOT NULL DEFAULT 'active',      -- active returned lost
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE tribe_relations (
    tribe_a BIGINT NOT NULL REFERENCES tribes(id),
    tribe_b BIGINT NOT NULL REFERENCES tribes(id),
    kind TEXT NOT NULL,             -- ally rival
    status TEXT NOT NULL DEFAULT 'pending',     -- pending active
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tribe_a, tribe_b)
);

-- ───────── بازار و معامله ─────────
CREATE TABLE market_listings (
    id BIGSERIAL PRIMARY KEY,
    seller_id BIGINT NOT NULL REFERENCES users(id),
    def_id BIGINT NOT NULL REFERENCES item_defs(id),
    instance_id UUID,
    qty BIGINT NOT NULL CHECK (qty > 0),
    unit_price BIGINT NOT NULL CHECK (unit_price > 0),
    status TEXT NOT NULL DEFAULT 'active',      -- active sold cancelled
    buyer_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX market_active_idx ON market_listings (status, def_id);
CREATE TABLE trades (
    id BIGSERIAL PRIMARY KEY,
    a_user BIGINT NOT NULL REFERENCES users(id),
    b_user BIGINT NOT NULL REFERENCES users(id),
    a_coins BIGINT NOT NULL DEFAULT 0 CHECK (a_coins >= 0),
    b_coins BIGINT NOT NULL DEFAULT 0 CHECK (b_coins >= 0),
    a_ok BOOLEAN NOT NULL DEFAULT false,
    b_ok BOOLEAN NOT NULL DEFAULT false,
    status TEXT NOT NULL DEFAULT 'open',        -- open done cancelled
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE trade_items (
    id BIGSERIAL PRIMARY KEY,
    trade_id BIGINT NOT NULL REFERENCES trades(id),
    side TEXT NOT NULL,
    def_id BIGINT NOT NULL REFERENCES item_defs(id),
    qty BIGINT NOT NULL DEFAULT 1,
    instance_id UUID
);

-- ───────── مبارزه و اسارت ─────────
CREATE TABLE combats (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT,
    message_id BIGINT,
    flow TEXT NOT NULL,             -- open home tribe
    attacker_user_id BIGINT NOT NULL REFERENCES users(id),
    defender_user_id BIGINT REFERENCES users(id),
    defender_tribe_id BIGINT REFERENCES tribes(id),
    state TEXT NOT NULL,            -- choose recon round loot closed
    round_no INT NOT NULL DEFAULT 0,
    version INT NOT NULL DEFAULT 0,
    deadline TIMESTAMPTZ,
    outcome TEXT,
    victor TEXT,                    -- att def none
    mods JSONB NOT NULL DEFAULT '{}'::jsonb,
    sides JSONB NOT NULL DEFAULT '{}'::jsonb,
    log JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX combats_one_active_att ON combats (attacker_user_id) WHERE state <> 'closed';
CREATE UNIQUE INDEX combats_one_active_def ON combats (defender_user_id) WHERE state <> 'closed' AND defender_user_id IS NOT NULL;
CREATE INDEX combats_pair_idx ON combats (attacker_user_id, defender_user_id, created_at DESC);

CREATE TABLE captivities (
    id BIGSERIAL PRIMARY KEY,
    captive_id BIGINT NOT NULL REFERENCES users(id),
    captor_id BIGINT NOT NULL REFERENCES users(id),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ends_at TIMESTAMPTZ NOT NULL,
    ransom BIGINT NOT NULL DEFAULT 0,
    offer BIGINT NOT NULL DEFAULT 0,
    task JSONB,
    status TEXT NOT NULL DEFAULT 'active',      -- active ended
    end_reason TEXT,
    last_escape_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX captivities_one_active ON captivities (captive_id) WHERE status = 'active';

-- ───────── مأموریت، رویداد، فصل، گزارش تخلف ─────────
CREATE TABLE mission_defs (
    id BIGSERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,             -- deliver activity
    target TEXT NOT NULL,           -- کد آیتم یا نوع فعالیت (hunt gather explore)
    amount INT NOT NULL DEFAULT 1,
    reward_coins INT NOT NULL DEFAULT 20,
    reward_xp INT NOT NULL DEFAULT 10,
    duration_min INT NOT NULL DEFAULT 120,
    description TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT true
);
CREATE TABLE missions_active (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    def_id BIGINT NOT NULL REFERENCES mission_defs(id),
    progress INT NOT NULL DEFAULT 0,
    expires_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'       -- active done expired
);
CREATE UNIQUE INDEX missions_one_active ON missions_active (user_id) WHERE status = 'active';

CREATE TABLE chat_groups (
    chat_id BIGINT PRIMARY KEY,
    title TEXT,
    events_enabled BOOLEAN NOT NULL DEFAULT true,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE events (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    message_id BIGINT,
    kind TEXT NOT NULL,             -- cargo beast
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    ends_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'         -- open done
);
CREATE TABLE event_claims (
    event_id BIGINT NOT NULL REFERENCES events(id),
    user_id BIGINT NOT NULL REFERENCES users(id),
    power INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, user_id)
);
CREATE TABLE seasons (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ NOT NULL,
    entry_fee BIGINT NOT NULL DEFAULT 0,
    min_level INT NOT NULL DEFAULT 1,
    rewards JSONB NOT NULL DEFAULT '[]'::jsonb,   -- سکه رتبه‌های ۱..n
    status TEXT NOT NULL DEFAULT 'scheduled'      -- scheduled active ended
);
CREATE TABLE season_entries (
    season_id BIGINT NOT NULL REFERENCES seasons(id),
    user_id BIGINT NOT NULL REFERENCES users(id),
    score BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (season_id, user_id)
);
CREATE TABLE violation_reports (
    id BIGSERIAL PRIMARY KEY,
    reporter_id BIGINT NOT NULL REFERENCES users(id),
    target_id BIGINT REFERENCES users(id),
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
