"""محتوای اولیه بازی. فقط کدهای جدید اضافه می‌شوند؛ تغییرات مدیر دست‌نخورده می‌ماند.
هر عدد اینجا فقط مقدار شروع است و از پنل مدیریت قابل ویرایش است."""
from __future__ import annotations

import json
from decimal import Decimal


def I(code, name, kind, value=1, weight=1.0, **kw):
    d = dict(code=code, name=name, kind=kind, value=value, weight=weight, tier=1, power=0, defense=0,
             max_durability=0, stackable=False, effects={}, recipe={}, tradable=True, lootable=True,
             in_shop=False, shop_price=0, description="")
    d.update(kw)
    return d


ITEMS = [
    # منابع
    I("wood", "چوب", "resource", 2, 1.0, stackable=True, in_shop=True, shop_price=4),
    I("stone", "سنگ", "resource", 3, 1.5, stackable=True, in_shop=True, shop_price=6),
    I("metal", "فلز", "resource", 6, 2.0, stackable=True, in_shop=True, shop_price=12),
    I("fiber", "الیاف", "resource", 1, 0.2, stackable=True, in_shop=True, shop_price=2),
    I("hide", "پوست", "resource", 5, 1.0, stackable=True),
    I("herb", "گیاه دارویی", "resource", 4, 0.1, stackable=True),
    I("meat_raw", "گوشت خام", "food", 4, 0.8, stackable=True, effects={"hunger": 15, "health": -3}),
    # خوراکی و دارو
    I("water", "آب", "drink", 2, 1.0, stackable=True, effects={"thirst": 40}, in_shop=True, shop_price=3),
    I("bread", "نان", "food", 3, 0.3, stackable=True, effects={"hunger": 30}, in_shop=True, shop_price=5),
    I("berries", "توت جنگلی", "food", 2, 0.2, stackable=True, effects={"hunger": 10, "thirst": 5}),
    I("meat_cooked", "گوشت کبابی", "food", 8, 0.8, stackable=True, effects={"hunger": 45, "energy": 5},
      recipe={"inputs": {"meat_raw": 1, "wood": 1}}),
    I("bandage", "باند و دارو", "medicine", 10, 0.2, stackable=True, effects={"health": 30}, in_shop=True,
      shop_price=18, recipe={"inputs": {"herb": 2, "fiber": 1}}),
    I("energy_tonic", "نوشیدنی انرژی‌زا", "drink", 12, 0.5, stackable=True, effects={"energy": 30, "fatigue": 20},
      in_shop=True, shop_price=25),
    # سلاح
    I("knife", "چاقو", "weapon", 15, 1.0, power=4, max_durability=60, in_shop=True, shop_price=28,
      recipe={"inputs": {"metal": 1, "wood": 1}}),
    I("spear", "نیزه", "weapon", 20, 2.0, power=6, max_durability=50, recipe={"inputs": {"wood": 3, "stone": 1}}),
    I("axe", "تبر", "weapon", 30, 2.5, power=7, max_durability=80,
      recipe={"inputs": {"metal": 2, "wood": 2}, "skill": "building", "min_skill": 2}),
    I("bow", "کمان", "weapon", 50, 1.0, power=8, max_durability=70, effects={"ranged": True},
      recipe={"inputs": {"wood": 3, "fiber": 3}, "skill": "building", "min_skill": 2}),
    I("sword", "شمشیر", "weapon", 90, 2.0, power=12, max_durability=120, tier=2,
      recipe={"inputs": {"metal": 5, "wood": 1}, "skill": "building", "min_skill": 4}),
    I("warhammer", "چکش جنگی", "weapon", 110, 3.5, power=14, max_durability=140, tier=2,
      recipe={"inputs": {"metal": 6, "wood": 2}, "skill": "building", "min_skill": 5}),
    I("hunting_rifle", "تفنگ شکاری", "weapon", 300, 3.0, power=20, max_durability=200, tier=3, effects={"ranged": True},
      in_shop=True, shop_price=650),
    # زره، سپر، لباس
    I("base_clothes", "لباس پایه", "clothing", 0, 0.5, tradable=False, lootable=False),
    I("warm_coat", "کاپشن گرم", "clothing", 20, 1.0, defense=2, max_durability=80, in_shop=True, shop_price=35),
    I("leather_armor", "زره چرمی", "armor", 40, 3.0, defense=5, max_durability=100,
      recipe={"inputs": {"hide": 4, "fiber": 2}}),
    I("iron_armor", "زره فلزی", "armor", 120, 8.0, defense=12, max_durability=180, tier=2,
      recipe={"inputs": {"metal": 8, "hide": 2}, "skill": "building", "min_skill": 4}),
    I("wood_shield", "سپر چوبی", "shield", 15, 2.0, defense=3, max_durability=60, recipe={"inputs": {"wood": 4}}),
    I("iron_shield", "سپر آهنی", "shield", 60, 4.0, defense=6, max_durability=120, tier=2,
      recipe={"inputs": {"metal": 4, "wood": 1}, "skill": "building", "min_skill": 3}),
    # کوله و ابزار
    I("small_pack", "کوله کوچک", "bag", 20, 1.0, effects={"capacity": 10}, max_durability=0, in_shop=True, shop_price=25),
    I("big_pack", "کوله بزرگ", "bag", 70, 1.5, effects={"capacity": 25}, tier=2,
      recipe={"inputs": {"hide": 6, "fiber": 4}}, in_shop=True, shop_price=95),
    I("builder_tools", "ابزار ساخت‌وساز", "tool", 25, 2.0, max_durability=100, effects={"build_speed": 0.8},
      in_shop=True, shop_price=40),
]


def A(code, name, zone, kind, loot, risk=None, **kw):
    d = dict(code=code, name=name, zone=zone, kind=kind, duration_min=5, energy_cost=8, fatigue_gain=4,
             hunger_cost=3, thirst_cost=4, skill="gathering", min_skill=1, loot=loot, risk=risk or {}, xp=5,
             description="")
    d.update(kw)
    return d


ACTIVITIES = [
    A("gather_wood", "جمع‌آوری چوب", "dense", "gather",
      [{"item": "wood", "min": 3, "max": 6, "chance": 1.0}, {"item": "fiber", "min": 1, "max": 2, "chance": 0.5}]),
    A("forage_berries", "جمع‌آوری توت و گیاه", "dense", "gather",
      [{"item": "berries", "min": 2, "max": 5, "chance": 1.0}, {"item": "herb", "min": 1, "max": 2, "chance": 0.5}],
      energy_cost=5),
    A("fetch_water", "آب‌برداری از رودخانه", "river", "gather",
      [{"item": "water", "min": 3, "max": 5, "chance": 1.0}], energy_cost=5, hunger_cost=2, thirst_cost=0),
    A("mine_stone", "استخراج سنگ", "mountain", "gather",
      [{"item": "stone", "min": 3, "max": 6, "chance": 1.0}, {"item": "metal", "min": 1, "max": 2, "chance": 0.3}],
      duration_min=8, energy_cost=12, risk={"chance": 0.1, "damage": [3, 8], "text": "سنگ‌ریزه به دستت خورد"}),
    A("mine_metal", "استخراج فلز", "mountain", "gather",
      [{"item": "metal", "min": 2, "max": 4, "chance": 1.0}], duration_min=10, energy_cost=15, min_skill=3,
      risk={"chance": 0.15, "damage": [4, 10], "text": "ریزش کوچک معدن"}, xp=8),
    A("hunt_small", "شکار حیوان کوچک", "hunting", "hunt",
      [{"item": "meat_raw", "min": 1, "max": 3, "chance": 1.0}, {"item": "hide", "min": 1, "max": 2, "chance": 0.5},
       {"coins": [3, 10], "chance": 0.4}],
      skill="combat", duration_min=8, energy_cost=10, risk={"chance": 0.1, "damage": [3, 10], "text": "طعمه فرار کرد و زخمی شدی"}),
    A("hunt_big", "شکار حیوان بزرگ", "dense", "hunt",
      [{"item": "meat_raw", "min": 3, "max": 6, "chance": 1.0}, {"item": "hide", "min": 2, "max": 4, "chance": 0.8},
       {"coins": [10, 25], "chance": 0.5}, {"animal": "wolf_pup", "chance": 0.05}],
      skill="combat", min_skill=3, duration_min=15, energy_cost=18, fatigue_gain=8,
      risk={"chance": 0.25, "damage": [10, 25], "text": "حیوان وحشی به تو حمله کرد"}, xp=10),
    A("explore_cave", "کاوش غار", "cave", "explore",
      [{"item": "metal", "min": 1, "max": 3, "chance": 0.6}, {"item": "bandage", "min": 1, "max": 1, "chance": 0.2},
       {"coins": [5, 20], "chance": 0.4}],
      skill="perception", duration_min=15, energy_cost=15,
      risk={"chance": 0.2, "damage": [5, 15], "text": "در تاریکی غار زخمی شدی"}, xp=8),
    A("explore_ruins", "کاوش مناطق متروکه", "ruins", "explore",
      [{"item": "knife", "min": 1, "max": 1, "chance": 0.08}, {"item": "axe", "min": 1, "max": 1, "chance": 0.05},
       {"item": "leather_armor", "min": 1, "max": 1, "chance": 0.04}, {"item": "metal", "min": 1, "max": 3, "chance": 0.6},
       {"coins": [10, 40], "chance": 0.5}],
      skill="perception", min_skill=2, duration_min=20, energy_cost=20, fatigue_gain=8,
      risk={"chance": 0.3, "damage": [8, 20], "text": "با خطری در ویرانه‌ها روبه‌رو شدی"}, xp=12),
]


def B(code, scope, kind, name, cost, effects, max_level=3, minutes=5, requires=None, mult=1.6):
    return dict(code=code, scope=scope, kind=kind, name=name, cost=cost, effects=effects, max_level=max_level,
                build_minutes=minutes, requires=requires or {}, cost_mult=mult, description="")


BUILDINGS = [
    # خانه‌ها (یکی از آن‌ها خانه اصلی است)
    B("lean_to", "home", "house", "کپر چوبی", {}, {"storage": 20, "defense": 2, "guard_slots": 0, "kennel_slots": 0}, 3, 1),
    B("wood_house", "home", "house", "خانه چوبی", {"coins": 50, "wood": 20},
      {"storage": 80, "defense": 6, "guard_slots": 1, "kennel_slots": 1}, 5, 10),
    B("stone_house", "home", "house", "خانه سنگی", {"coins": 150, "stone": 30, "wood": 10},
      {"storage": 150, "defense": 12, "guard_slots": 1, "kennel_slots": 1}, 5, 20),
    B("bunker", "home", "house", "پناهگاه زیرزمینی", {"coins": 200, "stone": 25, "metal": 10},
      {"storage": 120, "defense": 10, "guard_slots": 1, "kennel_slots": 1, "conceal": 4}, 5, 25),
    B("tree_house", "home", "house", "خانه درختی", {"wood": 25, "fiber": 10},
      {"storage": 50, "defense": 5, "guard_slots": 1, "kennel_slots": 0, "conceal": 3}, 5, 12),
    B("cave_hideout", "home", "house", "مخفیگاه داخل غار", {"stone": 15, "wood": 5},
      {"storage": 60, "defense": 6, "guard_slots": 1, "kennel_slots": 1, "conceal": 6}, 5, 12),
    # امکانات دفاعی و جانبی خانه
    B("wall", "home", "wall", "دیوار", {"wood": 10, "stone": 10}, {"defense": 4}, 5, 6),
    B("fence", "home", "fence", "حصار", {"wood": 8}, {"defense": 2}, 3, 3),
    B("barbed", "home", "barbed", "سیم خاردار", {"metal": 4, "fiber": 4}, {"defense": 2, "trap": 3}, 3, 4),
    B("trap", "home", "trap", "تله", {"metal": 5, "wood": 5}, {"trap": 8}, 5, 5),
    B("gate", "home", "gate", "دروازه", {"wood": 10, "metal": 3}, {"defense": 3}, 3, 5),
    B("guard_post", "home", "guard_post", "محل استقرار نگهبان", {"wood": 15, "stone": 5}, {"guard_slots": 1}, 4, 6),
    B("kennel", "home", "kennel", "محل نگهداری حیوانات", {"wood": 15}, {"kennel_slots": 1}, 4, 6),
    B("storage", "home", "storage", "انبار", {"wood": 15, "stone": 5}, {"storage": 60}, 5, 6),
    B("alarm", "home", "alarm", "تجهیزات هشدار", {"metal": 3, "fiber": 5}, {"alarm": 3}, 3, 4),
    # ساختمان‌های قبیله (هزینه از خزانه/انبار قبیله)
    B("t_treasury", "tribe", "treasury", "خزانه", {"coins": 300, "stone": 20}, {"storage": 100}, 3, 30),
    B("t_storage", "tribe", "storage", "انبار قبیله", {"coins": 200, "wood": 30, "stone": 15}, {"storage": 300}, 3, 30),
    B("t_tower", "tribe", "tower", "برج نگهبانی", {"coins": 250, "wood": 25, "stone": 20}, {"defense": 10, "alarm": 2}, 3, 40),
    B("t_wall", "tribe", "wall", "دیوار دفاعی", {"coins": 300, "stone": 40}, {"defense": 15}, 3, 40, {"tribe_level": 2}),
    B("t_workshop", "tribe", "workshop", "کارگاه", {"coins": 250, "wood": 30, "metal": 10}, {"craft": 1}, 3, 40, {"tribe_level": 2}),
    B("t_kennel", "tribe", "kennel", "محل نگهداری حیوانات قبیله", {"coins": 200, "wood": 30}, {"kennel_slots": 2}, 3, 30),
    B("t_barracks", "tribe", "barracks", "ساختمان نیروهای دفاعی", {"coins": 400, "wood": 30, "stone": 30},
      {"guard_slots": 3, "defense": 5}, 3, 50, {"tribe_level": 2}),
]


def Z(code, name, species, hp, power, end, price, perks, desc=""):
    return dict(code=code, name=name, species=species, base_hp=hp, base_power=power, base_endurance=end,
                price=price, perks=perks, description=desc)


BREEDS = [
    Z("german_shepherd", "ژرمن شپرد", "dog", 55, 7, 6, 250, {"guard": 2, "alert": 1, "track": 1}, "نگهبان و ردیاب متعادل"),
    Z("rottweiler", "روتوایلر", "dog", 70, 9, 5, 350, {"guard": 3}, "قوی‌ترین نگهبان؛ ردیابی ضعیف"),
    Z("husky", "هاسکی", "dog", 50, 5, 9, 220, {"carry": 5, "forage": 1}, "پرانرژی؛ کمک در حمل بار و جست‌وجوی منابع"),
    Z("hound", "سگ شکاری", "dog", 40, 5, 7, 200, {"track": 3, "hunt": 2}, "ردیاب و کمک شکار"),
    Z("wolf_pup", "توله‌گرگ", "wolf", 30, 3, 4, 0, {"grows_into": "tame_wolf", "grow_level": 5}, "بعد از تربیت به گرگ تبدیل می‌شود"),
    Z("tame_wolf", "گرگ دست‌آموز", "wolf", 75, 11, 8, 700, {"guard": 1, "hunt": 1, "alert": 1}, "قوی در نبرد"),
    Z("horse", "اسب", "horse", 80, 4, 10, 500, {"carry": 20, "speed": 2}, "ظرفیت حمل بالا و سفر سریع‌تر"),
    Z("falcon", "شاهین", "bird", 20, 3, 4, 300, {"scout": 3, "alert": 2}, "هشدار و اکتشاف؛ در نبرد ضعیف"),
]


def H(code, name, role, hp, power, wage, price, desc=""):
    return dict(code=code, name=name, role=role, hp=hp, power=power, wage=wage, price=price, description=desc)


HIRELINGS = [
    H("guard_basic", "نگهبان معمولی", "guard", 50, 6, 10, 120),
    H("guard_vet", "نگهبان باتجربه", "veteran", 70, 10, 22, 300),
    H("archer", "تیرانداز", "archer", 45, 11, 20, 280),
    H("patrol", "نیروی گشتی", "patrol", 55, 8, 14, 200),
    H("escort", "نیروی همراه", "companion", 60, 8, 15, 220),
]

MISSIONS = [
    dict(code="m_wood", name="تأمین چوب", kind="deliver", target="wood", amount=10, reward_coins=40, reward_xp=15,
         duration_min=180, description="۱۰ چوب تحویل بده"),
    dict(code="m_bread", name="تأمین غذا", kind="deliver", target="bread", amount=3, reward_coins=30, reward_xp=10,
         duration_min=180, description="۳ نان تحویل بده"),
    dict(code="m_hide", name="تأمین پوست", kind="deliver", target="hide", amount=3, reward_coins=45, reward_xp=15,
         duration_min=240, description="۳ پوست تحویل بده"),
    dict(code="m_hunt", name="شکار", kind="activity", target="hunt", amount=2, reward_coins=50, reward_xp=20,
         duration_min=240, description="۲ شکار انجام بده"),
    dict(code="m_gather", name="جمع‌آوری", kind="activity", target="gather", amount=3, reward_coins=35, reward_xp=15,
         duration_min=240, description="۳ بار منبع جمع کن"),
    dict(code="m_explore", name="کاوش", kind="activity", target="explore", amount=1, reward_coins=40, reward_xp=15,
         duration_min=240, description="یک کاوش انجام بده"),
]

TABLES = [("item_defs", ITEMS), ("activity_defs", ACTIVITIES), ("building_defs", BUILDINGS),
          ("animal_breeds", BREEDS), ("hireling_defs", HIRELINGS), ("mission_defs", MISSIONS)]


async def seed_content(pool) -> int:
    added = 0
    async with pool.acquire() as conn:
        for table, rows in TABLES:
            for row in rows:
                cols = list(row.keys())
                ph = []
                vals = []
                for i, c in enumerate(cols, 1):
                    v = row[c]
                    if isinstance(v, (dict, list)):
                        ph.append(f"${i}::jsonb")
                        vals.append(json.dumps(v, ensure_ascii=False))
                    else:
                        ph.append(f"${i}")
                        vals.append(Decimal(str(v)) if isinstance(v, float) else v)
                sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(ph)}) ON CONFLICT (code) DO NOTHING"
                res = await conn.execute(sql, *vals)
                if res.endswith("1"):
                    added += 1
    return added
