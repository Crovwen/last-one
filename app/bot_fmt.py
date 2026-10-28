"""ابزارهای قالب‌بندی مشترک (بدون وابستگی به تلگرام)."""
_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa_num(value) -> str:
    return str(value).translate(_FA)


def bar(value: int, maximum: int = 100, width: int = 10) -> str:
    value = max(0, min(value, maximum))
    filled = round(width * value / maximum) if maximum else 0
    return "▓" * filled + "░" * (width - filled)
