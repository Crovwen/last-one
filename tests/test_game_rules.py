"""تست‌های قوانین بازی و محتوا (بدون دیتابیس و تلگرام)."""
import random
from datetime import datetime, timedelta, timezone

from app.services import rules
from app.services.seed import ACTIVITIES, BREEDS, BUILDINGS, HIRELINGS, ITEMS, MISSIONS

SETTINGS = {"vitals.hunger_per_hour": 4, "vitals.thirst_per_hour": 6, "vitals.fatigue_per_hour": 2,
            "vitals.energy_idle_per_hour": 6, "vitals.energy_rest_per_hour": 20, "vitals.fatigue_rest_per_hour": 15,
            "vitals.health_regen_per_hour": 3, "vitals.starve_damage_per_hour": 6}
P = {"dmg_k": 2.5, "armor_k": 0.15, "luck_range": 0.4, "adv_k": 0.5}


def _char(**kw):
    now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    ch = dict(health=80, hunger=80, thirst=80, energy=50, fatigue=50, location="home", vitals_updated_at=now - timedelta(hours=5))
    ch.update(kw)
    return ch, now


def test_vitals_idle_recovers_and_decays():
    ch, now = _char()
    v = rules.compute_vitals(ch, now, SETTINGS.get)
    assert v["hunger"] == 60 and v["thirst"] == 50 and v["energy"] == 80 and v["health"] > 80 - 1


def test_starvation_never_kills():
    ch, now = _char(hunger=0, thirst=0, health=5)
    v = rules.compute_vitals(ch, now, SETTINGS.get)
    assert v["health"] >= 1


def test_rest_is_better_than_idle():
    ch, now = _char(location="resting")
    rest = rules.compute_vitals(ch, now, SETTINGS.get)
    ch2, _ = _char()
    idle = rules.compute_vitals(ch2, now, SETTINGS.get)
    assert rest["energy"] >= idle["energy"] and rest["fatigue"] <= idle["fatigue"]


def test_dead_has_no_decay():
    ch, now = _char(location="dead", health=0)
    assert rules.compute_vitals(ch, now, SETTINGS.get)["hunger"] == 80


def test_condition_mult_penalizes():
    assert rules.condition_mult(80, 80, 0, 80) == 1.0
    assert rules.condition_mult(0, 0, 90, 5) < 0.6
    assert rules.condition_mult(0, 0, 100, 0) >= 0.3


def test_xp_levels():
    lvl, xp, ups = rules.add_xp(1, 0, 25, lambda l: rules.skill_xp_needed(l, 10), 50)
    assert lvl == 2 and ups == 1 and xp == 15
    lvl, xp, ups = rules.add_xp(50, 0, 999, lambda l: l * 10, 50)
    assert lvl == 50 and xp == 0


def _side(n, power, hp=60, k="hireling"):
    return [{"k": k, "id": i, "name": f"u{i}", "hp": hp, "max": hp, "power": power, "def": 2} for i in range(n)]


def _winrate(ratio, n=400):
    """مهاجم (۱۰۰ سلامتی) علیه ۲ نگهبان؛ ratio = قدرت مهاجم / مجموع قدرت نگهبان‌ها."""
    wins, rounds = 0, 0
    for seed in range(n):
        att = _side(1, ratio * 20, 100, "player")
        dfn = _side(2, 10, 50)
        rng = random.Random(seed)
        for k in range(1, 60):
            rules.resolve_round(att, dfn, 1.0, 1.0, P, rng)
            if att[0]["hp"] <= 0:
                break
            if rules.defender_down(dfn, "home"):
                wins += 1
                break
        rounds += k
    return wins / n, rounds / n


def test_power_matters_but_never_decides_everything():
    weak, _ = _winrate(0.8)
    even, r_even = _winrate(1.0)
    strong, _ = _winrate(1.6)
    assert weak < even < strong
    assert 0.02 < weak < 0.35         # ضعیف‌تر شانس کم اما واقعی دارد
    assert 0.2 < even < 0.65
    assert 0.6 < strong < 0.99        # قوی‌تر مطمئن نیست
    assert r_even < 9                 # نبرد از چند دور فراتر نمی‌رود


def test_armor_reduces_damage():
    a = [{"k": "player", "id": 1, "name": "a", "hp": 100, "max": 100, "power": 10, "def": 0}]
    b = [{"k": "player", "id": 2, "name": "b", "hp": 100, "max": 100, "power": 10, "def": 40}]
    for t in (a, b):
        rules.apply_damage(t, 40, 0.15, random.Random(3), "x")
    assert b[0]["hp"] > a[0]["hp"]


def test_escape_chance_bounds_and_monotonic():
    lo = rules.escape_chance(0.45, 0, 0.1, 0.4, 0.9)
    hi = rules.escape_chance(0.45, 50, 1.0, 0.0, 0.1)
    assert 0.05 <= lo < hi <= 0.9


def test_stealth_check_favors_stealthy():
    rng = random.Random(1)
    good = sum(rules.stealth_check(15, 3, 0, rng) for _ in range(200))
    bad = sum(rules.stealth_check(2, 15, 0, rng) for _ in range(200))
    assert good > 190 and bad < 10


def test_recon_report_accuracy_depends_on_perception():
    info = {"guards": [{"name": "g", "gear": ["شمشیر"]}] * 2, "dogs": [{"breed": "ژرمن شپرد"}], "traps": 1, "defense_power": 30}
    sharp = rules.recon_report(info, 10, 20, random.Random(1))
    dull = rules.recon_report(info, 1, 20, random.Random(1))
    assert any("۲" in l for l in sharp) and any("چند" in l for l in dull)
    assert any("وضعیت دفاعی" in l for l in sharp + dull)


def test_defense_label_scale():
    assert [rules.defense_label(r) for r in (0.2, 0.6, 1.0, 1.5, 3)] == ["ضعیف", "نسبتاً ضعیف", "متوسط", "نسبتاً قوی", "قوی"]


def test_seed_references_are_consistent():
    codes = {i["code"] for i in ITEMS}
    assert len(codes) == len(ITEMS)
    for i in ITEMS:
        for k in i["recipe"].get("inputs", {}):
            assert k in codes, (i["code"], k)
    breeds = {b["code"] for b in BREEDS}
    for a in ACTIVITIES:
        for l in a["loot"]:
            assert l.get("item", "coins") in codes | {"coins"}
            assert l.get("animal", "wolf_pup") in breeds
    for b in BUILDINGS:
        for k in b["cost"]:
            assert k == "coins" or k in codes
    for m in MISSIONS:
        assert m["kind"] != "deliver" or m["target"] in codes
    for b in BREEDS:
        g = b["perks"].get("grows_into")
        assert not g or g in breeds
    assert {b["kind"] for b in BUILDINGS if b["scope"] == "home"} >= {"house", "wall", "fence", "barbed", "trap", "gate", "guard_post", "kennel", "storage"}
    assert len(HIRELINGS) >= 5


def test_cost_scaling_and_text():
    from app.services import homes
    assert homes.cost_at({"coins": 100, "wood": 10}, 1.6, 1) == {"coins": 100, "wood": 10}
    assert homes.cost_at({"coins": 100}, 1.5, 3) == {"coins": 225}
    assert "🪙" in homes.cost_text({"coins": 5, "wood": 2}, {"wood": "چوب"})


def test_inventory_helpers():
    from app.services import inventory as inv
    assert inv.fit_qty(2.0, 7.0, 10) == 3 and inv.fit_qty(0, 1, 5) == 5 and inv.fit_qty(5, 1, 3) == 0
    assert inv.dur_factor(100, 100) == 1.0 and inv.dur_factor(0, 100) == 0.5 and inv.dur_factor(5, 0) == 1.0
    assert inv.repair_cost(100, 0, 100) == 50 and inv.repair_cost(100, 100, 100) == 1


def test_combat_describe_states():
    from app.services import combat
    side = {"att": [{"k": "player", "id": 1, "name": "A", "hp": 50, "max": 100, "power": 10, "def": 0, "end": 3}],
            "def": [{"k": "hireling", "id": 2, "name": "G", "hp": 30, "max": 50, "power": 5, "def": 0},
                    {"k": "struct", "id": 0, "name": "S", "hp": 1, "max": 1, "power": 0, "def": 0}]}
    mods = {"recon": ["نگهبان انسانی: ۱"], "result": "", "notice": "", "def_joined": False, "looter": "att", "raid_steps": 0, "drop_id": 5}
    base = dict(flow="home", side=None, sides=side, mods=mods, log=["x"], round_no=1, outcome=None)
    expect = {"recon": {"start", "recon", "cancel"}, "choose": {"direct", "ambush", "pursue", "siege", "cancel"},
              "round": {"continue", "flee", "join"}, "loot": {"take", "raid", "finish"}, "closed": set()}
    for st, acts in expect.items():
        text, btns = combat.describe(dict(base, state=st))
        assert {a for _, a in btns} == acts, st
        assert text
    assert "S" not in combat.describe(dict(base, state="round"))[0].split("مدافعان")[1]


def test_event_text():
    from app.services import events
    e = {"kind": "cargo", "status": "open", "data": {"slots": 3, "coins": 60, "claimed": 1}}
    t, b = events.event_text(e)
    assert b and "محموله" in t
    e = {"kind": "beast", "status": "done", "data": {"hp": 100, "name": "گرگ"}}
    t, b = events.event_text(e, [{"power": 30}])
    assert not b and "پایان" in t


def test_environment_is_deterministic_and_varied():
    from app.services.settings import DEFAULTS
    g = lambda k: DEFAULTS[k][0]  # noqa: E731
    t0 = datetime(2026, 10, 28, 0, 0, tzinfo=timezone.utc)
    seen, nights = set(), 0
    for i in range(24 * 10):
        e1 = rules.environment(t0 + timedelta(hours=i), g)
        assert e1 == rules.environment(t0 + timedelta(hours=i), g)
        seen.add(e1["weather"])
        nights += e1["night"]
    assert seen == {"clear", "rain", "cold", "storm"} and 60 < nights < 100
    e_night = {"night": True, "weather": "rain"}
    assert rules.env_energy_mult(e_night, g) > rules.env_energy_mult({"night": False, "weather": "clear"}, g) == 1.0


def test_cold_makes_hunger_faster():
    ch, now = _char()
    s = dict(SETTINGS, **{"env.cold_hunger_mult": 1.3})
    cold = rules.compute_vitals(ch, now, s.get, {"night": False, "weather": "cold"})
    clear = rules.compute_vitals(ch, now, s.get, {"night": False, "weather": "clear"})
    assert cold["hunger"] < clear["hunger"]


def test_mug_amount_small_and_scaled():
    from app.services.mug import mug_amount, mug_chance
    assert mug_amount(100, 0, 0.05, 20, 250) == 20            # حداقل
    assert mug_amount(2000, 0, 0.05, 20, 250) == 100          # متناسب با ثروت
    assert mug_amount(100000, 0, 0.05, 20, 250) == 250        # سقف
    assert mug_amount(100, 100, 0.05, 20, 250) == 0           # سکه‌های قفل دزدیده نمی‌شود
    assert mug_amount(10, 0, 0.05, 20, 250) <= 5              # هرگز همه پول نیست
    assert 0.1 <= mug_chance(1, 100) < mug_chance(100, 1) <= 0.9


def test_trap_detection_depends_on_trap_and_level():
    weak = rules.trap_detect_chance(5, 5, 8)
    strong = rules.trap_detect_chance(5, 5, 50)
    assert weak > strong
    assert rules.trap_detect_chance(5, 30, 20) > rules.trap_detect_chance(5, 1, 20)
    assert 0.1 <= strong <= weak <= 0.9


def test_attacker_down_needs_all_players():
    a = [{"k": "player", "id": 1, "hp": 0, "max": 100, "power": 1}, {"k": "player", "id": 2, "hp": 10, "max": 100, "power": 1}]
    assert not rules.attacker_down(a)
    a[1]["hp"] = 0
    assert rules.attacker_down(a)


def test_parse_mentions_and_group_commands():
    from app.bot.commands import KEYWORDS, norm
    from app.bot.handlers.group_text import parse_combat_command
    from app.services.party import parse_mentions
    assert parse_mentions("گروگانگیری با @Ali1234 @mamad_324 @ali1234") == ["ali1234", "mamad_324"]
    t = norm
    assert parse_combat_command(t("حمله")) == ("attack", [])
    assert parse_combat_command(t("حمله به خانه")) == ("raid", [])
    assert parse_combat_command(t("حمله به خانه با @a_bc @d_ef")) == ("raid", ["a_bc", "d_ef"])
    assert parse_combat_command(t("گروگانگیری با @a_bc")) == ("hostage", ["a_bc"])
    assert parse_combat_command(t("خفتگیری")) == ("mug", [])
    assert parse_combat_command(t("حمله به شهر")) is None
    assert KEYWORDS[t("جنگل")][0] == "fo" and KEYWORDS[t("خرید حیوان")][0] == "anb"
    assert KEYWORDS[t("محافظ خاموش")] == ("prot", "off") and KEYWORDS[t("راهنما")][0] == "help"
    assert KEYWORDS[t("كوله‌پشتی")][0] == "inv"          # حروف عربی هم شناخته می‌شوند


def test_button_colors():
    from app.bot.ui import style_for
    assert style_for("✅ تأیید") == "success" and style_for("❌ انصراف") == "danger"
    assert style_for("🎒 کوله‌پشتی") == "primary" and style_for("❌ لغو درخواست") == "danger"
    assert style_for("پذیرفتن قبیله") == "success" and style_for("⛔️ خاموش کردن") == "danger"


def test_party_describe_texts():
    from app.services import party
    m = {"kind": "hostage", "names": {"1": "علی", "2": "محمد", "3": "هدف"}, "tg": {"1": 11, "2": 22, "3": 33},
         "invites": {"2": "no", "3": "pending"}}
    c = {"state": "party", "mods": m, "attacker_user_id": 1, "defender_user_id": 3, "party": [1]}
    text, btns = party.describe(c)
    assert "منتظر تأیید" in text and "درخواست شما را رد کرد" in text
    assert {a for _, a in btns} == {"pj", "pn", "cancel"}
    c["state"], m["defend_min"] = "defend_prompt", 5
    c["party"] = [1, 2]
    text, btns = party.describe(c)
    assert "دفاع می‌کنید" in text and {a for _, a in btns} == {"defend", "surrender"}


def test_help_covers_all_sections():
    from app.bot.help_fa import COMMANDS, SECTIONS
    assert {"hostage", "raid", "mug", "protect", "inv", "forest"} <= set(SECTIONS)
    assert "گروگانگیری" in COMMANDS and "خفتگیری" in COMMANDS
