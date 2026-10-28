"""تنظیمات قابل‌تغییر بازی. هر عدد بازی فقط اینجا (مقدار پیش‌فرض) و در جدول game_settings (مقدار فعلی) است."""
from __future__ import annotations

import json

import asyncpg

# کلید: (مقدار پیش‌فرض, توضیح فارسی)
DEFAULTS = {
    "economy.start_coins": (100, "سکه اولیه بازیکن جدید"),
    "character.name_min_len": (3, "حداقل طول نام شخصیت"),
    "character.name_max_len": (16, "حداکثر طول نام شخصیت"),
    "character.start_health": (100, "سلامتی اولیه"),
    "character.start_hunger": (80, "سیری اولیه (۱۰۰ = کاملاً سیر)"),
    "character.start_thirst": (80, "سیرابی اولیه (۱۰۰ = کاملاً سیراب)"),
    "character.start_energy": (100, "انرژی اولیه"),
    "character.start_fatigue": (0, "خستگی اولیه"),
    "character.start_skill_level": (1, "سطح اولیه هر مهارت"),
    "newbie.protection_hours": (24, "مدت محافظت اولیه تازه‌واردها (ساعت)"),
    "backup.interval_hours": (24, "فاصله پشتیبان‌گیری خودکار (ساعت)"),
}

DEFAULTS.update({
    # نیازهای حیاتی
    "vitals.hunger_per_hour": (4, "کاهش سیری در هر ساعت"),
    "vitals.thirst_per_hour": (6, "کاهش سیرابی در هر ساعت"),
    "vitals.fatigue_per_hour": (2, "افزایش خستگی در هر ساعت فعالیت"),
    "vitals.energy_idle_per_hour": (6, "بازیابی انرژی در ساعت (در خانه)"),
    "vitals.energy_rest_per_hour": (20, "بازیابی انرژی در ساعت استراحت"),
    "vitals.fatigue_rest_per_hour": (15, "کاهش خستگی در ساعت استراحت"),
    "vitals.health_regen_per_hour": (3, "بازیابی سلامتی در ساعت"),
    "vitals.starve_damage_per_hour": (6, "آسیب گرسنگی/تشنگی در ساعت (هرگز کشنده نیست)"),
    "stance.cautious_energy_mult": (1.3, "ضریب انرژی حالت محتاط"),
    "stance.cautious_risk_mult": (0.7, "ضریب ریسک حالت محتاط"),
    "stance.cautious_stealth_bonus": (3, "بونوس مخفی‌کاری حالت محتاط"),
    "stance.fast_time_mult": (0.75, "ضریب زمان حالت سریع"),
    "stance.fast_risk_mult": (1.3, "ضریب ریسک حالت سریع"),
    "rest.fast_cost_per_hour": (20, "هزینه سکه‌ای استراحت سریع (برای هر ساعت)"),
    "rest.max_hours": (8, "حداکثر ساعت استراحت"),
    # شب و روز و آب‌وهوا
    "env.tz_offset_min": (210, "اختلاف ساعت بازی با UTC (دقیقه)؛ ۲۱۰ = تهران"),
    "env.night_start_hour": (21, "ساعت شروع شب"),
    "env.night_end_hour": (5, "ساعت پایان شب"),
    "env.night_energy_mult": (1.15, "ضریب انرژی فعالیت در شب"),
    "env.night_risk_mult": (1.2, "ضریب ریسک فعالیت در شب"),
    "env.night_stealth_bonus": (2, "بونوس مخفی‌کاری در شب"),
    "env.bad_weather_energy_mult": (1.1, "ضریب انرژی در باران/طوفان/سرما"),
    "env.cold_hunger_mult": (1.3, "ضریب گرسنگی در هوای سرد"),
    # حمل بار
    "carry.base_capacity": (30.0, "ظرفیت پایه حمل (کیلو)"),
    "carry.per_skill": (2.0, "ظرفیت اضافه به‌ازای هر سطح مهارت حمل"),
    # رشد
    "skills.xp_base": (10, "پایه تجربه لازم مهارت (سطح²×پایه)"),
    "skills.max_level": (50, "حداکثر سطح مهارت"),
    "character.xp_base": (100, "تجربه لازم سطح شخصیت (سطح×پایه)"),
    "account.xp_per_activity": (2, "تجربه حساب به‌ازای هر فعالیت"),
    "account.xp_base": (50, "تجربه لازم سطح حساب"),
    # همراهان
    "companions.max_animals": (1, "حداکثر حیوان همراه شخصیت"),
    "companions.max_hirelings": (1, "حداکثر نیروی همراه شخصیت"),
    # مرگ
    "death.respawn_minutes": (10, "زمان بازگشت عادی بعد از مرگ (دقیقه)"),
    "death.fast_respawn_cost": (50, "هزینه بازگشت سریع (سکه)"),
    "drop.expire_hours": (24, "ماندگاری وسایل محل مرگ (ساعت)"),
    # مبارزه
    "combat.round_timeout_sec": (90, "مهلت هر مرحله نبرد (ثانیه)"),
    "combat.dmg_k": (2.5, "ضریب آسیب هر دور"),
    "combat.armor_k": (0.15, "اثر زره روی آسیب"),
    "combat.luck_range": (0.4, "دامنه شانس هر دور (±)"),
    "combat.advantage_k": (0.5, "اثر برتری قدرت روی سهم آسیب (۰ تا ۱)"),
    "combat.ambush_att_mult": (1.35, "ضریب مهاجم در دور اول غافلگیری موفق"),
    "combat.ambush_fail_def_mult": (1.3, "ضریب مدافع وقتی غافلگیری لو رفت"),
    "combat.siege_escape_penalty": (0.2, "کاهش شانس فرار مدافع در محاصره"),
    "combat.pursue_att_mult": (0.9, "ضریب مهاجم در تعقیب"),
    "combat.escape_base": (0.45, "شانس پایه فرار"),
    "combat.capture_chance": (0.6, "شانس اسارت بازیکن شکست‌خورده"),
    "combat.repeat_cooldown_min": (60, "فاصله حمله مجدد یک مهاجم به یک هدف (دقیقه)"),
    "combat.recon_extra_energy": (3, "انرژی بررسی بیشتر"),
    "combat.attack_energy": (10, "انرژی لازم برای شروع حمله"),
    "combat.flee_fatigue": (10, "خستگی بعد از فرار"),
    "death.return_minutes": (10, "زمان بازگشت عادی به محل مرگ (دقیقه)"),
    "death.return_fast_cost": (30, "هزینه بازگشت سریع به محل مرگ"),
    "death.respawn_health": (50, "سلامتی بعد از بازگشت از مرگ"),
    "combat.loot_minutes": (10, "مهلت مرحله غارت (دقیقه)"),
    "combat.durability_loss": (3, "افت دوام تجهیز در هر دور"),
    "loot.steps": (3, "حداکثر مرحله‌های غارت خانه"),
    "loot.step_items": (3, "حداکثر نوع آیتم در هر مرحله غارت"),
    "loot.base_success": (0.8, "شانس موفقیت مرحله اول غارت"),
    "loot.step_penalty": (0.15, "کاهش شانس در هر مرحله بعدی"),
    "loot.max_storage_fraction": (0.3, "حداکثر کسر انبار قابل غارت در هر مرحله"),
    "loot.tribe_coin_fraction": (0.1, "کسر خزانه قبیله قابل غارت"),
    # اسارت
    "captivity.max_hours": (12, "سقف مدت اسارت (ساعت)"),
    "captivity.immunity_hours": (6, "مصونیت بعد از آزادی از اسارت (ساعت)"),
    "captivity.escape_cooldown_min": (30, "فاصله تلاش‌های فرار از اسارت (دقیقه)"),
    "captivity.ransom_max": (2000, "سقف باج"),
    "captivity.task_presets": ([["wood", 10], ["stone", 8], ["bread", 5]], "کارهای آزادی (کد آیتم، تعداد)"),
    "rescue.base_chance": (0.35, "شانس پایه نجات اسیر"),
    # خانه و نیرو
    "home.base_storage": (200.0, "ظرفیت پایه انبار خانه (کیلو)"),
    "home.base_guard_slots": (1, "جای نگهبان بدون ساختمان"),
    "home.base_kennel_slots": (1, "جای حیوان بدون ساختمان"),
    "hirelings.wage_catchup_days": (2, "حداکثر روزهای دستمزد انباشته هنگام غیبت"),
    "animals.heal_per_hour": (8, "بهبود سلامتی حیوان در ساعت"),
    "animals.train_minutes": (30, "مدت تمرین حیوان (دقیقه)"),
    "animals.train_energy": (8, "انرژی لازم برای تمرین"),
    "animals.tame_loyalty": (30, "وفاداری اولیه حیوان وحشی"),
    "animals.obey_loyalty": (40, "حداقل وفاداری برای اطاعت"),
    # قبیله
    "tribe.found_cost": (500, "هزینه تأسیس قبیله"),
    "tribe.levels": ({"1": {"members": 10, "cost": 0}, "2": {"members": 20, "cost": 2000}, "3": {"members": 40, "cost": 8000}}, "سطح‌های قبیله: ظرفیت و هزینه ارتقا از خزانه"),
    "tribe.loan_compensation_pct": (50, "درصد غرامت تجهیز امانی نابودشده از خزانه"),
    # بازار
    "market.fee_pct": (5, "کارمزد فروش بازیکن‌به‌بازیکن (درصد)"),
    "market.price_min_pct": (50, "حداقل قیمت مجاز (درصد ارزش)"),
    "market.price_max_pct": (300, "حداکثر قیمت مجاز (درصد ارزش)"),
    "market.sell_to_npc_pct": (40, "درصد ارزش در فروش به بازار سیستمی"),
    "market.max_listings": (10, "حداکثر آگهی فعال هر بازیکن"),
    "trade.expire_minutes": (30, "مهلت معامله امن"),
    "economy.welcome_locked": (True, "سکه اولیه قابل انتقال نباشد"),
    # مأموریت، رویداد
    "missions.cooldown_min": (30, "فاصله مأموریت‌های تازه"),
    "events.auto_interval_hours": (6, "فاصله رویداد خودکار گروه‌ها (ساعت؛ ۰ = خاموش)"),
    "events.duration_min": (10, "مدت هر رویداد"),
    "events.cargo_slots": (3, "تعداد برداشت‌کننده محموله"),
    "events.cargo_coins": (60, "ارزش سکه‌ای هر محموله"),
    "events.beast_hp": (120, "قدرت جانور رویداد گروهی"),
    "events.beast_reward": (40, "پاداش سکه‌ای هر شرکت‌کننده رویداد جانور"),
    "report.max_open": (5, "حداکثر گزارش تخلف باز هر کاربر"),
})

SKILLS = {
    "strength": "قدرت بدنی",
    "endurance": "استقامت",
    "perception": "ادراک و هوشیاری",
    "stealth": "مخفی‌کاری",
    "combat": "مهارت مبارزه",
    "marksmanship": "تیراندازی",
    "animal_handling": "کار با حیوانات",
    "gathering": "جمع‌آوری منابع",
    "building": "ساخت‌وساز",
    "carry": "ظرفیت حمل بار",
}


class SettingsError(ValueError):
    pass


def check_value(key: str, value) -> None:
    """نوع مقدار باید با نوع پیش‌فرض یکی باشد."""
    if key not in DEFAULTS:
        raise SettingsError("کلید ناشناخته")
    default = DEFAULTS[key][0]
    if isinstance(default, bool) or isinstance(value, bool):
        ok = isinstance(value, bool) and isinstance(default, bool)
    elif isinstance(default, int):
        ok = isinstance(value, int)
    elif isinstance(default, float):
        ok = isinstance(value, (int, float))
    else:
        ok = isinstance(value, type(default))
    if not ok:
        raise SettingsError("نوع مقدار با نوع تنظیم یکی نیست")


class SettingsService:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool
        self._cache = {}

    async def seed_and_load(self) -> None:
        async with self._pool.acquire() as conn:
            for key, (value, desc) in DEFAULTS.items():
                await conn.execute(
                    "INSERT INTO game_settings(key, value, description) VALUES($1, $2::jsonb, $3) "
                    "ON CONFLICT (key) DO UPDATE SET description = EXCLUDED.description",
                    key, json.dumps(value), desc,
                )
            rows = await conn.fetch("SELECT key, value FROM game_settings")
        self._cache = {r["key"]: json.loads(r["value"]) for r in rows}

    def get(self, key: str):
        if key in self._cache:
            return self._cache[key]
        return DEFAULTS[key][0]

    def all(self) -> dict:
        return {k: self.get(k) for k in DEFAULTS}

    async def set(self, key: str, value, actor: str) -> None:
        check_value(key, value)
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE game_settings SET value = $2::jsonb, updated_at = now() WHERE key = $1",
                    key, json.dumps(value),
                )
                await conn.execute(
                    "INSERT INTO audit_log(actor, action, data) VALUES($1, 'setting_changed', $2::jsonb)",
                    actor, json.dumps({"key": key, "value": value}),
                )
        self._cache[key] = value
