"""تست‌های منطق خالص (بدون دیتابیس و تلگرام)."""
import pytest

from app.config import ConfigError, load_config, parse_ids
from app.services.accounts import normalize_name, validate_name
from app.services.jobs import backoff_seconds
from app.services.settings import DEFAULTS, SKILLS, SettingsError, check_value


def test_parse_ids():
    assert parse_ids("1, 2,3") == frozenset({1, 2, 3})
    assert parse_ids("") == frozenset()


def test_config_requires_values():
    with pytest.raises(ConfigError):
        load_config({})
    with pytest.raises(ConfigError):
        load_config({"BOT_TOKEN": "x", "DATABASE_URL": "y"})  # بدون آدرس وب‌هوک


def test_config_ok_and_webhook_url():
    cfg = load_config({"BOT_TOKEN": "123:abc", "DATABASE_URL": "postgresql://u@h/d",
                       "RENDER_EXTERNAL_URL": "https://x.onrender.com/", "ADMIN_IDS": "5"})
    assert cfg.webhook_url.startswith("https://x.onrender.com/tg/")
    assert "123:abc" not in cfg.webhook_url
    assert cfg.admin_ids == frozenset({5})


def test_name_validation():
    assert validate_name("علی", 3, 16) is None
    assert validate_name("ab", 3, 16) is not None
    assert validate_name("x" * 17, 3, 16) is not None
    assert validate_name("12345", 3, 16) is not None
    assert validate_name("a<b>c", 3, 16) is not None
    assert normalize_name("  علی    رضا ") == "علی رضا"


def test_settings_defaults_are_well_formed():
    for key, (value, desc) in DEFAULTS.items():
        assert desc
        check_value(key, value)
    assert len(SKILLS) == 10


def test_settings_type_check():
    check_value("economy.start_coins", 10)
    with pytest.raises(SettingsError):
        check_value("economy.start_coins", "10")
    with pytest.raises(SettingsError):
        check_value("economy.start_coins", True)
    with pytest.raises(SettingsError):
        check_value("no.such.key", 1)


def test_backoff_grows_and_caps():
    assert backoff_seconds(1) == 30
    assert backoff_seconds(2) == 60
    assert backoff_seconds(50) == 3600


def test_texts():
    from app.bot.texts_fa import bar, fa_num
    assert fa_num(2026) == "۲۰۲۶"
    assert bar(50) == "▓▓▓▓▓░░░░░"
    assert bar(-5) == "░" * 10
    assert bar(999) == "▓" * 10


def test_callback_data_fits_telegram_limit():
    pytest.importorskip("aiogram")
    from app.bot.ui import Act
    assert len(Act(u=9_999_999_999, a="tbdi", x="d", y="i" + "0" * 36).pack().encode()) <= 64
    assert len(Act(u=9_999_999_999, a="hgi", x="123456", y="0" * 36).pack().encode()) <= 64
