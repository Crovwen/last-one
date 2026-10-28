"""موجودی، کوله‌پشتی، انبار و تجهیزات. همه توابع داخل تراکنش صدا زده می‌شوند.
هر جابه‌جایی آیتم در item_events ثبت می‌شود و با شرط‌های اتمیک از تکثیر جلوگیری می‌شود."""
from __future__ import annotations

import json

from app.services.errors import GameError
from app.services.rules import carry_capacity, clamp

SLOT_BY_KIND = {"weapon": "weapon", "armor": "armor", "shield": "shield", "bag": "bag",
                "clothing": "clothing", "tool": "tool"}
SLOT_FA = {"weapon": "سلاح", "armor": "زره", "shield": "سپر", "bag": "کوله", "clothing": "لباس", "tool": "ابزار"}


def dur_factor(durability: int, max_dur: int) -> float:
    if not max_dur or max_dur <= 0:
        return 1.0
    return 0.5 + 0.5 * clamp(durability / max_dur, 0, 1)


async def _event(conn, item_id, def_id, qty, ft, fid, tt, tid, reason):
    await conn.execute(
        "INSERT INTO item_events(item_id, def_id, qty, from_type, from_id, to_type, to_id, reason) "
        "VALUES($1,$2,$3,$4,$5,$6,$7,$8)", item_id, def_id, qty, ft, fid, tt, tid, reason)


async def def_by_code(conn, code: str):
    row = await conn.fetchrow("SELECT * FROM item_defs WHERE code = $1", code)
    if row is None:
        raise GameError(f"آیتم «{code}» تعریف نشده است.")
    return row


async def def_by_id(conn, def_id: int):
    return await conn.fetchrow("SELECT * FROM item_defs WHERE id = $1", def_id)


# ───────── دسته‌ها (منابع/خوراکی) ─────────
async def add_stack(conn, ht: str, hid: int, def_id: int, qty: int, reason: str, frm=(None, None)):
    if qty <= 0:
        return
    await conn.execute(
        "INSERT INTO item_stacks(holder_type, holder_id, def_id, qty) VALUES($1,$2,$3,$4) "
        "ON CONFLICT (holder_type, holder_id, def_id) DO UPDATE SET qty = item_stacks.qty + EXCLUDED.qty",
        ht, hid, def_id, qty)
    await _event(conn, None, def_id, qty, frm[0], frm[1], ht, hid, reason)


async def take_stack(conn, ht: str, hid: int, def_id: int, qty: int, reason: str, to=(None, None)):
    if qty <= 0:
        raise GameError("تعداد نامعتبر است.")
    row = await conn.fetchrow(
        "UPDATE item_stacks SET qty = qty - $4 WHERE holder_type=$1 AND holder_id=$2 AND def_id=$3 AND qty >= $4 "
        "RETURNING qty", ht, hid, def_id, qty)
    if row is None:
        raise GameError("موجودی کافی نیست.")
    if row["qty"] == 0:
        await conn.execute("DELETE FROM item_stacks WHERE holder_type=$1 AND holder_id=$2 AND def_id=$3", ht, hid, def_id)
    await _event(conn, None, def_id, qty, ht, hid, to[0], to[1], reason)


async def move_stack(conn, ft, fid, tt, tid, def_id, qty, reason):
    await take_stack(conn, ft, fid, def_id, qty, reason, to=(tt, tid))
    await conn.execute(
        "INSERT INTO item_stacks(holder_type, holder_id, def_id, qty) VALUES($1,$2,$3,$4) "
        "ON CONFLICT (holder_type, holder_id, def_id) DO UPDATE SET qty = item_stacks.qty + EXCLUDED.qty",
        tt, tid, def_id, qty)


async def stack_qty(conn, ht, hid, def_id) -> int:
    return int(await conn.fetchval(
        "SELECT qty FROM item_stacks WHERE holder_type=$1 AND holder_id=$2 AND def_id=$3", ht, hid, def_id) or 0)


# ───────── تجهیزات یکتا ─────────
async def create_instance(conn, def_id: int, owner_user_id, ht: str, hid: int, reason: str,
                          bound: bool = False, owner_tribe_id=None):
    d = await def_by_id(conn, def_id)
    iid = await conn.fetchval(
        "INSERT INTO item_instances(def_id, owner_user_id, owner_tribe_id, holder_type, holder_id, durability, bound) "
        "VALUES($1,$2,$3,$4,$5,$6,$7) RETURNING id",
        def_id, owner_user_id, owner_tribe_id, ht, hid, d["max_durability"], bound)
    await _event(conn, iid, def_id, 1, None, None, ht, hid, reason)
    return iid


async def move_instance(conn, iid, ft, fid, tt, tid, reason, slot=None):
    """جابه‌جایی اتمیک: فقط اگر آیتم هنوز در مبدأ باشد."""
    row = await conn.fetchrow(
        "UPDATE item_instances SET holder_type=$4, holder_id=$5, slot=$6 "
        "WHERE id=$1 AND holder_type=$2 AND holder_id=$3 RETURNING def_id", iid, ft, fid, tt, tid, slot)
    if row is None:
        raise GameError("این وسیله دیگر در دسترس نیست.")
    await _event(conn, iid, row["def_id"], 1, ft, fid, tt, tid, reason)


async def destroy_instance(conn, iid, reason: str, comp_pct: int = 0):
    row = await conn.fetchrow(
        "DELETE FROM item_instances WHERE id=$1 RETURNING def_id, owner_user_id, holder_type, holder_id", iid)
    if row is None:
        return None
    await _event(conn, iid, row["def_id"], 1, row["holder_type"], row["holder_id"], None, None, reason)
    # غرامت تجهیز امانی قبیله که در دست عضو دیگری نابود شد
    if comp_pct > 0 and row["owner_user_id"] and row["holder_type"] in ("equipped", "backpack") \
            and row["holder_id"] != row["owner_user_id"]:
        tid = await conn.fetchval("SELECT tribe_id FROM tribe_members WHERE user_id=$1", row["holder_id"])
        if tid:
            val = await conn.fetchval("SELECT value FROM item_defs WHERE id=$1", row["def_id"])
            pay = int(val * comp_pct / 100)
            have = await conn.fetchval("SELECT coins FROM tribes WHERE id=$1 FOR UPDATE", tid)
            pay = min(pay, have or 0)
            if pay > 0:
                from app.services.economy import change_coins
                await conn.execute("UPDATE tribes SET coins = coins - $2 WHERE id=$1", tid, pay)
                await change_coins(conn, row["owner_user_id"], pay, "loan_compensation", ref=str(tid))
                await conn.execute("UPDATE tribe_loans SET status='lost' WHERE instance_id=$1 AND status='active'", iid)
    return row["def_id"]


# ───────── وزن و ظرفیت ─────────
async def holder_weight(conn, ht: str, hid: int) -> float:
    a = await conn.fetchval(
        "SELECT COALESCE(SUM(s.qty*d.weight),0) FROM item_stacks s JOIN item_defs d ON d.id=s.def_id "
        "WHERE s.holder_type=$1 AND s.holder_id=$2", ht, hid)
    b = await conn.fetchval(
        "SELECT COALESCE(SUM(d.weight),0) FROM item_instances i JOIN item_defs d ON d.id=i.def_id "
        "WHERE i.holder_type=$1 AND i.holder_id=$2", ht, hid)
    return float(a) + float(b)


async def carried_weight(conn, user_id: int) -> float:
    return await holder_weight(conn, "backpack", user_id) + await holder_weight(conn, "equipped", user_id)


async def loadout(conn, user_id: int) -> dict:
    rows = await conn.fetch(
        "SELECT i.id AS iid, i.slot, i.durability, d.name, d.kind, d.power, d.defense, d.max_durability, d.effects "
        "FROM item_instances i JOIN item_defs d ON d.id=i.def_id WHERE i.holder_type='equipped' AND i.holder_id=$1",
        user_id)
    out = {"power": 0.0, "defense": 0.0, "bag_bonus": 0.0, "items": [], "has_weapon": False}
    for r in rows:
        f = dur_factor(r["durability"], r["max_durability"])
        out["power"] += r["power"] * f
        out["defense"] += r["defense"] * f
        eff = json.loads(r["effects"]) if isinstance(r["effects"], str) else r["effects"]
        out["bag_bonus"] += float(eff.get("capacity", 0))
        out["has_weapon"] |= r["kind"] == "weapon"
        out["items"].append(dict(r) | {"effects": eff})
    return out


async def capacity(conn, settings, user_id: int, carry_skill: int, bag_bonus: float) -> float:
    return carry_capacity(carry_skill, bag_bonus, settings.get)


async def free_weight(conn, settings, user_id: int, ch) -> float:
    skills = json.loads(ch["skills"]) if isinstance(ch["skills"], str) else ch["skills"]
    lo = await loadout(conn, user_id)
    cap = carry_capacity(skills.get("carry", 1), lo["bag_bonus"], settings.get)
    return cap - await carried_weight(conn, user_id)


def fit_qty(weight_each: float, free: float, want: int) -> int:
    if weight_each <= 0:
        return want
    return int(max(0, min(want, free // weight_each)))


async def add_to_backpack(conn, settings, user_id: int, ch, def_row, qty: int, reason: str, frm=(None, None)) -> int:
    """تا حد ظرفیت اضافه می‌کند؛ تعداد واقعاً اضافه‌شده را برمی‌گرداند."""
    free = await free_weight(conn, settings, user_id, ch)
    n = fit_qty(float(def_row["weight"]), free, qty)
    if n > 0:
        if def_row["stackable"]:
            await add_stack(conn, "backpack", user_id, def_row["id"], n, reason, frm)
        else:
            for _ in range(n):
                await create_instance(conn, def_row["id"], user_id, "backpack", user_id, reason)
    return n


# ───────── فهرست‌ها ─────────
async def stacks_of(conn, ht, hid):
    return await conn.fetch(
        "SELECT s.def_id, s.qty, d.name, d.kind, d.weight, d.value FROM item_stacks s JOIN item_defs d ON d.id=s.def_id "
        "WHERE s.holder_type=$1 AND s.holder_id=$2 ORDER BY d.kind, d.name", ht, hid)


async def instances_of(conn, ht, hid):
    return await conn.fetch(
        "SELECT i.id, i.def_id, i.durability, i.bound, i.slot, i.owner_user_id, d.name, d.kind, d.power, d.defense, "
        "d.max_durability, d.weight, d.value, d.tradable FROM item_instances i JOIN item_defs d ON d.id=i.def_id "
        "WHERE i.holder_type=$1 AND i.holder_id=$2 ORDER BY d.kind, d.name", ht, hid)


# ───────── تجهیز / مصرف ─────────
async def equip(conn, user_id: int, iid):
    inst = await conn.fetchrow(
        "SELECT i.*, d.kind FROM item_instances i JOIN item_defs d ON d.id=i.def_id WHERE i.id=$1 FOR UPDATE", iid)
    if inst is None or inst["holder_id"] != user_id or inst["holder_type"] not in ("backpack", "storage"):
        raise GameError("این وسیله در اختیار تو نیست.")
    slot = SLOT_BY_KIND.get(inst["kind"])
    if not slot:
        raise GameError("این وسیله قابل تجهیز نیست.")
    old = await conn.fetchval(
        "SELECT id FROM item_instances WHERE holder_type='equipped' AND holder_id=$1 AND slot=$2 FOR UPDATE", user_id, slot)
    if old:
        await move_instance(conn, old, "equipped", user_id, "backpack", user_id, "unequip", None)
    await move_instance(conn, iid, inst["holder_type"], user_id, "equipped", user_id, "equip", slot)


async def unequip(conn, user_id: int, iid):
    await move_instance(conn, iid, "equipped", user_id, "backpack", user_id, "unequip", None)


async def consume(conn, user_id: int, def_id: int):
    d = await def_by_id(conn, def_id)
    if d["kind"] not in ("food", "drink", "medicine"):
        raise GameError("این چیز قابل مصرف نیست.")
    eff = json.loads(d["effects"]) if isinstance(d["effects"], str) else d["effects"]
    await take_stack(conn, "backpack", user_id, def_id, 1, "consume")
    ch = await conn.fetchrow("SELECT * FROM characters WHERE user_id=$1 FOR UPDATE", user_id)
    vals = {k: int(clamp(ch[k] + int(eff.get(k, 0)), 0, 100)) for k in ("health", "hunger", "thirst", "energy")}
    fat = int(clamp(ch["fatigue"] - int(eff.get("fatigue", 0)), 0, 100))
    await conn.execute(
        "UPDATE characters SET health=$2, hunger=$3, thirst=$4, energy=$5, fatigue=$6 WHERE user_id=$1",
        user_id, vals["health"], vals["hunger"], vals["thirst"], vals["energy"], fat)
    return d["name"]


async def damage_equipment(conn, settings, user_id: int, loss: int) -> list:
    """فرسودگی تجهیزات پوشیده؛ لیست نام وسایل شکسته را برمی‌گرداند."""
    broken = []
    rows = await conn.fetch(
        "SELECT i.id, i.durability, i.slot, d.name, d.max_durability FROM item_instances i JOIN item_defs d ON d.id=i.def_id "
        "WHERE i.holder_type='equipped' AND i.holder_id=$1 AND d.max_durability > 0 AND i.slot IN ('weapon','armor','shield') "
        "FOR UPDATE OF i", user_id)
    for r in rows:
        nd = max(0, r["durability"] - loss)
        if nd <= 0:
            await destroy_instance(conn, r["id"], "broken", int(settings.get("tribe.loan_compensation_pct")))
            broken.append(r["name"])
        else:
            await conn.execute("UPDATE item_instances SET durability=$2 WHERE id=$1", r["id"], nd)
    return broken


async def repair(conn, user_id: int, iid, cost_coins: int):
    from app.services.economy import change_coins
    row = await conn.fetchrow(
        "SELECT i.*, d.max_durability, d.value FROM item_instances i JOIN item_defs d ON d.id=i.def_id WHERE i.id=$1 FOR UPDATE", iid)
    if row is None or row["holder_id"] != user_id:
        raise GameError("این وسیله مال تو نیست.")
    if row["max_durability"] <= 0 or row["durability"] >= row["max_durability"]:
        raise GameError("نیازی به تعمیر ندارد.")
    await change_coins(conn, user_id, -cost_coins, "repair", ref=str(iid))
    await conn.execute("UPDATE item_instances SET durability=$2 WHERE id=$1", iid, row["max_durability"])


def repair_cost(value: int, durability: int, max_dur: int) -> int:
    missing = 1 - (durability / max_dur if max_dur else 1)
    return max(1, int(value * 0.5 * missing))


# ───────── مرگ / غنیمت ─────────
async def drop_carried(conn, user_id: int, drop_id: int, reason: str) -> int:
    """همه چیزهای همراه شخصیت (کوله‌پشتی و پوشیده‌ها، به‌جز غیرقابل‌غارت‌ها) به محل مرگ می‌روند."""
    n = 0
    for st in await conn.fetch(
            "SELECT s.def_id, s.qty FROM item_stacks s JOIN item_defs d ON d.id=s.def_id "
            "WHERE s.holder_type='backpack' AND s.holder_id=$1 AND d.lootable", user_id):
        await move_stack(conn, "backpack", user_id, "ground", drop_id, st["def_id"], st["qty"], reason)
        n += 1
    for it in await conn.fetch(
            "SELECT i.id, i.holder_type FROM item_instances i JOIN item_defs d ON d.id=i.def_id "
            "WHERE i.holder_type IN ('backpack','equipped') AND i.holder_id=$1 AND d.lootable AND NOT i.bound", user_id):
        await move_instance(conn, it["id"], it["holder_type"], user_id, "ground", drop_id, reason)
        n += 1
    return n


async def pickup_from_ground(conn, settings, user_id: int, ch, drop_id: int, limit_items: int = 99) -> list:
    """غنیمت را بر اساس ارزش و تا سقف ظرفیت برمی‌دارد. لیست (نام، تعداد) را برمی‌گرداند."""
    taken = []
    free = await free_weight(conn, settings, user_id, ch)
    entries = []
    for st in await conn.fetch(
            "SELECT s.def_id, s.qty, d.name, d.weight, d.value FROM item_stacks s JOIN item_defs d ON d.id=s.def_id "
            "WHERE s.holder_type='ground' AND s.holder_id=$1", drop_id):
        entries.append(("s", st["value"], st))
    for it in await conn.fetch(
            "SELECT i.id, i.def_id, d.name, d.weight, d.value FROM item_instances i JOIN item_defs d ON d.id=i.def_id "
            "WHERE i.holder_type='ground' AND i.holder_id=$1", drop_id):
        entries.append(("i", it["value"], it))
    entries.sort(key=lambda e: -e[1])
    for kind, _v, row in entries[:limit_items]:
        w = float(row["weight"])
        if kind == "s":
            n = fit_qty(w, free, row["qty"])
            if n > 0:
                await move_stack(conn, "ground", drop_id, "backpack", user_id, row["def_id"], n, "loot")
                free -= n * w
                taken.append((row["name"], n))
        else:
            if fit_qty(w, free, 1) >= 1:
                await move_instance(conn, row["id"], "ground", drop_id, "backpack", user_id, "loot")
                await conn.execute("UPDATE item_instances SET owner_user_id=$2 WHERE id=$1", row["id"], user_id)
                free -= w
                taken.append((row["name"], 1))
    return taken
