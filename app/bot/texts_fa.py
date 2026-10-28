"""همه متن‌های فارسی بازی یک‌جا (برای افزودن زبان‌های دیگر در آینده)."""
from __future__ import annotations

import json

from app.services.settings import SKILLS

from app.bot_fmt import bar, fa_num  # noqa: F401


WELCOME_NEW = (
    "🌲 <b>به LAST ONE خوش اومدی!</b>\n\n"
    "اینجا یه جنگل بزرگ و بی‌رحمه؛ فقط کسی که بتونه زنده بمونه، بسازه و متحد پیدا کنه تا آخر می‌مونه.\n\n"
    "اول یه اسم برای شخصیتت انتخاب کن (همین حالا بنویس):"
)
WELCOME_BACK = "🌲 <b>LAST ONE</b>\nخوش برگشتی، {name}!"
ASK_NAME_AGAIN = "اسم شخصیتت رو بنویس:"
CHARACTER_CREATED = "✅ شخصیت <b>{name}</b> ساخته شد.\nسکه اولیه‌ات: {coins} 🪙"
USE_MENU = "از منو استفاده کن 👇"
BANNED = "⛔️ حساب تو مسدود شده است."
SOON = "🚧 این بخش در فاز {phase} اضافه می‌شود."
NO_CHARACTER = "اول باید شخصیت بسازی. /start رو بزن."
GROUP_HINT = "برای بازی، در چت خصوصی ربات /start رو بزن."
STALE_BUTTON = "این دکمه مال تو نیست یا منقضی شده."

MENU_TITLES = {
    "profile": "👤 شخصیت",
    "backpack": "🎒 کوله‌پشتی",
    "forest": "🌲 جنگل",
    "home": "🏠 خانه",
    "animals": "🐺 حیوانات",
    "hirelings": "🛡 نیروها",
    "tribe": "🏕 قبیله",
    "market": "🛒 مارکت",
    "reports": "📜 گزارش‌ها",
    "settings": "⚙️ تنظیمات",
}
# بخش‌هایی که هنوز ساخته نشده‌اند و فاز ساختشان
PHASE_OF = {
    "backpack": 2, "forest": 3, "home": 6, "animals": 7,
    "hirelings": 8, "tribe": 9, "market": 10, "reports": 11, "settings": 2,
}


def profile_text(user, ch) -> str:
    lines = [
        f"👤 <b>{ch['name']}</b>",
        f"سطح شخصیت: {fa_num(ch['level'])}   |   سطح حساب: {fa_num(user['account_level'])}",
        f"🪙 سکه: {fa_num(user['coins'])}",
        "",
        f"❤️ سلامتی  {bar(ch['health'])} {fa_num(ch['health'])}",
        f"🍖 سیری    {bar(ch['hunger'])} {fa_num(ch['hunger'])}",
        f"💧 سیرابی  {bar(ch['thirst'])} {fa_num(ch['thirst'])}",
        f"⚡️ انرژی   {bar(ch['energy'])} {fa_num(ch['energy'])}",
        f"😮‍💨 خستگی  {bar(ch['fatigue'])} {fa_num(ch['fatigue'])}",
        "",
        "<b>مهارت‌ها</b>",
    ]
    skills = json.loads(ch["skills"]) if isinstance(ch["skills"], str) else ch["skills"]
    for key, title in SKILLS.items():
        lines.append(f"• {title}: {fa_num(skills.get(key, 1))}")
    return "\n".join(lines)
