"""زیرساخت رابط: ثبت اکشن‌های منو، کیبورد، ورودی متنی (وضعیت در دیتابیس)، ابزارهای قالب."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot_fmt import bar, fa_num  # noqa: F401
from app.services.chars import jload

esc = escape


class Act(CallbackData, prefix="a"):
    """u = تلگرام‌آیدی صاحب دکمه (دکمه دیگران رد می‌شود)، a = اکشن، x/y = آرگومان‌ها."""
    u: int
    a: str
    x: str = ""
    y: str = ""


@dataclass
class Screen:
    text: str
    rows: list = field(default_factory=list)    # [[(label, action, x, y), ...], ...]
    back: str = "main"
    note: str = ""
    combat_cid: int = 0       # اگر نبرد تازه‌ای ساخته شد، پیام آن بعد از ثبت ارسال می‌شود


def B(label: str, a: str, x="", y=""):
    return (label, a, str(x), str(y))


def keyboard(uid: int, rows: list, back: str = "main") -> InlineKeyboardMarkup:
    kb = [[InlineKeyboardButton(text=label, callback_data=Act(u=uid, a=a, x=x, y=y).pack()) for label, a, x, y in row]
          for row in rows if row]
    if back:
        nav = [InlineKeyboardButton(text="🔙 بازگشت" if back != "main" else "🏠 منوی اصلی",
                                    callback_data=Act(u=uid, a=back).pack())]
        if back != "main":
            nav.append(InlineKeyboardButton(text="🏠 منو", callback_data=Act(u=uid, a="main").pack()))
        kb.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=kb)


ACTIONS = {}
INPUTS = {}


def action(name: str):
    def deco(fn):
        ACTIONS[name] = fn
        return fn
    return deco


def text_input(kind: str):
    def deco(fn):
        INPUTS[kind] = fn
        return fn
    return deco


async def ask(conn, user_id: int, kind: str, data: dict, prompt: str, back: str = "main") -> Screen:
    await conn.execute("UPDATE users SET state=$2, state_data=$3::jsonb WHERE id=$1", user_id, "in:" + kind,
                       json.dumps(data, ensure_ascii=False))
    return Screen(prompt + "\n\n(برای انصراف دکمه بازگشت را بزن)", [[B("❌ انصراف", "cancel_in")]], back=back)


async def clear_state(conn, user_id: int):
    await conn.execute("UPDATE users SET state=NULL, state_data='{}'::jsonb WHERE id=$1", user_id)


def left(dt) -> str:
    sec = max(0, int((dt - datetime.now(timezone.utc)).total_seconds()))
    if sec >= 3600:
        return f"{fa_num(sec // 3600)} ساعت و {fa_num((sec % 3600) // 60)} دقیقه"
    if sec >= 60:
        return f"{fa_num(sec // 60)} دقیقه"
    return f"{fa_num(sec)} ثانیه"


def parse_int(text: str, lo: int = 1, hi: int = 10**9) -> int:
    from app.services.errors import GameError
    t = text.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    if not t.isdigit() or not (lo <= int(t) <= hi):
        raise GameError(f"یک عدد بین {lo} تا {hi} بفرست.")
    return int(t)


LOC_FA = {"home": "خانه 🏠", "resting": "استراحت 😴", "hunting": "در شکار 🏹", "gathering": "جمع‌آوری ⛏",
          "exploring": "اکتشاف 🧭", "operation": "در راه 🧭", "dead": "مرده ☠️", "captive": "اسیر ⛓",
          "tribe_base": "پایگاه قبیله 🏕"}
