"""کلمه‌های فارسی که هر بخش بازی را باز می‌کنند (چت خصوصی و گروه)."""
from __future__ import annotations

import re

from app.bot.ui import ACTIONS, render
from app.context import AppContext
from app.runtime import with_user


def norm(text: str) -> str:
    t = (text or "").replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ").replace("‌", " ")
    t = re.sub(r"[ً-ٕ]", "", t)
    return re.sub(r"\s+", " ", t).strip().lower()


# عبارت نرمال‌شده → (اکشن، آرگومان)
KEYWORDS = {}


def _k(action: str, x: str = "", *words: str):
    for w in words:
        KEYWORDS[norm(w)] = (action, x)


_k("main", "", "منو", "منوی اصلی", "منو اصلی", "menu")
_k("pf", "", "پروفایل", "شخصیت", "وضعیت", "موجودی")
_k("fo", "", "جنگل", "جستجو", "جمع آوری", "جمع‌آوری", "شکار", "کاوش", "فعالیت")
_k("fo_rest", "", "استراحت", "خواب")
_k("ms", "", "مأموریت", "ماموریت")
_k("inv", "", "کوله", "کوله پشتی", "کوله‌پشتی", "وسایل", "تجهیزات", "اینونتوری")
_k("sto", "", "انبار")
_k("cr", "", "ساخت", "ساخت وسایل", "ساخت وسیله", "کرافت")
_k("ho", "", "خانه", "خانه من")
_k("bl", "", "ساخت خانه", "ارتقا خانه", "ساخت و ارتقا")
_k("an", "", "حیوانات", "حیوان", "سگ")
_k("anb", "", "خرید حیوان", "فروشگاه حیوان")
_k("hr", "", "نیروها", "نیرو", "نگهبان", "نگهبانان")
_k("hrb", "", "استخدام", "استخدام نیرو")
_k("tb", "", "قبیله", "قبایل")
_k("mk", "", "مارکت", "بازار")
_k("shop", "", "فروشگاه")
_k("pm", "", "بازار بازیکنان", "آگهی ها", "آگهی‌ها")
_k("pmy", "", "آگهی های من", "آگهی‌های من")
_k("tr", "", "معامله", "معامله امن", "ترید")
_k("xf", "", "انتقال سکه", "انتقال")
_k("rp", "", "گزارش", "گزارش ها", "گزارش‌ها", "اخبار")
_k("ss", "", "فصل", "فصل رقابتی")
_k("cp", "", "اسارت")
_k("cap_l", "", "اسرا", "اسرای من")
_k("st", "", "تنظیمات", "رفتار")
_k("prot", "on", "محافظ روشن", "محافظ را روشن کن")
_k("prot", "off", "محافظ خاموش", "محافظ را خاموش کن")
_k("prot", "", "محافظ")
_k("help", "", "راهنما", "کمک", "help")
_k("help_cmds", "", "دستورات", "لیست دستورات", "دستور")
_k("ad", "", "پنل", "پنل ادمین", "ادمین")

SLASH = {"menu": "main", "start_menu": "main", "help": "help", "forest": "fo", "inv": "inv", "home": "ho",
         "profile": "pf", "tribe": "tb", "market": "mk", "admin": "ad"}


async def open_action(ctx: AppContext, tg_id: int, action: str, x: str = "", y: str = ""):
    """اکشن را اجرا می‌کند و (متن، کیبورد) آماده ارسال را برمی‌گرداند."""
    fn = ACTIONS.get(action, ACTIONS["main"])

    async def run(conn, user, ch):
        return await fn(ctx, conn, user, ch, x, y)

    screen, msgs = await with_user(ctx, tg_id, run)
    text, markup = render(screen, tg_id, msgs)
    return screen, text, markup
