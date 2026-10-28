"""قوانین خالص بازی (بدون دیتابیس) — کاملاً قابل‌تست. اعداد از تنظیمات می‌آیند."""
from __future__ import annotations

import random
from datetime import datetime, timedelta


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ───────── رشد ─────────
def skill_xp_needed(level: int, base: int) -> int:
    return level * level * base


def char_xp_needed(level: int, base: int) -> int:
    return level * base


def add_xp(level: int, xp: int, gain: int, need_fn, max_level: int):
    """(سطح جدید, تجربه جدید, تعداد ارتقا)"""
    xp += max(0, gain)
    ups = 0
    while level < max_level and xp >= need_fn(level):
        xp -= need_fn(level)
        level += 1
        ups += 1
    if level >= max_level:
        xp = 0
    return level, xp, ups


# ───────── شب و روز و آب‌وهوا ─────────
WEATHER_FA = {"clear": "صاف ☀️", "rain": "بارانی 🌧", "cold": "سرد 🥶", "storm": "طوفانی ⛈"}


def environment(now_utc: datetime, get) -> dict:
    """شب/روز و آب‌وهوای فعلی جهان؛ قطعی و مشترک برای همه (هر ۶ ساعت یک وضعیت)."""
    local = now_utc + timedelta(minutes=int(get("env.tz_offset_min")))
    h = local.hour
    s, e = int(get("env.night_start_hour")), int(get("env.night_end_hour"))
    night = (h >= s or h < e) if s > e else (s <= h < e)
    seed = local.toordinal() * 4 + h // 6
    w = ["clear", "clear", "clear", "rain", "cold", "clear", "storm", "rain"][(seed * 2654435761) % 8]
    return {"night": night, "weather": w, "hour": h}


def env_energy_mult(env: dict, get) -> float:
    m = float(get("env.night_energy_mult")) if env["night"] else 1.0
    if env["weather"] in ("rain", "cold", "storm"):
        m *= float(get("env.bad_weather_energy_mult"))
    return m


# ───────── نیازهای حیاتی (محاسبه تنبل بر اساس زمان) ─────────
def compute_vitals(ch: dict, now: datetime, get, env: dict = None) -> dict:
    """مقادیر جدید سلامتی/سیری/... را بر اساس زمان سپری‌شده برمی‌گرداند. هرگز باعث مرگ نمی‌شود."""
    hours = max((now - ch["vitals_updated_at"]).total_seconds() / 3600.0, 0.0)
    out = {k: ch[k] for k in ("health", "hunger", "thirst", "energy", "fatigue")}
    if hours <= 0 or ch["location"] in ("dead",):
        return out
    resting = ch["location"] == "resting"
    active = ch["location"] in ("hunting", "gathering", "exploring", "operation")
    hmult = float(get("env.cold_hunger_mult")) if env and env["weather"] == "cold" and ch["location"] != "resting" else 1.0
    out["hunger"] = clamp(ch["hunger"] - get("vitals.hunger_per_hour") * hmult * hours, 0, 100)
    out["thirst"] = clamp(ch["thirst"] - get("vitals.thirst_per_hour") * hours, 0, 100)
    if resting:
        out["energy"] = clamp(ch["energy"] + get("vitals.energy_rest_per_hour") * hours, 0, 100)
        out["fatigue"] = clamp(ch["fatigue"] - get("vitals.fatigue_rest_per_hour") * hours, 0, 100)
    elif active:
        out["fatigue"] = clamp(ch["fatigue"] + get("vitals.fatigue_per_hour") * hours, 0, 100)
    else:
        out["energy"] = clamp(ch["energy"] + get("vitals.energy_idle_per_hour") * hours, 0, 100)
        out["fatigue"] = clamp(ch["fatigue"] - 3 * hours, 0, 100)
    if out["hunger"] <= 0 or out["thirst"] <= 0:
        out["health"] = max(1, ch["health"] - get("vitals.starve_damage_per_hour") * hours)
    elif out["hunger"] > 40 and out["thirst"] > 40 and not active:
        out["health"] = clamp(ch["health"] + get("vitals.health_regen_per_hour") * hours, 0, 100)
    return {k: int(round(v)) for k, v in out.items()}


def condition_mult(hunger: int, thirst: int, fatigue: int, energy: int) -> float:
    """ضریب کارایی شخصیت: گرسنگی، تشنگی و خستگی قدرت را کم می‌کنند."""
    m = 1.0
    if hunger < 20:
        m -= 0.15 if hunger > 0 else 0.3
    if thirst < 20:
        m -= 0.15 if thirst > 0 else 0.3
    if fatigue > 70:
        m -= 0.2
    elif fatigue > 40:
        m -= 0.08
    if energy < 15:
        m -= 0.15
    return clamp(m, 0.3, 1.0)


def carry_capacity(carry_skill: int, bag_bonus: float, get) -> float:
    return float(get("carry.base_capacity")) + float(get("carry.per_skill")) * carry_skill + bag_bonus


# ───────── مبارزه ─────────
def luck(rng: random.Random, rng_range: float) -> float:
    return 1.0 + rng.uniform(-rng_range, rng_range)


def stealth_check(att_stealth: float, def_perception: float, bonus: float, rng: random.Random) -> bool:
    """غافلگیری: مخفی‌کاری مهاجم در برابر ادراک مدافع (با شانس)."""
    a = att_stealth + bonus + rng.uniform(0, 6)
    d = def_perception + rng.uniform(0, 6)
    return a > d


def escape_chance(base: float, endurance: float, hp_frac: float, siege_pen: float, power_ratio: float) -> float:
    """power_ratio = قدرت طرف مقابل / مجموع قدرت دو طرف."""
    c = base + endurance * 0.01 - (1 - hp_frac) * 0.3 - siege_pen - 0.2 * power_ratio
    return clamp(c, 0.05, 0.9)


def side_power(parts: list) -> float:
    """قدرت مؤثر طرف؛ هر نفر با کم‌شدن سلامتی ضعیف‌تر می‌شود."""
    total = 0.0
    for p in parts:
        if p["hp"] > 0:
            frac = p["hp"] / p["max"] if p.get("max") else 1.0
            total += p["power"] * (0.6 + 0.4 * frac)
    return total


def _alive_targets(parts):
    return [p for p in parts if p["hp"] > 0 and p["k"] != "struct"]


def apply_damage(parts: list, total: float, armor_k: float, rng: random.Random, tag: str) -> list:
    lines = []
    targets = _alive_targets(parts)
    if total <= 0 or not targets:
        return lines
    hits = max(1, min(4, len(targets) + 1))
    for _ in range(hits):
        targets = _alive_targets(parts)
        if not targets:
            break
        t = rng.choice(targets)
        dmg = total / hits * rng.uniform(0.7, 1.3)
        dmg *= 1 - min(0.6, t.get("def", 0) * armor_k / 10.0)
        dmg = max(1, int(round(dmg)))
        t["hp"] = max(0, t["hp"] - dmg)
        t["dmg_taken"] = t.get("dmg_taken", 0) + dmg
        if t["hp"] == 0:
            lines.append(f"💀 {t['name']} از پا افتاد")
        else:
            lines.append(f"🩸 {t['name']} {dmg} آسیب دید")
    return lines


def resolve_round(att: list, dfn: list, att_mult: float, def_mult: float, p: dict, rng: random.Random,
                  trap: float = 0.0) -> dict:
    """یک دور نبرد. participants درجا تغییر می‌کنند. p = {dmg_k, armor_k, luck_range}"""
    ap = side_power(att) * att_mult * luck(rng, p["luck_range"])
    dp = side_power(dfn) * def_mult * luck(rng, p["luck_range"])
    tot = ap + dp
    lines = []
    if tot <= 0:
        return {"lines": lines, "ap": 0, "dp": 0}
    # برتری قدرت مهم است ولی نتیجه را قطعی نمی‌کند: سهم آسیب‌ها با حساسیت adv_k به برتری وابسته است
    adv = (ap - dp) / tot
    pool = tot / 2.0 * p["dmg_k"]
    sens = p.get("adv_k", 0.6)
    dmg_def = pool * 0.5 * (1 + sens * adv)
    dmg_att = pool * 0.5 * (1 - sens * adv) + trap
    lines += apply_damage(dfn, dmg_def, p["armor_k"], rng, "def")
    lines += apply_damage(att, dmg_att, p["armor_k"], rng, "att")
    return {"lines": lines, "ap": round(ap, 1), "dp": round(dp, 1)}


def attacker_down(att: list) -> bool:
    pl = [x for x in att if x["k"] == "player"]
    return bool(pl) and pl[0]["hp"] <= 0


def defender_down(dfn: list, flow: str) -> bool:
    if flow == "open":
        pl = [x for x in dfn if x["k"] == "player"]
        return bool(pl) and pl[0]["hp"] <= 0
    return not _alive_targets(dfn)


# ───────── شناسایی خانه ─────────
def defense_label(ratio: float) -> str:
    if ratio < 0.5:
        return "ضعیف"
    if ratio < 0.8:
        return "نسبتاً ضعیف"
    if ratio < 1.2:
        return "متوسط"
    if ratio < 1.8:
        return "نسبتاً قوی"
    return "قوی"


def fuzzy_count(n: int, perception: int, rng: random.Random) -> str:
    from app.bot_fmt import fa_num  # noqa: WPS433

    if perception >= 8:
        return fa_num(n)
    if perception >= 4:
        lo, hi = max(0, n - 1), n + 1
        return f"{fa_num(lo)} تا {fa_num(hi)}" if hi != lo else fa_num(n)
    return "چند نفر" if n else "هیچ‌کس"


def recon_report(info: dict, perception: int, att_power: float, rng: random.Random, extra: bool = False) -> list:
    """info: guards[{name,gear}], dogs[{breed}], defense_power, traps"""
    from app.bot_fmt import fa_num

    p = perception + (3 if extra else 0)
    noise = 1.0 if p >= 8 else (rng.uniform(0.8, 1.2) if p >= 4 else rng.uniform(0.55, 1.45))
    ratio = (info["defense_power"] * noise) / max(att_power, 1.0)
    lines = [f"نگهبان انسانی: {fuzzy_count(len(info['guards']), p, rng)}"]
    lines.append(f"سگ و حیوان نگهبان: {fuzzy_count(len(info['dogs']), p, rng)}")
    if info["dogs"] and p >= 4:
        lines.append("نوع حیوان: " + "، ".join(sorted({d['breed'] for d in info['dogs']})))
    gear = sorted({g for gd in info["guards"] for g in gd.get("gear", [])})
    if gear and p >= 3:
        lines.append("تجهیزات قابل‌مشاهده: " + "، ".join(gear))
    if info["traps"] and rng.random() < min(0.9, 0.15 + p * 0.08):
        lines.append("⚠️ نشانه‌هایی از تله دیده می‌شود")
    lines.append("وضعیت دفاعی: " + defense_label(ratio))
    return lines
