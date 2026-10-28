"""حیوانات: خرید، تربیت، وظیفه، شرکت در نبرد."""
from __future__ import annotations

from datetime import timedelta

from app.services import homes
from app.services import inventory as inv
from app.services.chars import jload, now
from app.services.economy import change_coins
from app.services.errors import GameError

ROLE_FA = {"idle": "استراحت", "guard": "نگهبان خانه", "companion": "همراه شخصیت"}


def xp_needed(level: int) -> int:
    return level * 20


async def list_animals(conn, user_id: int):
    return await conn.fetch(
        "SELECT a.*, b.name AS breed, b.species, b.perks, b.code AS breed_code FROM animals a "
        "JOIN animal_breeds b ON b.id=a.breed_id WHERE a.owner_user_id=$1 AND a.status='ok' ORDER BY a.id", user_id)


async def settle_animals(conn, settings, user_id: int) -> list:
    msgs, t = [], now()
    rate = float(settings.get("animals.heal_per_hour"))
    for a in await conn.fetch(
            "SELECT a.*, b.name AS breed, b.perks, b.code AS bcode FROM animals a JOIN animal_breeds b ON b.id=a.breed_id "
            "WHERE a.owner_user_id=$1 AND a.status='ok' FOR UPDATE OF a", user_id):
        hp = a["hp"]
        hours = (t - a["hp_updated_at"]).total_seconds() / 3600
        if hp < a["max_hp"] and hours > 0:
            hp = min(a["max_hp"], hp + int(rate * hours))
        lvl, xp, power, endu, mhp, loyalty = a["level"], a["xp"], a["power"], a["endurance"], a["max_hp"], a["loyalty"]
        train_until = a["train_until"]
        if train_until and train_until <= t:
            xp += 12 + loyalty // 20
            loyalty = min(100, loyalty + 6)
            train_until = None
            msgs.append(f"🐕 تمرین {a['name']} تمام شد")
            while xp >= xp_needed(lvl):
                xp -= xp_needed(lvl)
                lvl += 1
                power += 1
                endu += 1
                mhp += 5
                msgs.append(f"⭐️ {a['name']} به سطح {lvl} رسید")
        breed_id = a["breed_id"]
        perks = jload(a["perks"])
        if perks.get("grows_into") and lvl >= int(perks.get("grow_level", 5)):
            nb = await conn.fetchrow("SELECT * FROM animal_breeds WHERE code=$1", perks["grows_into"])
            if nb:
                breed_id, power, endu, mhp = nb["id"], max(power, nb["base_power"]), max(endu, nb["base_endurance"]), max(mhp, nb["base_hp"])
                msgs.append(f"🐺 {a['name']} به {nb['name']} تبدیل شد!")
        await conn.execute(
            "UPDATE animals SET hp=$2, hp_updated_at=$3, level=$4, xp=$5, power=$6, endurance=$7, max_hp=$8, loyalty=$9, "
            "train_until=$10, breed_id=$11 WHERE id=$1", a["id"], hp, t, lvl, xp, power, endu, mhp, loyalty, train_until, breed_id)
    return msgs


async def capacity_left(conn, settings, user_id: int) -> int:
    st = await homes.home_stats(conn, settings, user_id)
    n = await conn.fetchval("SELECT count(*) FROM animals WHERE owner_user_id=$1 AND status='ok'", user_id)
    return st["kennel_slots"] - n


async def _create(conn, user_id, breed, name, loyalty):
    return await conn.fetchval(
        "INSERT INTO animals(owner_user_id, breed_id, name, hp, max_hp, power, endurance, loyalty) "
        "VALUES($1,$2,$3,$4,$4,$5,$6,$7) RETURNING id",
        user_id, breed["id"], name, breed["base_hp"], breed["base_power"], breed["base_endurance"], loyalty)


async def buy(conn, settings, user_id: int, breed_code: str) -> str:
    b = await conn.fetchrow("SELECT * FROM animal_breeds WHERE code=$1 AND is_active AND price > 0", breed_code)
    if b is None:
        raise GameError("این نژاد برای فروش نیست.")
    if await capacity_left(conn, settings, user_id) <= 0:
        raise GameError("جای نگهداری حیوان نداری؛ محل نگهداری حیوانات را بساز یا ارتقا بده.")
    try:
        await change_coins(conn, user_id, -b["price"], "buy_animal", ref=breed_code)
    except Exception as exc:  # noqa: BLE001
        raise GameError("سکه کافی نداری.") from exc
    n = await conn.fetchval("SELECT count(*) FROM animals WHERE owner_user_id=$1", user_id)
    await _create(conn, user_id, b, f"{b['name']} {n + 1}", 60)
    return f"🐾 {b['name']} خریداری شد."


async def add_wild(conn, settings, user_id: int, breed_code: str) -> str:
    b = await conn.fetchrow("SELECT * FROM animal_breeds WHERE code=$1", breed_code)
    if b is None:
        return ""
    if await capacity_left(conn, settings, user_id) <= 0:
        return f"🐺 یک {b['name']} پیدا کردی ولی جای نگهداری نداشتی و رفت."
    n = await conn.fetchval("SELECT count(*) FROM animals WHERE owner_user_id=$1", user_id)
    await _create(conn, user_id, b, f"{b['name']} {n + 1}", int(settings.get("animals.tame_loyalty")))
    return f"🐺 یک {b['name']} پیدا کردی و همراهت شد! (برای تربیت، از منوی حیوانات استفاده کن)"


async def set_role(conn, settings, user_id: int, aid: int, role: str):
    if role not in ROLE_FA:
        raise GameError("وظیفه نامعتبر.")
    a = await conn.fetchrow("SELECT * FROM animals WHERE id=$1 AND owner_user_id=$2 AND status='ok' FOR UPDATE", aid, user_id)
    if a is None:
        raise GameError("حیوان پیدا نشد.")
    if role == "companion":
        n = await conn.fetchval("SELECT count(*) FROM animals WHERE owner_user_id=$1 AND role='companion' AND status='ok' AND id<>$2",
                                user_id, aid)
        if n >= int(settings.get("companions.max_animals")):
            raise GameError("تعداد حیوان همراه به سقف رسیده است.")
    await conn.execute("UPDATE animals SET role=$2 WHERE id=$1", aid, role)


async def feed(conn, user_id: int, aid: int) -> str:
    a = await conn.fetchrow("SELECT * FROM animals WHERE id=$1 AND owner_user_id=$2 AND status='ok' FOR UPDATE", aid, user_id)
    if a is None:
        raise GameError("حیوان پیدا نشد.")
    for code in ("meat_cooked", "meat_raw", "bread"):
        d = await conn.fetchrow("SELECT id, name FROM item_defs WHERE code=$1", code)
        if d and await inv.stack_qty(conn, "backpack", user_id, d["id"]) > 0:
            await inv.take_stack(conn, "backpack", user_id, d["id"], 1, "feed_animal")
            await conn.execute("UPDATE animals SET hp=LEAST(max_hp, hp+15), loyalty=LEAST(100, loyalty+5) WHERE id=$1", aid)
            return f"🍖 {a['name']} با {d['name']} سیر شد."
    raise GameError("غذایی در کوله‌پشتی نداری (گوشت یا نان).")


async def train(conn, settings, user_id: int, ch, aid: int) -> str:
    a = await conn.fetchrow("SELECT * FROM animals WHERE id=$1 AND owner_user_id=$2 AND status='ok' FOR UPDATE", aid, user_id)
    if a is None:
        raise GameError("حیوان پیدا نشد.")
    if a["train_until"] and a["train_until"] > now():
        raise GameError("در حال تمرین است.")
    cost = int(settings.get("animals.train_energy"))
    if ch["energy"] < cost:
        raise GameError("انرژی کافی برای تمرین نداری.")
    await conn.execute("UPDATE characters SET energy=energy-$2 WHERE user_id=$1", user_id, cost)
    minutes = int(settings.get("animals.train_minutes"))
    await conn.execute("UPDATE animals SET train_until=$2 WHERE id=$1", aid, now() + timedelta(minutes=minutes))
    return f"🎯 تمرین {a['name']} شروع شد ({minutes} دقیقه)."


def to_part(a, breed_name: str, perks: dict, role_guard: bool, obey_loyalty: int) -> dict:
    power = float(a["power"]) * (1 + 0.1 * perks.get("guard", 0) * (1 if role_guard else 0))
    if a["loyalty"] < obey_loyalty:
        power *= 0.5
    return {"k": "animal", "id": a["id"], "name": a["name"], "hp": a["hp"], "max": a["max_hp"], "power": power,
            "def": float(a["endurance"]) * 0.3, "breed": breed_name}


async def _by_role(conn, settings, user_id: int, role: str):
    rows = await conn.fetch(
        "SELECT a.*, b.name AS bname, b.perks FROM animals a JOIN animal_breeds b ON b.id=a.breed_id "
        "WHERE a.owner_user_id=$1 AND a.status='ok' AND a.role=$2 AND a.hp > 0", user_id, role)
    obey = int(settings.get("animals.obey_loyalty"))
    return [{"part": to_part(a, a["bname"], jload(a["perks"]), role == "guard", obey), "breed": a["bname"],
             "perks": jload(a["perks"])} for a in rows]


async def guards_at_home(conn, settings, user_id: int):
    return await _by_role(conn, settings, user_id, "guard")


async def companions(conn, settings, user_id: int):
    return await _by_role(conn, settings, user_id, "companion")


def perk_total(items: list, perk: str) -> int:
    return sum(int(i["perks"].get(perk, 0)) for i in items)


async def apply_result(conn, part: dict):
    """بعد از نبرد: سلامتی یا مرگ حیوان را ذخیره می‌کند."""
    if part["hp"] <= 0:
        await conn.execute("UPDATE animals SET hp=0, status='dead', role='idle' WHERE id=$1", part["id"])
    else:
        await conn.execute("UPDATE animals SET hp=$2, hp_updated_at=now() WHERE id=$1", part["id"], part["hp"])
