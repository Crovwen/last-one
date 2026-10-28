"""قبایل: تأسیس، عضویت، نقش‌ها، خزانه و انبار (دارایی شخصی / امانی / عمومی)، ساختمان، دیپلماسی."""
from __future__ import annotations

from app.services import homes
from app.services import inventory as inv
from app.services.accounts import validate_name
from app.services.chars import jload
from app.services.economy import InsufficientFunds, change_coins
from app.services.errors import GameError
from app.services.notify import notify

ROLE_FA = {"leader": "رهبر", "deputy": "جانشین", "commander": "فرمانده", "guard": "نگهبان",
           "resources": "مسئول منابع", "member": "عضو"}
ROLES = list(ROLE_FA)
PERMS = {
    "invite": {"leader", "deputy", "commander"},
    "kick": {"leader", "deputy"},
    "withdraw": {"leader", "deputy", "resources"},
    "build": {"leader", "deputy"},
    "membership": {"leader", "deputy"},
    "diplomacy": {"leader", "deputy"},
    "attack": {"leader", "deputy", "commander"},
    "roles": {"leader"},
    "upgrade": {"leader"},
    "defense": {"leader", "deputy", "commander", "guard"},
}
MEMBERSHIP_FA = {"public": "عمومی", "private": "خصوصی", "locked": "قفل‌شده"}


async def membership(conn, user_id: int):
    return await conn.fetchrow(
        "SELECT m.*, t.name, t.level, t.coins, t.membership, t.leader_id FROM tribe_members m "
        "JOIN tribes t ON t.id=m.tribe_id WHERE m.user_id=$1", user_id)


def allowed(role: str, perm: str) -> bool:
    return role in PERMS[perm]


async def need(conn, user_id: int, perm: str):
    m = await membership(conn, user_id)
    if m is None:
        raise GameError("عضو قبیله‌ای نیستی.")
    if not allowed(m["role"], perm):
        raise GameError("دسترسی لازم را نداری.")
    return m


async def tribe_stats(conn, tribe_id: int) -> dict:
    s = {"defense": 0.0, "alarm": 0.0, "storage": 0.0, "guard_slots": 0, "kennel_slots": 0}
    for b in await homes.user_buildings(conn, "tribe", tribe_id):
        eff = jload(b["effects"])
        for k in ("defense", "alarm", "storage"):
            s[k] += float(eff.get(k, 0)) * b["level"]
        for k in ("guard_slots", "kennel_slots"):
            s[k] += int(eff.get(k, 0)) * b["level"]
    return s


async def member_cap(conn, settings, tribe_id: int) -> int:
    lvl = await conn.fetchval("SELECT level FROM tribes WHERE id=$1", tribe_id)
    return int(settings.get("tribe.levels")[str(lvl)]["members"])


async def count_members(conn, tribe_id: int) -> int:
    return await conn.fetchval("SELECT count(*) FROM tribe_members WHERE tribe_id=$1", tribe_id)


async def create(conn, settings, user_id: int, name: str) -> int:
    if await membership(conn, user_id):
        raise GameError("همین حالا عضو یک قبیله هستی.")
    err = validate_name(name, 3, 20)
    if err:
        raise GameError(err)
    name = " ".join(name.split())
    try:
        await change_coins(conn, user_id, -int(settings.get("tribe.found_cost")), "found_tribe")
    except InsufficientFunds as exc:
        raise GameError(f"سکه کافی نداری (هزینه تأسیس: {settings.get('tribe.found_cost')}).") from exc
    if await conn.fetchval("SELECT 1 FROM tribes WHERE lower(name)=lower($1)", name):
        raise GameError("این نام قبلاً گرفته شده.")
    tid = await conn.fetchval("INSERT INTO tribes(name, leader_id) VALUES($1,$2) RETURNING id", name, user_id)
    await conn.execute("INSERT INTO tribe_members(user_id, tribe_id, role) VALUES($1,$2,'leader')", user_id, tid)
    return tid


async def list_open(conn):
    return await conn.fetch(
        "SELECT t.id, t.name, t.level, t.membership, (SELECT count(*) FROM tribe_members WHERE tribe_id=t.id) AS n "
        "FROM tribes t WHERE t.membership <> 'locked' ORDER BY n DESC LIMIT 15")


async def _add_member(conn, settings, tribe_id: int, user_id: int):
    if await membership(conn, user_id):
        raise GameError("این بازیکن عضو قبیله دیگری است.")
    if await count_members(conn, tribe_id) >= await member_cap(conn, settings, tribe_id):
        raise GameError("ظرفیت قبیله پر است.")
    await conn.execute("INSERT INTO tribe_members(user_id, tribe_id) VALUES($1,$2)", user_id, tribe_id)
    await conn.execute("DELETE FROM tribe_requests WHERE user_id=$1", user_id)


async def join(conn, settings, user_id: int, tribe_id: int) -> str:
    t = await conn.fetchrow("SELECT * FROM tribes WHERE id=$1 FOR UPDATE", tribe_id)
    if t is None:
        raise GameError("قبیله پیدا نشد.")
    if t["membership"] == "locked":
        raise GameError("🔒 عضوگیری این قبیله قفل است.")
    if t["membership"] == "private":
        await conn.execute("INSERT INTO tribe_requests(tribe_id, user_id, kind) VALUES($1,$2,'request') ON CONFLICT DO NOTHING",
                           tribe_id, user_id)
        await notify(conn, t["leader_id"], "tribe_request", "📨 درخواست عضویت جدید برای قبیله‌ات رسید.")
        return "درخواستت برای رهبران قبیله ارسال شد."
    await _add_member(conn, settings, tribe_id, user_id)
    return f"به قبیله «{t['name']}» پیوستی."


async def requests_of(conn, tribe_id: int):
    return await conn.fetch(
        "SELECT r.user_id, c.name FROM tribe_requests r JOIN characters c ON c.user_id=r.user_id "
        "WHERE r.tribe_id=$1 AND r.kind='request' ORDER BY r.created_at", tribe_id)


async def decide_request(conn, settings, actor: int, target: int, accept: bool):
    m = await need(conn, actor, "invite")
    r = await conn.fetchval("DELETE FROM tribe_requests WHERE tribe_id=$1 AND user_id=$2 AND kind='request' RETURNING 1",
                            m["tribe_id"], target)
    if not r:
        raise GameError("درخواست پیدا نشد.")
    if accept:
        await _add_member(conn, settings, m["tribe_id"], target)
        await notify(conn, target, "tribe", f"✅ به قبیله «{m['name']}» پذیرفته شدی.")
    else:
        await notify(conn, target, "tribe", f"❌ درخواست عضویتت در «{m['name']}» رد شد.")


async def invite(conn, settings, actor: int, target: int):
    m = await need(conn, actor, "invite")
    if m["membership"] == "locked":
        raise GameError("عضوگیری قفل است.")
    if await membership(conn, target):
        raise GameError("این بازیکن عضو قبیله است.")
    await conn.execute("INSERT INTO tribe_requests(tribe_id, user_id, kind) VALUES($1,$2,'invite') ON CONFLICT DO NOTHING",
                       m["tribe_id"], target)
    await notify(conn, target, "tribe_invite", f"📨 قبیله «{m['name']}» تو را دعوت کرد. از منوی قبایل بپذیر.")


async def my_invites(conn, user_id: int):
    return await conn.fetch(
        "SELECT r.tribe_id, t.name FROM tribe_requests r JOIN tribes t ON t.id=r.tribe_id WHERE r.user_id=$1 AND r.kind='invite'", user_id)


async def accept_invite(conn, settings, user_id: int, tribe_id: int):
    if not await conn.fetchval("DELETE FROM tribe_requests WHERE tribe_id=$1 AND user_id=$2 AND kind='invite' RETURNING 1",
                               tribe_id, user_id):
        raise GameError("دعوتی وجود ندارد.")
    t = await conn.fetchrow("SELECT * FROM tribes WHERE id=$1 FOR UPDATE", tribe_id)
    await _add_member(conn, settings, tribe_id, user_id)
    return t["name"]


async def _return_loans_of(conn, tribe_id: int, user_id: int):
    """وسایل امانی بازیکن هنگام خروج به انبار شخصی‌اش برمی‌گردد."""
    for l in await conn.fetch("SELECT * FROM tribe_loans WHERE tribe_id=$1 AND user_id=$2 AND status='active' FOR UPDATE",
                              tribe_id, user_id):
        try:
            if l["instance_id"]:
                await inv.move_instance(conn, l["instance_id"], "tribe", tribe_id, "storage", user_id, "loan_return_leave")
            else:
                await inv.move_stack(conn, "tribe_loan", tribe_id, "storage", user_id, l["def_id"], l["qty"], "loan_return_leave")
        except GameError:
            await conn.execute("UPDATE tribe_loans SET status='lost' WHERE id=$1", l["id"])
            continue
        await conn.execute("UPDATE tribe_loans SET status='returned' WHERE id=$1", l["id"])


async def leave(conn, user_id: int):
    m = await membership(conn, user_id)
    if m is None:
        raise GameError("عضو قبیله‌ای نیستی.")
    n = await count_members(conn, m["tribe_id"])
    if m["role"] == "leader" and n > 1:
        raise GameError("رهبر باید اول رهبری را به دیگری بدهد.")
    await _return_loans_of(conn, m["tribe_id"], user_id)
    await conn.execute("UPDATE hirelings SET post='idle' WHERE owner_user_id=$1 AND post='tribe_defense'", user_id)
    await conn.execute("DELETE FROM tribe_members WHERE user_id=$1", user_id)
    if n == 1:   # آخرین عضو: قبیله منحل می‌شود و دارایی عمومی‌اش از بین نمی‌رود تا مدیر رسیدگی کند
        await conn.execute("UPDATE tribes SET membership='locked' WHERE id=$1", m["tribe_id"])


async def members(conn, tribe_id: int):
    return await conn.fetch(
        "SELECT m.user_id, m.role, c.name FROM tribe_members m JOIN characters c ON c.user_id=m.user_id "
        "WHERE m.tribe_id=$1 ORDER BY array_position(ARRAY['leader','deputy','commander','guard','resources','member'], m.role), c.name",
        tribe_id)


async def kick(conn, actor: int, target: int):
    m = await need(conn, actor, "kick")
    t = await conn.fetchrow("SELECT * FROM tribe_members WHERE user_id=$1 AND tribe_id=$2", target, m["tribe_id"])
    if t is None or t["role"] == "leader" or target == actor:
        raise GameError("اخراج این عضو ممکن نیست.")
    if m["role"] == "deputy" and t["role"] == "deputy":
        raise GameError("اجازه نداری.")
    await _return_loans_of(conn, m["tribe_id"], target)
    await conn.execute("UPDATE hirelings SET post='idle' WHERE owner_user_id=$1 AND post='tribe_defense'", target)
    await conn.execute("DELETE FROM tribe_members WHERE user_id=$1", target)
    await notify(conn, target, "tribe", f"🚪 از قبیله «{m['name']}» اخراج شدی. دارایی شخصی‌ات دست‌نخورده است.")


async def set_role(conn, actor: int, target: int, role: str):
    m = await need(conn, actor, "roles")
    if role not in ROLES:
        raise GameError("نقش نامعتبر.")
    t = await conn.fetchrow("SELECT * FROM tribe_members WHERE user_id=$1 AND tribe_id=$2", target, m["tribe_id"])
    if t is None or target == actor:
        raise GameError("عضو پیدا نشد.")
    if role == "leader":
        await conn.execute("UPDATE tribe_members SET role='deputy' WHERE user_id=$1", actor)
        await conn.execute("UPDATE tribes SET leader_id=$2 WHERE id=$1", m["tribe_id"], target)
    await conn.execute("UPDATE tribe_members SET role=$2 WHERE user_id=$1", target, role)
    await notify(conn, target, "tribe", f"🎖 نقش تو در قبیله «{m['name']}» شد: {ROLE_FA[role]}")


async def set_membership(conn, actor: int, mode: str):
    m = await need(conn, actor, "membership")
    if mode not in MEMBERSHIP_FA:
        raise GameError("حالت نامعتبر.")
    await conn.execute("UPDATE tribes SET membership=$2 WHERE id=$1", m["tribe_id"], mode)


async def upgrade(conn, settings, actor: int):
    m = await need(conn, actor, "upgrade")
    levels = settings.get("tribe.levels")
    nxt = str(m["level"] + 1)
    if nxt not in levels:
        raise GameError("قبیله به بالاترین سطح رسیده است.")
    cost = int(levels[nxt]["cost"])
    ok = await conn.fetchval("UPDATE tribes SET coins=coins-$2, level=level+1 WHERE id=$1 AND coins >= $2 RETURNING level",
                             m["tribe_id"], cost)
    if ok is None:
        raise GameError(f"خزانه کافی نیست (نیاز: {cost}).")
    await conn.execute("INSERT INTO tribe_ledger(tribe_id, user_id, kind, coins, note) VALUES($1,$2,'upgrade',$3,'level')",
                       m["tribe_id"], actor, -cost)
    return ok


# ───────── خزانه و انبار ─────────
async def donate_coins(conn, user_id: int, amount: int):
    m = await membership(conn, user_id)
    if m is None or amount <= 0:
        raise GameError("درخواست نامعتبر.")
    try:
        await change_coins(conn, user_id, -amount, "tribe_donate", ref=str(m["tribe_id"]))
    except InsufficientFunds as exc:
        raise GameError("سکه کافی نداری.") from exc
    await conn.execute("UPDATE tribes SET coins=coins+$2 WHERE id=$1", m["tribe_id"], amount)
    await conn.execute("INSERT INTO tribe_ledger(tribe_id, user_id, kind, coins) VALUES($1,$2,'donate_coins',$3)",
                       m["tribe_id"], user_id, amount)


async def withdraw_coins(conn, actor: int, amount: int):
    m = await need(conn, actor, "withdraw")
    ok = await conn.fetchval("UPDATE tribes SET coins=coins-$2 WHERE id=$1 AND coins>=$2 RETURNING 1", m["tribe_id"], amount)
    if not ok or amount <= 0:
        raise GameError("خزانه کافی نیست.")
    await change_coins(conn, actor, amount, "tribe_withdraw", ref=str(m["tribe_id"]))
    await conn.execute("INSERT INTO tribe_ledger(tribe_id, user_id, kind, coins) VALUES($1,$2,'withdraw_coins',$3)",
                       m["tribe_id"], actor, -amount)


async def _deposit(conn, settings, user_id: int, def_id: int, qty: int, iid, loan: bool):
    m = await membership(conn, user_id)
    if m is None:
        raise GameError("عضو قبیله‌ای نیستی.")
    d = await inv.def_by_id(conn, def_id)
    if not d["tradable"]:
        raise GameError("این وسیله قابل انتقال نیست.")
    ts = await tribe_stats(conn, m["tribe_id"])
    cap = ts["storage"]
    used = await inv.holder_weight(conn, "tribe", m["tribe_id"]) + await inv.holder_weight(conn, "tribe_loan", m["tribe_id"])
    if used + float(d["weight"]) * (1 if iid else qty) > cap:
        raise GameError("انبار قبیله جا ندارد؛ ساختمان انبار یا خزانه را بساز/ارتقا بده.")
    if iid:
        row = await conn.fetchrow("SELECT * FROM item_instances WHERE id=$1 FOR UPDATE", iid)
        if row is None or row["owner_user_id"] != user_id or row["bound"] or row["holder_id"] != user_id \
                or row["holder_type"] not in ("backpack", "storage"):
            raise GameError("این وسیله قابل واگذاری نیست.")
        await inv.move_instance(conn, iid, row["holder_type"], user_id, "tribe", m["tribe_id"], "tribe_loan" if loan else "tribe_donate")
        if loan:
            await conn.execute("INSERT INTO tribe_loans(tribe_id, user_id, def_id, qty, instance_id) VALUES($1,$2,$3,1,$4)",
                               m["tribe_id"], user_id, def_id, iid)
        else:
            await conn.execute("UPDATE item_instances SET owner_user_id=NULL, owner_tribe_id=$2 WHERE id=$1", iid, m["tribe_id"])
    else:
        src = "backpack" if await inv.stack_qty(conn, "backpack", user_id, def_id) >= qty else "storage"
        dst = "tribe_loan" if loan else "tribe"
        await inv.move_stack(conn, src, user_id, dst, m["tribe_id"], def_id, qty, "tribe_loan" if loan else "tribe_donate")
        if loan:
            await conn.execute("INSERT INTO tribe_loans(tribe_id, user_id, def_id, qty) VALUES($1,$2,$3,$4)",
                               m["tribe_id"], user_id, def_id, qty)
    await conn.execute("INSERT INTO tribe_ledger(tribe_id, user_id, kind, def_id, qty) VALUES($1,$2,$3,$4,$5)",
                       m["tribe_id"], user_id, "loan" if loan else "donate_item", def_id, 1 if iid else qty)


async def donate_item(conn, settings, user_id, def_id, qty=1, iid=None):
    await _deposit(conn, settings, user_id, def_id, qty, iid, False)


async def loan_item(conn, settings, user_id, def_id, qty=1, iid=None):
    await _deposit(conn, settings, user_id, def_id, qty, iid, True)


async def pool_items(conn, tribe_id: int):
    stacks = await conn.fetch(
        "SELECT s.def_id, s.qty, d.name FROM item_stacks s JOIN item_defs d ON d.id=s.def_id "
        "WHERE s.holder_type='tribe' AND s.holder_id=$1 ORDER BY d.name", tribe_id)
    inst = await conn.fetch(
        "SELECT i.id, i.def_id, i.owner_user_id, d.name FROM item_instances i JOIN item_defs d ON d.id=i.def_id "
        "WHERE i.holder_type='tribe' AND i.holder_id=$1 ORDER BY d.name", tribe_id)
    return stacks, inst


async def withdraw_item(conn, settings, actor: int, ch, def_id: int, qty: int = 1, iid=None):
    """برداشت از دارایی عمومی (به‌صورت مالکیت شخصی) یا قرض‌گرفتن وسیله امانی (مالکیت اصلی ثابت)."""
    m = await need(conn, actor, "withdraw")
    d = await inv.def_by_id(conn, def_id)
    free = await inv.free_weight(conn, settings, actor, ch)
    if float(d["weight"]) * (1 if iid else qty) > free:
        raise GameError("کوله‌پشتی جا ندارد.")
    if iid:
        row = await conn.fetchrow("SELECT * FROM item_instances WHERE id=$1 FOR UPDATE", iid)
        if row is None or row["holder_type"] != "tribe" or row["holder_id"] != m["tribe_id"]:
            raise GameError("این وسیله در انبار نیست.")
        await inv.move_instance(conn, iid, "tribe", m["tribe_id"], "backpack", actor, "tribe_withdraw")
        if row["owner_tribe_id"]:
            await conn.execute("UPDATE item_instances SET owner_user_id=$2, owner_tribe_id=NULL WHERE id=$1", iid, actor)
    else:
        await inv.move_stack(conn, "tribe", m["tribe_id"], "backpack", actor, def_id, qty, "tribe_withdraw")
    await conn.execute("INSERT INTO tribe_ledger(tribe_id, user_id, kind, def_id, qty) VALUES($1,$2,'withdraw_item',$3,$4)",
                       m["tribe_id"], actor, def_id, -(1 if iid else qty))


async def recall_loan(conn, user_id: int, loan_id: int):
    l = await conn.fetchrow("SELECT * FROM tribe_loans WHERE id=$1 AND user_id=$2 AND status='active' FOR UPDATE", loan_id, user_id)
    if l is None:
        raise GameError("امانت پیدا نشد.")
    if l["instance_id"]:
        await inv.move_instance(conn, l["instance_id"], "tribe", l["tribe_id"], "storage", user_id, "loan_recall")
    else:
        await inv.move_stack(conn, "tribe_loan", l["tribe_id"], "storage", user_id, l["def_id"], l["qty"], "loan_recall")
    await conn.execute("UPDATE tribe_loans SET status='returned' WHERE id=$1", loan_id)
    await conn.execute("INSERT INTO tribe_ledger(tribe_id, user_id, kind, def_id, qty) VALUES($1,$2,'return',$3,$4)",
                       l["tribe_id"], user_id, l["def_id"], l["qty"])


async def my_loans(conn, user_id: int):
    return await conn.fetch(
        "SELECT l.id, l.qty, d.name FROM tribe_loans l JOIN item_defs d ON d.id=l.def_id "
        "WHERE l.user_id=$1 AND l.status='active'", user_id)


async def ledger(conn, tribe_id: int, limit: int = 12):
    return await conn.fetch(
        "SELECT l.*, c.name AS uname, d.name AS dname FROM tribe_ledger l LEFT JOIN characters c ON c.user_id=l.user_id "
        "LEFT JOIN item_defs d ON d.id=l.def_id WHERE l.tribe_id=$1 ORDER BY l.id DESC LIMIT $2", tribe_id, limit)


# ───────── ساختمان‌ها ─────────
async def build(conn, settings, actor: int, code: str):
    m = await need(conn, actor, "build")
    return await homes.start_build(conn, settings, actor, code, 1.0, m["tribe_id"], m["level"])


async def set_defense_post(conn, settings, user_id: int, hid: int, on: bool):
    m = await membership(conn, user_id)
    if m is None:
        raise GameError("عضو قبیله‌ای نیستی.")
    ts = await tribe_stats(conn, m["tribe_id"])
    n = await conn.fetchval("SELECT count(*) FROM hirelings h JOIN tribe_members t ON t.user_id=h.owner_user_id "
                            "WHERE t.tribe_id=$1 AND h.post='tribe_defense' AND h.status='active' AND h.id<>$2", m["tribe_id"], hid)
    if on and n >= ts["guard_slots"]:
        raise GameError("ظرفیت نیروهای دفاعی قبیله پر است (ساختمان نیروهای دفاعی را بساز/ارتقا بده).")
    await conn.execute("UPDATE hirelings SET post=$3 WHERE id=$1 AND owner_user_id=$2 AND status='active'", hid, user_id,
                       "tribe_defense" if on else "idle")


# ───────── دیپلماسی ─────────
async def relations(conn, tribe_id: int):
    return await conn.fetch(
        "SELECT r.*, ta.name AS a_name, tb.name AS b_name FROM tribe_relations r JOIN tribes ta ON ta.id=r.tribe_a "
        "JOIN tribes tb ON tb.id=r.tribe_b WHERE r.tribe_a=$1 OR r.tribe_b=$1", tribe_id)


async def propose(conn, actor: int, other_name: str, kind: str):
    m = await need(conn, actor, "diplomacy")
    o = await conn.fetchrow("SELECT * FROM tribes WHERE lower(name)=lower($1)", other_name.strip())
    if o is None or o["id"] == m["tribe_id"] or kind not in ("ally", "rival"):
        raise GameError("قبیله پیدا نشد یا درخواست نامعتبر است.")
    await conn.execute("DELETE FROM tribe_relations WHERE (tribe_a=$1 AND tribe_b=$2) OR (tribe_a=$2 AND tribe_b=$1)", m["tribe_id"], o["id"])
    status = "active" if kind == "rival" else "pending"
    await conn.execute("INSERT INTO tribe_relations(tribe_a, tribe_b, kind, status) VALUES($1,$2,$3,$4)", m["tribe_id"], o["id"], kind, status)
    if kind == "ally":
        await notify(conn, o["leader_id"], "tribe_diplo", f"🤝 قبیله «{m['name']}» پیشنهاد اتحاد داد. از منوی قبیله بپذیر.")
    else:
        await notify(conn, o["leader_id"], "tribe_diplo", f"⚔️ قبیله «{m['name']}» شما را رقیب اعلام کرد.")
    return status


async def respond_alliance(conn, actor: int, other_id: int, accept: bool):
    m = await need(conn, actor, "diplomacy")
    r = await conn.fetchrow("SELECT * FROM tribe_relations WHERE tribe_a=$1 AND tribe_b=$2 AND kind='ally' AND status='pending'",
                            other_id, m["tribe_id"])
    if r is None:
        raise GameError("پیشنهادی نیست.")
    if accept:
        await conn.execute("UPDATE tribe_relations SET status='active' WHERE tribe_a=$1 AND tribe_b=$2", other_id, m["tribe_id"])
    else:
        await conn.execute("DELETE FROM tribe_relations WHERE tribe_a=$1 AND tribe_b=$2", other_id, m["tribe_id"])


async def other_tribes(conn, tribe_id: int):
    return await conn.fetch("SELECT id, name, level FROM tribes WHERE id<>$1 ORDER BY level DESC, name LIMIT 20", tribe_id)
