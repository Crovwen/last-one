"""ثبت‌نام بازیکن و ساخت شخصیت — مستقل از تلگرام."""
from __future__ import annotations

import json
import re

import asyncpg

from app.services.economy import change_coins
from app.services.settings import SKILLS, SettingsService

_NAME_RE = re.compile(r"^[\w ]+$")
STATE_AWAITING_NAME = "awaiting_name"


def normalize_name(raw: str) -> str:
    return re.sub(r"\s+", " ", raw.strip())


def validate_name(raw: str, min_len: int, max_len: int):
    """اگر نام معتبر نباشد پیام خطای فارسی برمی‌گرداند، وگرنه None."""
    name = normalize_name(raw)
    if len(name) < min_len or len(name) > max_len:
        return f"نام باید بین {min_len} تا {max_len} حرف باشد."
    if not _NAME_RE.match(name):
        return "نام فقط می‌تواند شامل حروف، عدد، فاصله و _ باشد."
    if name.isdigit():
        return "نام نمی‌تواند فقط عدد باشد."
    return None


async def get_or_create_user(pool: asyncpg.Pool, settings: SettingsService, tg_user):
    """کاربر را می‌سازد (با سکه اولیه) یا به‌روز می‌کند. (ردیف کاربر, تازه_ساخته_شده) را برمی‌گرداند."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "INSERT INTO users(telegram_id, username, first_name, state) VALUES($1, $2, $3, $4) "
                "ON CONFLICT (telegram_id) DO UPDATE SET username = EXCLUDED.username, "
                "first_name = EXCLUDED.first_name, last_seen_at = now() "
                "RETURNING *, (xmax = 0) AS created",
                tg_user.id, tg_user.username, tg_user.first_name, STATE_AWAITING_NAME,
            )
            created = row["created"]
            if created:
                bonus = int(settings.get("economy.start_coins"))
                if bonus > 0:
                    await change_coins(conn, row["id"], bonus, "welcome_bonus")
                    if settings.get("economy.welcome_locked"):
                        await conn.execute("UPDATE users SET locked_coins=$2 WHERE id=$1", row["id"], bonus)
                row = await conn.fetchrow("SELECT *, false AS created FROM users WHERE id = $1", row["id"])
            return row, created


async def grant_starter_kit(conn, user_id: int):
    """لباس پایه (غیرقابل‌غارت)، چاقوی اولیه، کمی غذا و آب، و کپر رایگان."""
    from app.services import homes
    from app.services import inventory as inv
    clothes = await inv.def_by_code(conn, "base_clothes")
    await inv.create_instance(conn, clothes["id"], user_id, "equipped", user_id, "starter", bound=True)
    await conn.execute("UPDATE item_instances SET slot='clothing' WHERE holder_type='equipped' AND holder_id=$1 AND slot IS NULL", user_id)
    knife = await inv.def_by_code(conn, "knife")
    kid = await inv.create_instance(conn, knife["id"], user_id, "backpack", user_id, "starter", bound=True)
    await inv.equip(conn, user_id, kid)
    for code, n in (("bread", 3), ("water", 3)):
        d = await inv.def_by_code(conn, code)
        await inv.add_stack(conn, "backpack", user_id, d["id"], n, "starter")
    await homes.ensure_starter_home(conn, user_id)


async def get_user(pool: asyncpg.Pool, telegram_id: int):
    return await pool.fetchrow("SELECT * FROM users WHERE telegram_id = $1", telegram_id)


async def get_character(pool: asyncpg.Pool, user_id: int):
    return await pool.fetchrow("SELECT * FROM characters WHERE user_id = $1", user_id)


async def create_character(pool: asyncpg.Pool, settings: SettingsService, user_id: int, raw_name: str):
    """(موفق, پیام_خطا) را برمی‌گرداند."""
    err = validate_name(
        raw_name,
        int(settings.get("character.name_min_len")),
        int(settings.get("character.name_max_len")),
    )
    if err:
        return False, err
    name = normalize_name(raw_name)
    lvl = int(settings.get("character.start_skill_level"))
    skills = {k: lvl for k in SKILLS}
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                if await conn.fetchval("SELECT 1 FROM characters WHERE user_id = $1", user_id):
                    return False, "شخصیت قبلاً ساخته شده است."
                await conn.execute(
                    "INSERT INTO characters(user_id, name, health, hunger, thirst, energy, fatigue, skills) "
                    "VALUES($1, $2, $3, $4, $5, $6, $7, $8::jsonb)",
                    user_id, name,
                    int(settings.get("character.start_health")),
                    int(settings.get("character.start_hunger")),
                    int(settings.get("character.start_thirst")),
                    int(settings.get("character.start_energy")),
                    int(settings.get("character.start_fatigue")),
                    json.dumps(skills),
                )
                hrs = int(settings.get("newbie.protection_hours"))
                await conn.execute("UPDATE users SET state = NULL, state_data = '{}'::jsonb, "
                                   "protected_until = now() + make_interval(hours => $2) WHERE id = $1", user_id, hrs)
                await grant_starter_kit(conn, user_id)
    except asyncpg.UniqueViolationError:
        return False, "این نام قبلاً گرفته شده، نام دیگری انتخاب کن."
    return True, None
