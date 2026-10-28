"""استخدام نیرو، تجهیز از انبار شخصی، دستمزد، استقرار."""
from __future__ import annotations

from datetime import timedelta

from app.services import homes
from app.services import inventory as inv
from app.services.chars import now
from app.services.economy import InsufficientFunds, change_coins
from app.services.errors import GameError

POST_FA = {"idle": "آماده‌باش", "entrance": "نگهبان ورودی", "storage": "نگهبان انبار",
           "companion": "همراه شخصیت", "tribe_defense": "دفاع از قبیله"}
STANCE_FA = {"defend": "دفاع", "hold": "نگهداری موقعیت", "capture": "تلاش برای دستگیری"}
GEAR_SLOTS = ("weapon", "armor", "shield")


async def list_hirelings(conn, user_id: int):
    return await conn.fetch(
        "SELECT h.*, d.name, d.role, d.wage FROM hirelings h JOIN hireling_defs d ON d.id=h.def_id "
        "WHERE h.owner_user_id=$1 AND h.status IN ('active','unpaid') ORDER BY h.id", user_id)


async def settle_hirelings(conn, settings, user_id: int) -> list:
    msgs, t = [], now()
    heal = float(settings.get("animals.heal_per_hour"))
    cap_days = int(settings.get("hirelings.wage_catchup_days"))
    for h in await conn.fetch(
            "SELECT h.*, d.name, d.wage FROM hirelings h JOIN hireling_defs d ON d.id=h.def_id "
            "WHERE h.owner_user_id=$1 AND h.status IN ('active','unpaid') FOR UPDATE OF h", user_id):
        paid, status = h["wage_paid_until"], h["status"]
        floor = t - timedelta(days=cap_days)
        if paid < floor:
            paid = floor                      # بدهی بیش از سقف، بدون پیامد جمع نمی‌شود
        while paid <= t:
            try:
                await change_coins(conn, user_id, -h["wage"], "wage", ref=str(h["id"]))
                paid += timedelta(days=1)
                if status == "unpaid":
                    status = "active"
            except InsufficientFunds:
                if status == "active":
                    msgs.append(f"⚠️ دستمزد {h['name']} پرداخت نشد؛ تا پرداخت غیرفعال است")
                status = "unpaid"
                break
        hp = h["hp"]
        hours = (t - h["hp_updated_at"]).total_seconds() / 3600
        if hp < h["max_hp"] and hours > 0:
            hp = min(h["max_hp"], hp + int(heal * hours))
        await conn.execute("UPDATE hirelings SET wage_paid_until=$2, status=$3, hp=$4, hp_updated_at=$5 WHERE id=$1",
                           h["id"], paid, status, hp, t)
    return msgs


async def slots_left(conn, settings, user_id: int) -> int:
    st = await homes.home_stats(conn, settings, user_id)
    n = await conn.fetchval("SELECT count(*) FROM hirelings WHERE owner_user_id=$1 AND status IN ('active','unpaid')", user_id)
    return st["guard_slots"] - n


async def hire(conn, settings, user_id: int, code: str) -> str:
    d = await conn.fetchrow("SELECT * FROM hireling_defs WHERE code=$1 AND is_active", code)
    if d is None:
        raise GameError("این نیرو موجود نیست.")
    if await slots_left(conn, settings, user_id) <= 0:
        raise GameError("ظرفیت نیروی فعال پر است؛ محل استقرار نگهبان بساز یا خانه را ارتقا بده.")
    try:
        await change_coins(conn, user_id, -d["price"], "hire", ref=code)
    except InsufficientFunds as exc:
        raise GameError("سکه کافی نداری.") from exc
    await conn.execute(
        "INSERT INTO hirelings(owner_user_id, def_id, hp, max_hp, power, wage_paid_until) VALUES($1,$2,$3,$3,$4,$5)",
        user_id, d["id"], d["hp"], d["power"], now() + timedelta(days=1))
    return f"🛡 {d['name']} استخدام شد."


async def dismiss(conn, user_id: int, hid: int):
    h = await conn.fetchrow("SELECT * FROM hirelings WHERE id=$1 AND owner_user_id=$2 AND status IN ('active','unpaid') FOR UPDATE",
                            hid, user_id)
    if h is None:
        raise GameError("نیرو پیدا نشد.")
    for it in await conn.fetch("SELECT id FROM item_instances WHERE holder_type='guard' AND holder_id=$1", hid):
        await inv.move_instance(conn, it["id"], "guard", hid, "storage", user_id, "dismiss")
    await conn.execute("UPDATE hirelings SET status='dismissed', post='idle' WHERE id=$1", hid)


async def gear_of(conn, hid: int):
    return await conn.fetch(
        "SELECT i.id, i.slot, i.durability, d.name, d.power, d.defense, d.max_durability FROM item_instances i "
        "JOIN item_defs d ON d.id=i.def_id WHERE i.holder_type='guard' AND i.holder_id=$1", hid)


async def equip(conn, user_id: int, hid: int, iid):
    h = await conn.fetchrow("SELECT id FROM hirelings WHERE id=$1 AND owner_user_id=$2 AND status IN ('active','unpaid') FOR UPDATE",
                            hid, user_id)
    inst = await conn.fetchrow(
        "SELECT i.*, d.kind FROM item_instances i JOIN item_defs d ON d.id=i.def_id WHERE i.id=$1 FOR UPDATE", iid)
    if h is None or inst is None or inst["owner_user_id"] != user_id or inst["holder_type"] not in ("backpack", "storage") \
            or inst["holder_id"] != user_id:
        raise GameError("این وسیله در اختیار تو نیست.")
    if inst["kind"] not in GEAR_SLOTS:
        raise GameError("فقط سلاح، زره و سپر قابل دادن به نیرو است.")
    old = await conn.fetchval("SELECT id FROM item_instances WHERE holder_type='guard' AND holder_id=$1 AND slot=$2 FOR UPDATE",
                              hid, inst["kind"])
    if old:
        await inv.move_instance(conn, old, "guard", hid, "storage", user_id, "swap_gear")
    await inv.move_instance(conn, iid, inst["holder_type"], user_id, "guard", hid, "give_to_hireling", inst["kind"])


async def unequip(conn, user_id: int, hid: int, iid):
    h = await conn.fetchval("SELECT 1 FROM hirelings WHERE id=$1 AND owner_user_id=$2", hid, user_id)
    if not h:
        raise GameError("نیرو پیدا نشد.")
    await inv.move_instance(conn, iid, "guard", hid, "storage", user_id, "take_back")


async def set_post(conn, settings, user_id: int, hid: int, post: str):
    if post not in POST_FA:
        raise GameError("وظیفه نامعتبر.")
    if post == "companion":
        n = await conn.fetchval("SELECT count(*) FROM hirelings WHERE owner_user_id=$1 AND post='companion' "
                                "AND status='active' AND id<>$2", user_id, hid)
        if n >= int(settings.get("companions.max_hirelings")):
            raise GameError("تعداد نیروی همراه به سقف رسیده است.")
    r = await conn.execute("UPDATE hirelings SET post=$3 WHERE id=$1 AND owner_user_id=$2 AND status IN ('active','unpaid')",
                           hid, user_id, post)
    if r.endswith("0"):
        raise GameError("نیرو پیدا نشد.")


async def set_stance(conn, user_id: int, hid: int, stance: str):
    if stance not in STANCE_FA:
        raise GameError("دستور نامعتبر.")
    await conn.execute("UPDATE hirelings SET stance=$3 WHERE id=$1 AND owner_user_id=$2", hid, user_id, stance)


async def _part(conn, h):
    gear = await gear_of(conn, h["id"])
    power = float(h["power"])
    defense = 0.0
    names = []
    for g in gear:
        f = inv.dur_factor(g["durability"], g["max_durability"])
        power += g["power"] * f
        defense += g["defense"] * f
        names.append(g["name"])
    return {"part": {"k": "hireling", "id": h["id"], "name": h["name"], "hp": h["hp"], "max": h["max_hp"],
                     "power": power, "def": defense, "stance": h["stance"]}, "gear": names}


async def _by_posts(conn, user_id: int, posts: tuple):
    rows = await conn.fetch(
        "SELECT h.*, d.name FROM hirelings h JOIN hireling_defs d ON d.id=h.def_id "
        "WHERE h.owner_user_id=$1 AND h.status='active' AND h.hp > 0 AND h.post = ANY($2::text[])", user_id, list(posts))
    return [await _part(conn, h) for h in rows]


async def guards_at_home(conn, settings, user_id: int):
    return await _by_posts(conn, user_id, ("entrance", "storage"))


async def companions(conn, settings, user_id: int):
    return await _by_posts(conn, user_id, ("companion",))


async def tribe_defenders(conn, tribe_id: int):
    rows = await conn.fetch(
        "SELECT h.*, d.name FROM hirelings h JOIN hireling_defs d ON d.id=h.def_id "
        "JOIN tribe_members m ON m.user_id=h.owner_user_id WHERE m.tribe_id=$1 AND h.status='active' AND h.hp > 0 "
        "AND h.post='tribe_defense'", tribe_id)
    return [await _part(conn, h) for h in rows]


async def apply_result(conn, part: dict, drop_id: int = None):
    """بعد از نبرد: سلامتی/مرگ نیرو؛ در صورت مرگ، تجهیزات او در محل مرگ می‌ماند."""
    if part["hp"] <= 0:
        await conn.execute("UPDATE hirelings SET hp=0, status='dead', post='idle' WHERE id=$1", part["id"])
        for it in await conn.fetch("SELECT id FROM item_instances WHERE holder_type='guard' AND holder_id=$1", part["id"]):
            if drop_id:
                await inv.move_instance(conn, it["id"], "guard", part["id"], "ground", drop_id, "hireling_died")
            else:
                await inv.destroy_instance(conn, it["id"], "hireling_died")
    else:
        await conn.execute("UPDATE hirelings SET hp=$2, hp_updated_at=now() WHERE id=$1", part["id"], part["hp"])
