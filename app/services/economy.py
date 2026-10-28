"""منطق سکه — مستقل از تلگرام. همیشه باید داخل تراکنش (conn.transaction) صدا زده شود."""
from __future__ import annotations


class InsufficientFunds(Exception):
    pass


async def change_coins(conn, user_id: int, delta: int, reason: str, ref: str = None) -> int:
    """تغییر اتمیک موجودی با قفل ردیف و ثبت در دفتر کل. موجودی جدید را برمی‌گرداند."""
    row = await conn.fetchrow("SELECT coins FROM users WHERE id = $1 FOR UPDATE", user_id)
    if row is None:
        raise ValueError("کاربر پیدا نشد")
    new_balance = row["coins"] + delta
    if new_balance < 0:
        raise InsufficientFunds()
    if delta < 0 and not reason.startswith("transfer"):
        await conn.execute("UPDATE users SET coins=$2, locked_coins=LEAST(locked_coins, $2) WHERE id=$1", user_id, new_balance)
    else:
        await conn.execute("UPDATE users SET coins = $2 WHERE id = $1", user_id, new_balance)
    await conn.execute(
        "INSERT INTO coin_ledger(user_id, delta, balance_after, reason, ref) VALUES($1, $2, $3, $4, $5)",
        user_id, delta, new_balance, reason, ref,
    )
    return new_balance
