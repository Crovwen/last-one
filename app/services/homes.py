"""خانه و ساختمان‌ها (خانه شخصی و قبیله)، آمار دفاعی و انبار شخصی."""
from __future__ import annotations

import json
import random
from datetime import timedelta

from app.services import inventory as inv
from app.services.chars import jload, now
from app.services.economy import change_coins
from app.services.errors import GameError
from app.services.notify import notify
from app.services.sched import schedule

HOME = "home"


def cost_at(cost: dict, mult: float, level: int) -> dict:
    """هزینه سطح level (۱ = ساخت اولیه). هر سطح بعدی ضریب cost_mult."""
    f = float(mult) ** (level - 1)
    return {k: max(1, int(round(v * f))) for k, v in cost.items()}


def cost_text(cost: dict, names: dict) -> str:
    parts = []
    for k, v in cost.items():
        parts.append(f"{v} 🪙" if k == "coins" else f"{v} {names.get(k, k)}")
    return "، ".join(parts) or "رایگان"


async def user_buildings(conn, scope: str, owner_id: int):
    return await conn.fetch(
        "SELECT b.*, d.code, d.kind, d.name, d.max_level, d.cost, d.cost_mult, d.build_minutes, d.effects, d.requires "
        "FROM buildings b JOIN building_defs d ON d.id=b.def_id WHERE b.scope=$1 AND b.owner_id=$2 ORDER BY d.kind, d.name",
        scope, owner_id)


async def settle_buildings(conn, scope: str, owner_id: int) -> list:
    done = []
    rows = await conn.fetch(
        "SELECT b.id, b.target_level, d.name FROM buildings b JOIN building_defs d ON d.id=b.def_id "
        "WHERE b.scope=$1 AND b.owner_id=$2 AND b.status='building' AND b.ready_at <= now() FOR UPDATE OF b",
        scope, owner_id)
    for r in rows:
        await conn.execute("UPDATE buildings SET status='built', level=COALESCE(target_level, level), target_level=NULL, "
                           "ready_at=NULL WHERE id=$1", r["id"])
        msg = f"🏗 {r['name']} (سطح {r['target_level']}) آماده شد"
        done.append(msg)
        if scope == HOME:
            await notify(conn, owner_id, "build", msg)
        else:
            for m in await conn.fetch("SELECT user_id FROM tribe_members WHERE tribe_id=$1", owner_id):
                await notify(conn, m["user_id"], "build", "🏕 " + msg)
    return done


async def home_stats(conn, settings, user_id: int) -> dict:
    s = {"defense": 0.0, "storage": float(settings.get("home.base_storage")), "trap": 0.0, "alarm": 0.0,
         "conceal": 0.0, "guard_slots": int(settings.get("home.base_guard_slots")),
         "kennel_slots": int(settings.get("home.base_kennel_slots")), "house": None, "house_level": 0}
    for b in await user_buildings(conn, HOME, user_id):
        eff = jload(b["effects"])
        for key in ("defense", "storage", "trap", "alarm", "conceal"):
            s[key] += float(eff.get(key, 0)) * b["level"]
        s["guard_slots"] += int(eff.get("guard_slots", 0)) * b["level"]
        s["kennel_slots"] += int(eff.get("kennel_slots", 0)) * b["level"]
        if b["kind"] == "house":
            s["house"], s["house_level"] = b["name"], b["level"]
    return s


async def ensure_starter_home(conn, user_id: int):
    """کپر ابتدایی رایگان در اولین نیاز."""
    if await conn.fetchval("SELECT 1 FROM buildings WHERE scope='home' AND owner_id=$1 AND def_id IN "
                           "(SELECT id FROM building_defs WHERE kind='house')", user_id):
        return
    d = await conn.fetchrow("SELECT id FROM building_defs WHERE code='lean_to'")
    if d:
        await conn.execute("INSERT INTO buildings(scope, owner_id, def_id, level) VALUES('home',$1,$2,1) "
                           "ON CONFLICT DO NOTHING", user_id, d["id"])


async def pay_cost(conn, user_id: int, cost: dict, reason: str, tribe_id: int = None):
    """پرداخت هزینه ساخت. بازیکن: سکه از حساب، منابع از انبار سپس کوله. قبیله: از خزانه و انبار قبیله."""
    for key, need in cost.items():
        if key == "coins":
            if tribe_id:
                n = await conn.fetchval("UPDATE tribes SET coins = coins - $2 WHERE id=$1 AND coins >= $2 RETURNING coins",
                                        tribe_id, need)
                if n is None:
                    raise GameError("خزانه قبیله کافی نیست.")
                await conn.execute("INSERT INTO tribe_ledger(tribe_id, user_id, kind, coins, note) VALUES($1,$2,'build',$3,$4)",
                                   tribe_id, user_id, -need, reason)
            else:
                try:
                    await change_coins(conn, user_id, -need, reason)
                except Exception as exc:  # noqa: BLE001
                    raise GameError("سکه کافی نداری.") from exc
            continue
        d = await inv.def_by_code(conn, key)
        if tribe_id:
            await inv.take_stack(conn, "tribe", tribe_id, d["id"], need, reason)
        else:
            have_s = await inv.stack_qty(conn, "storage", user_id, d["id"])
            take_s = min(have_s, need)
            have_b = await inv.stack_qty(conn, "backpack", user_id, d["id"])
            if have_s + have_b < need:
                raise GameError(f"{d['name']} کافی نداری (نیاز: {need}).")
            if take_s:
                await inv.take_stack(conn, "storage", user_id, d["id"], take_s, reason)
            if need - take_s:
                await inv.take_stack(conn, "backpack", user_id, d["id"], need - take_s, reason)


async def start_build(conn, settings, user_id: int, code: str, build_speed: float = 1.0, tribe_id: int = None,
                      tribe_level: int = 1):
    """ساخت یا ارتقای یک ساختمان؛ (پیام, ready_at) را برمی‌گرداند."""
    scope = "tribe" if tribe_id else HOME
    owner = tribe_id or user_id
    d = await conn.fetchrow("SELECT * FROM building_defs WHERE code=$1 AND scope=$2 AND is_active", code, scope)
    if d is None:
        raise GameError("ساختمان پیدا نشد.")
    req = jload(d["requires"])
    if req.get("tribe_level", 1) > tribe_level:
        raise GameError(f"نیاز به قبیله سطح {req['tribe_level']}.")
    existing = await conn.fetchrow("SELECT * FROM buildings WHERE scope=$1 AND owner_id=$2 AND def_id=$3 FOR UPDATE",
                                   scope, owner, d["id"])
    if existing and existing["status"] == "building":
        raise GameError("این ساختمان در حال ساخت است.")
    busy = await conn.fetchval("SELECT 1 FROM buildings WHERE scope=$1 AND owner_id=$2 AND status='building'", scope, owner)
    if busy:
        raise GameError("یک ساخت‌وساز دیگر در جریان است.")
    level = (existing["level"] + 1) if existing else 1
    if level > d["max_level"]:
        raise GameError("به حداکثر سطح رسیده است.")
    if d["kind"] == "house" and scope == HOME:
        cur = await conn.fetchrow("SELECT b.id FROM buildings b JOIN building_defs x ON x.id=b.def_id "
                                  "WHERE b.scope='home' AND b.owner_id=$1 AND x.kind='house' AND b.def_id<>$2", user_id, d["id"])
        if cur and not existing:
            await conn.execute("DELETE FROM buildings WHERE id=$1", cur["id"])   # جایگزینی خانه قبلی
    cost = cost_at(jload(d["cost"]), d["cost_mult"], level)
    await pay_cost(conn, user_id, cost, f"build:{code}:{level}", tribe_id)
    minutes = max(1, int(round(d["build_minutes"] * (1.0 + 0.5 * (level - 1)) * build_speed)))
    ready = now() + timedelta(minutes=minutes)
    if existing:
        await conn.execute("UPDATE buildings SET status='building', target_level=$2, ready_at=$3 WHERE id=$1",
                           existing["id"], level, ready)
    else:
        await conn.execute("INSERT INTO buildings(scope, owner_id, def_id, level, status, target_level, ready_at) "
                           "VALUES($1,$2,$3,0,'building',$4,$5)", scope, owner, d["id"], level, ready)
    await schedule("building_done", {"scope": scope, "owner_id": owner}, ready)
    return f"🏗 ساخت «{d['name']}» (سطح {level}) شروع شد؛ {minutes} دقیقه", ready


# ───────── انبار شخصی ─────────
async def storage_used(conn, user_id: int) -> float:
    return await inv.holder_weight(conn, "storage", user_id)


async def deposit(conn, settings, user_id: int, def_id: int, qty: int = 1, iid=None):
    st = await home_stats(conn, settings, user_id)
    d = await inv.def_by_id(conn, def_id)
    w = float(d["weight"]) * (1 if iid else qty)
    if await storage_used(conn, user_id) + w > st["storage"]:
        raise GameError("انبار جا ندارد؛ با ساختن/ارتقای انبار ظرفیت را زیاد کن.")
    if iid:
        await inv.move_instance(conn, iid, "backpack", user_id, "storage", user_id, "deposit")
    else:
        await inv.move_stack(conn, "backpack", user_id, "storage", user_id, def_id, qty, "deposit")


async def withdraw(conn, settings, user_id: int, ch, def_id: int, qty: int = 1, iid=None):
    d = await inv.def_by_id(conn, def_id)
    free = await inv.free_weight(conn, settings, user_id, ch)
    if float(d["weight"]) * (1 if iid else qty) > free:
        raise GameError("کوله‌پشتی جا ندارد.")
    if iid:
        await inv.move_instance(conn, iid, "storage", user_id, "backpack", user_id, "withdraw")
    else:
        await inv.move_stack(conn, "storage", user_id, "backpack", user_id, def_id, qty, "withdraw")


# ───────── اطلاعات دفاعی برای شناسایی و نبرد ─────────
async def defense_participants(conn, settings, user_id: int) -> dict:
    """نگهبانان و حیوانات مستقر در خانه + ساختارها."""
    from app.services import animals as an
    from app.services import hirelings as hr

    parts, info = [], {"guards": [], "dogs": [], "traps": 0}
    st = await home_stats(conn, settings, user_id)
    for h in await hr.guards_at_home(conn, settings, user_id):
        parts.append(h["part"])
        info["guards"].append({"name": h["part"]["name"], "gear": h["gear"]})
    for a in await an.guards_at_home(conn, settings, user_id):
        parts.append(a["part"])
        info["dogs"].append({"breed": a["breed"]})
    info["traps"] = 1 if st["trap"] > 0 else 0
    info["defense_power"] = sum(p["power"] for p in parts) * (1 + st["defense"] / 100.0) + st["defense"]
    return {"parts": parts, "info": info, "stats": st}
