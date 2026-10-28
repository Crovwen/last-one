"""بازار سیستمی، بازار بازیکنان، انتقال سکه/آیتم، معامله امن دوطرفه، ساخت تجهیزات."""
from __future__ import annotations

import json
from datetime import timedelta

from app.services import homes
from app.services import inventory as inv
from app.services.chars import get_char, jload, now, skills_of
from app.services.economy import InsufficientFunds, change_coins
from app.services.errors import GameError
from app.services.notify import notify
from app.services.sched import schedule


# ───────── بازار سیستمی ─────────
async def shop_items(conn, kinds=None):
    return await conn.fetch(
        "SELECT * FROM item_defs WHERE in_shop AND is_active AND shop_price > 0 ORDER BY kind, shop_price")


async def buy_npc(conn, settings, user_id: int, ch, code: str, qty: int):
    d = await conn.fetchrow("SELECT * FROM item_defs WHERE code=$1 AND in_shop AND is_active", code)
    if d is None or qty <= 0:
        raise GameError("این کالا موجود نیست.")
    total = d["shop_price"] * qty
    try:
        await change_coins(conn, user_id, -total, "shop_buy", ref=code)
    except InsufficientFunds as exc:
        raise GameError(f"سکه کافی نداری (نیاز: {total}).") from exc
    added = await inv.add_to_backpack(conn, settings, user_id, ch, d, qty, "shop_buy")
    if added < qty:       # آنچه جا نشد به انبار می‌رود
        left = qty - added
        if d["stackable"]:
            await inv.add_stack(conn, "storage", user_id, d["id"], left, "shop_buy_overflow")
        else:
            for _ in range(left):
                await inv.create_instance(conn, d["id"], user_id, "storage", user_id, "shop_buy_overflow")
    return d["name"], added, qty - added


async def sell_npc(conn, settings, user_id: int, def_id: int, qty: int = 1, iid=None) -> int:
    d = await inv.def_by_id(conn, def_id)
    if not d["tradable"]:
        raise GameError("این وسیله قابل فروش نیست.")
    price = int(d["value"] * int(settings.get("market.sell_to_npc_pct")) / 100) * (1 if iid else qty)
    if iid:
        row = await conn.fetchrow("SELECT * FROM item_instances WHERE id=$1 FOR UPDATE", iid)
        if row is None or row["holder_id"] != user_id or row["holder_type"] not in ("backpack", "storage") or row["bound"]:
            raise GameError("این وسیله قابل فروش نیست.")
        await inv.destroy_instance(conn, iid, "sold_npc")
    else:
        src = "backpack" if await inv.stack_qty(conn, "backpack", user_id, def_id) >= qty else "storage"
        await inv.take_stack(conn, src, user_id, def_id, qty, "sold_npc")
    await change_coins(conn, user_id, price, "shop_sell", ref=d["code"])
    return price


# ───────── بازار بازیکنان ─────────
async def browse(conn, offset: int = 0, limit: int = 8):
    return await conn.fetch(
        "SELECT l.*, d.name, d.kind, d.power, d.defense, c.name AS seller FROM market_listings l "
        "JOIN item_defs d ON d.id=l.def_id JOIN characters c ON c.user_id=l.seller_id "
        "WHERE l.status='active' ORDER BY l.id DESC OFFSET $1 LIMIT $2", offset, limit)


async def my_listings(conn, user_id: int):
    return await conn.fetch(
        "SELECT l.*, d.name FROM market_listings l JOIN item_defs d ON d.id=l.def_id WHERE l.seller_id=$1 AND l.status='active' "
        "ORDER BY l.id DESC", user_id)


def price_range(value: int, settings) -> tuple:
    return (max(1, int(value * int(settings.get("market.price_min_pct")) / 100)),
            max(1, int(value * int(settings.get("market.price_max_pct")) / 100)))


async def list_item(conn, settings, user_id: int, def_id: int, qty: int, unit_price: int, iid=None) -> int:
    d = await inv.def_by_id(conn, def_id)
    if not d["tradable"]:
        raise GameError("این وسیله قابل فروش نیست.")
    lo, hi = price_range(d["value"], settings)
    if not lo <= unit_price <= hi:
        raise GameError(f"قیمت مجاز برای هر عدد: {lo} تا {hi} سکه.")
    n = await conn.fetchval("SELECT count(*) FROM market_listings WHERE seller_id=$1 AND status='active'", user_id)
    if n >= int(settings.get("market.max_listings")):
        raise GameError("به سقف آگهی‌های فعال رسیده‌ای.")
    qty = 1 if iid else qty
    lid = await conn.fetchval(
        "INSERT INTO market_listings(seller_id, def_id, instance_id, qty, unit_price) VALUES($1,$2,$3,$4,$5) RETURNING id",
        user_id, def_id, iid, qty, unit_price)
    if iid:
        row = await conn.fetchrow("SELECT * FROM item_instances WHERE id=$1 FOR UPDATE", iid)
        if row is None or row["owner_user_id"] != user_id or row["bound"] or row["holder_id"] != user_id \
                or row["holder_type"] not in ("backpack", "storage"):
            raise GameError("این وسیله قابل فروش نیست.")
        await inv.move_instance(conn, iid, row["holder_type"], user_id, "market", lid, "list")
    else:
        src = "backpack" if await inv.stack_qty(conn, "backpack", user_id, def_id) >= qty else "storage"
        await inv.move_stack(conn, src, user_id, "market", lid, def_id, qty, "list")
    return lid


async def cancel_listing(conn, user_id: int, lid: int):
    l = await conn.fetchrow("SELECT * FROM market_listings WHERE id=$1 AND seller_id=$2 AND status='active' FOR UPDATE", lid, user_id)
    if l is None:
        raise GameError("آگهی پیدا نشد.")
    if l["instance_id"]:
        await inv.move_instance(conn, l["instance_id"], "market", lid, "storage", user_id, "unlist")
    else:
        await inv.move_stack(conn, "market", lid, "storage", user_id, l["def_id"], l["qty"], "unlist")
    await conn.execute("UPDATE market_listings SET status='cancelled' WHERE id=$1", lid)


async def buy_listing(conn, settings, buyer_id: int, ch, lid: int, qty: int):
    l = await conn.fetchrow("SELECT * FROM market_listings WHERE id=$1 AND status='active' FOR UPDATE", lid)
    if l is None:
        raise GameError("این آگهی دیگر فعال نیست.")
    if l["seller_id"] == buyer_id:
        raise GameError("آگهی خودت را نمی‌توانی بخری.")
    qty = 1 if l["instance_id"] else max(1, min(qty, l["qty"]))
    d = await inv.def_by_id(conn, l["def_id"])
    free = await inv.free_weight(conn, settings, buyer_id, ch)
    if float(d["weight"]) * qty > free:
        raise GameError("کوله‌پشتی جا ندارد.")
    total = l["unit_price"] * qty
    try:
        await change_coins(conn, buyer_id, -total, "market_buy", ref=str(lid))
    except InsufficientFunds as exc:
        raise GameError("سکه کافی نداری.") from exc
    fee = total * int(settings.get("market.fee_pct")) // 100
    await change_coins(conn, l["seller_id"], total - fee, "market_sell", ref=str(lid))
    if l["instance_id"]:
        await inv.move_instance(conn, l["instance_id"], "market", lid, "backpack", buyer_id, "market_buy")
        await conn.execute("UPDATE item_instances SET owner_user_id=$2 WHERE id=$1", l["instance_id"], buyer_id)
    else:
        await inv.move_stack(conn, "market", lid, "backpack", buyer_id, l["def_id"], qty, "market_buy")
    left = l["qty"] - qty
    await conn.execute("UPDATE market_listings SET qty=$2, status=$3, buyer_id=$4 WHERE id=$1", lid, max(left, 1),
                       "active" if left > 0 else "sold", buyer_id if left <= 0 else None)
    await notify(conn, l["seller_id"], "sale", f"💰 {qty} عدد {d['name']} فروخته شد: {total - fee} 🪙 (کارمزد {fee})")
    return d["name"], qty, total


# ───────── انتقال مستقیم ─────────
async def _lock_users(conn, ids):
    await conn.fetch("SELECT id FROM users WHERE id = ANY($1::bigint[]) ORDER BY id FOR UPDATE", list(ids))


async def transfer_coins(conn, settings, sender: int, receiver: int, amount: int):
    if sender == receiver or amount < 1:
        raise GameError("مقدار یا گیرنده نامعتبر است.")
    await _lock_users(conn, (sender, receiver))
    u = await conn.fetchrow("SELECT coins, locked_coins FROM users WHERE id=$1", sender)
    if amount > u["coins"] - u["locked_coins"]:
        raise GameError(f"فقط {u['coins'] - u['locked_coins']} سکه قابل انتقال است (سکه‌های اولیه/ویژه قفل‌اند).")
    await change_coins(conn, sender, -amount, "transfer_out", ref=str(receiver))
    await change_coins(conn, receiver, amount, "transfer_in", ref=str(sender))
    names = await conn.fetchrow("SELECT (SELECT name FROM characters WHERE user_id=$1) a", sender)
    await notify(conn, receiver, "transfer", f"💸 {names['a']} مبلغ {amount} 🪙 برایت فرستاد.")


async def give_item(conn, sender: int, receiver: int, def_id: int, qty: int = 1, iid=None):
    if sender == receiver:
        raise GameError("گیرنده نامعتبر است.")
    d = await inv.def_by_id(conn, def_id)
    if not d["tradable"]:
        raise GameError("این وسیله قابل انتقال نیست.")
    if iid:
        row = await conn.fetchrow("SELECT * FROM item_instances WHERE id=$1 FOR UPDATE", iid)
        if row is None or row["owner_user_id"] != sender or row["bound"] or row["holder_id"] != sender \
                or row["holder_type"] not in ("backpack", "storage"):
            raise GameError("این وسیله قابل انتقال نیست.")
        await inv.move_instance(conn, iid, row["holder_type"], sender, "storage", receiver, "gift")
        await conn.execute("UPDATE item_instances SET owner_user_id=$2 WHERE id=$1", iid, receiver)
    else:
        src = "backpack" if await inv.stack_qty(conn, "backpack", sender, def_id) >= qty else "storage"
        await inv.move_stack(conn, src, sender, "storage", receiver, def_id, qty, "gift")
    await notify(conn, receiver, "gift", f"🎁 {d['name']} برایت فرستاده شد (در انبار خانه).")


# ───────── معامله امن دوطرفه ─────────
async def trade_open(conn, settings, a: int, b: int) -> int:
    if a == b:
        raise GameError("گیرنده نامعتبر است.")
    if await conn.fetchval("SELECT 1 FROM trades WHERE status='open' AND (a_user=$1 OR b_user=$1 OR a_user=$2 OR b_user=$2)", a, b):
        raise GameError("یکی از دو طرف معامله بازی دارد.")
    exp = now() + timedelta(minutes=int(settings.get("trade.expire_minutes")))
    tid = await conn.fetchval("INSERT INTO trades(a_user, b_user, expires_at) VALUES($1,$2,$3) RETURNING id", a, b, exp)
    await schedule("trade_expire", {"trade_id": tid}, exp)
    nm = await conn.fetchval("SELECT name FROM characters WHERE user_id=$1", a)
    await notify(conn, b, "trade", f"🤝 {nm} معامله امن پیشنهاد داد. از منوی بازار ← معامله باز کن.")
    return tid


async def trade_get(conn, tid: int, user_id: int, lock=False):
    t = await conn.fetchrow("SELECT * FROM trades WHERE id=$1" + (" FOR UPDATE" if lock else ""), tid)
    if t is None or t["status"] != "open" or user_id not in (t["a_user"], t["b_user"]):
        raise GameError("این معامله فعال نیست.")
    if t["expires_at"] <= now():
        raise GameError("مهلت معامله تمام شده است.")
    return t


async def trade_of(conn, user_id: int):
    return await conn.fetchrow("SELECT * FROM trades WHERE status='open' AND (a_user=$1 OR b_user=$1) AND expires_at > now()", user_id)


async def trade_items(conn, tid: int):
    return await conn.fetch(
        "SELECT ti.*, d.name FROM trade_items ti JOIN item_defs d ON d.id=ti.def_id WHERE ti.trade_id=$1 ORDER BY ti.id", tid)


async def _reset_ok(conn, tid: int):
    await conn.execute("UPDATE trades SET a_ok=false, b_ok=false WHERE id=$1", tid)


async def trade_set_coins(conn, user_id: int, tid: int, amount: int):
    t = await trade_get(conn, tid, user_id, True)
    col = "a_coins" if user_id == t["a_user"] else "b_coins"
    await conn.execute(f"UPDATE trades SET {col}=$2 WHERE id=$1", tid, max(0, amount))
    await _reset_ok(conn, tid)


async def trade_add_item(conn, user_id: int, tid: int, def_id: int, qty: int = 1, iid=None):
    t = await trade_get(conn, tid, user_id, True)
    d = await inv.def_by_id(conn, def_id)
    if not d["tradable"]:
        raise GameError("این وسیله قابل معامله نیست.")
    if iid:
        row = await conn.fetchrow("SELECT * FROM item_instances WHERE id=$1", iid)
        if row is None or row["owner_user_id"] != user_id or row["bound"] or row["holder_id"] != user_id \
                or row["holder_type"] not in ("backpack", "storage"):
            raise GameError("این وسیله قابل معامله نیست.")
        if await conn.fetchval("SELECT 1 FROM trade_items WHERE trade_id=$1 AND instance_id=$2", tid, iid):
            raise GameError("قبلاً اضافه شده است.")
        qty = 1
    await conn.execute("INSERT INTO trade_items(trade_id, side, def_id, qty, instance_id) VALUES($1,$2,$3,$4,$5)",
                       tid, "a" if user_id == t["a_user"] else "b", def_id, qty, iid)
    await _reset_ok(conn, tid)


async def trade_clear(conn, user_id: int, tid: int):
    t = await trade_get(conn, tid, user_id, True)
    await conn.execute("DELETE FROM trade_items WHERE trade_id=$1 AND side=$2", tid, "a" if user_id == t["a_user"] else "b")
    await _reset_ok(conn, tid)


async def trade_cancel(conn, user_id: int, tid: int):
    t = await trade_get(conn, tid, user_id, True)
    await conn.execute("UPDATE trades SET status='cancelled' WHERE id=$1", tid)
    other = t["b_user"] if user_id == t["a_user"] else t["a_user"]
    await notify(conn, other, "trade", "❌ معامله لغو شد.")


async def _deliver_side(conn, items, giver: int, taker: int):
    for it in items:
        if it["instance_id"]:
            row = await conn.fetchrow("SELECT * FROM item_instances WHERE id=$1 FOR UPDATE", it["instance_id"])
            if row is None or row["holder_id"] != giver or row["holder_type"] not in ("backpack", "storage") or row["bound"]:
                raise GameError("یکی از وسایل معامله دیگر در دسترس نیست؛ معامله انجام نشد.")
            await inv.move_instance(conn, it["instance_id"], row["holder_type"], giver, "storage", taker, "trade")
            await conn.execute("UPDATE item_instances SET owner_user_id=$2 WHERE id=$1", it["instance_id"], taker)
        else:
            src = "backpack" if await inv.stack_qty(conn, "backpack", giver, it["def_id"]) >= it["qty"] else "storage"
            await inv.move_stack(conn, src, giver, "storage", taker, it["def_id"], it["qty"], "trade")


async def trade_confirm(conn, settings, user_id: int, tid: int) -> bool:
    """True یعنی معامله نهایی شد (هر دو طرف تأیید کرده‌اند)."""
    t = await trade_get(conn, tid, user_id, True)
    col = "a_ok" if user_id == t["a_user"] else "b_ok"
    await conn.execute(f"UPDATE trades SET {col}=true WHERE id=$1", tid)
    t = await conn.fetchrow("SELECT * FROM trades WHERE id=$1", tid)
    if not (t["a_ok"] and t["b_ok"]):
        other = t["b_user"] if user_id == t["a_user"] else t["a_user"]
        await notify(conn, other, "trade", "✅ طرف مقابل معامله را تأیید کرد؛ نوبت توست.")
        return False
    await _lock_users(conn, (t["a_user"], t["b_user"]))
    items = await conn.fetch("SELECT * FROM trade_items WHERE trade_id=$1", tid)
    a_items = [i for i in items if i["side"] == "a"]
    b_items = [i for i in items if i["side"] == "b"]
    for who, amt in ((t["a_user"], t["a_coins"]), (t["b_user"], t["b_coins"])):
        u = await conn.fetchrow("SELECT coins, locked_coins FROM users WHERE id=$1", who)
        if amt > u["coins"] - u["locked_coins"]:
            raise GameError("یکی از طرفین سکه قابل‌انتقال کافی ندارد.")
    await _deliver_side(conn, a_items, t["a_user"], t["b_user"])
    await _deliver_side(conn, b_items, t["b_user"], t["a_user"])
    if t["a_coins"]:
        await change_coins(conn, t["a_user"], -t["a_coins"], "transfer_out", ref=f"trade{tid}")
        await change_coins(conn, t["b_user"], t["a_coins"], "transfer_in", ref=f"trade{tid}")
    if t["b_coins"]:
        await change_coins(conn, t["b_user"], -t["b_coins"], "transfer_out", ref=f"trade{tid}")
        await change_coins(conn, t["a_user"], t["b_coins"], "transfer_in", ref=f"trade{tid}")
    await conn.execute("UPDATE trades SET status='done' WHERE id=$1", tid)
    for u in (t["a_user"], t["b_user"]):
        await notify(conn, u, "trade", "🤝 معامله با موفقیت انجام شد؛ وسایل در انبار خانه‌ات است.")
    return True


async def trade_expire(conn, tid: int):
    await conn.execute("UPDATE trades SET status='cancelled' WHERE id=$1 AND status='open' AND expires_at <= now()", tid)


# ───────── ساخت تجهیزات ─────────
async def craftable(conn):
    return await conn.fetch("SELECT * FROM item_defs WHERE is_active AND recipe <> '{}'::jsonb ORDER BY kind, tier, name")


async def craft(conn, settings, user_id: int, ch, code: str, qty: int = 1):
    d = await conn.fetchrow("SELECT * FROM item_defs WHERE code=$1 AND is_active", code)
    rec = jload(d["recipe"]) if d else {}
    if not rec:
        raise GameError("این وسیله دستور ساخت ندارد.")
    from app.services.chars import grant_skill_xp, require_home
    require_home(ch, "ساخت وسیله")
    sk = rec.get("skill")
    if sk and skills_of(ch).get(sk, 1) < int(rec.get("min_skill", 1)):
        raise GameError(f"حداقل مهارت {rec['skill']} لازم است: {rec.get('min_skill', 1)}")
    qty = qty if d["stackable"] else 1
    free = await inv.free_weight(conn, settings, user_id, ch)
    if float(d["weight"]) * qty > free:
        raise GameError("کوله‌پشتی برای نتیجه ساخت جا ندارد.")
    cost = {k: v * qty for k, v in rec.get("inputs", {}).items()}
    if rec.get("coins"):
        cost["coins"] = rec["coins"] * qty
    await homes.pay_cost(conn, user_id, cost, f"craft:{code}")
    if d["stackable"]:
        await inv.add_stack(conn, "backpack", user_id, d["id"], qty, "craft")
    else:
        await inv.create_instance(conn, d["id"], user_id, "backpack", user_id, "craft")
    msgs = await grant_skill_xp(conn, settings, user_id, "building", 3 + d["tier"])
    return d["name"], qty, msgs
