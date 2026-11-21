import json

import pytest

from bp_auditor.config import Config, config_path


@pytest.fixture(autouse=True)
def temp_appdata(tmp_path, monkeypatch):
    """設定檔寫到暫存目錄，絕不碰真正的 %APPDATA%\\BPAuditor\\config.json。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert config_path().is_relative_to(tmp_path)


def test_feeding_threshold_survives_save_and_reload():
    cfg = Config()
    cfg.feeding_deaths_per_minute = 0.65
    cfg.save()
    assert Config.load().feeding_deaths_per_minute == 0.65


def test_old_config_without_feeding_threshold_loads_default():
    p = config_path()
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"api_key": "k", "recent_games": 7}), encoding="utf-8")
    cfg = Config.load()
    assert (cfg.recent_games, cfg.feeding_deaths_per_minute) == (7, 0.6)


def write_config(data):
    p = config_path()
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps(data), encoding="utf-8")


def test_default_feeding_threshold_is_0_6():
    assert Config().feeding_deaths_per_minute == 0.6


def test_stored_old_default_0_5_migrates_to_new_default():
    write_config({"api_key": "k", "feeding_deaths_per_minute": 0.5})
    assert Config.load().feeding_deaths_per_minute == 0.6


def test_stored_custom_threshold_is_kept():
    write_config({"api_key": "k", "feeding_deaths_per_minute": 0.55})
    assert Config.load().feeding_deaths_per_minute == 0.55


def test_old_config_with_only_the_three_old_thresholds_loads_default():
    p = config_path()
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"api_key": "k", "feeding_kda_below": 1.5, "feeding_kd_at_most": 0.3,
                             "feeding_min_deaths": 12}), encoding="utf-8")
    cfg = Config.load()
    assert ([k.key for k in cfg.api_keys], cfg.feeding_deaths_per_minute) == (["k"], 0.6)
    cfg.save()
    assert "feeding_min_deaths" not in json.loads(p.read_text(encoding="utf-8"))  # 舊欄位直接捨棄
