import json

import pytest

from bp_auditor.config import Config, config_path


@pytest.fixture(autouse=True)
def temp_appdata(tmp_path, monkeypatch):
    """設定檔寫到暫存目錄，絕不碰真正的 %APPDATA%\\BPAuditor\\config.json。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert config_path().is_relative_to(tmp_path)


def write_config(data):
    p = config_path()
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def thresholds(cfg):
    return cfg.poke_deaths_per_minute, cfg.poke_kda_at_most


def test_default_poke_thresholds():
    assert thresholds(Config()) == (0.6, 1.5)


def test_poke_thresholds_survive_save_and_reload():
    cfg = Config()
    cfg.poke_deaths_per_minute, cfg.poke_kda_at_most = 0.65, 1.2
    cfg.save()
    assert thresholds(Config.load()) == (0.65, 1.2)


def test_old_config_without_thresholds_loads_defaults():
    write_config({"api_key": "k", "recent_games": 7})
    cfg = Config.load()
    assert (cfg.recent_games, *thresholds(cfg)) == (7, 0.6, 1.5)


def test_old_thresholds_are_not_migrated_and_dropped():
    # 假設大家都是第一次安裝（#24）：舊的送頭場、戳場門檻一律不沿用，照預設值
    p = write_config({"api_key": "k", "feeding_deaths_per_minute": 0.7, "poke_kd_at_most": 0.3})
    cfg = Config.load()
    assert thresholds(cfg) == (0.6, 1.5)
    cfg.save()
    saved = json.loads(p.read_text(encoding="utf-8"))
    assert "poke_kd_at_most" not in saved and "feeding_deaths_per_minute" not in saved


def test_old_config_with_only_the_three_old_thresholds_loads_default():
    p = write_config({"api_key": "k", "feeding_kda_below": 1.5, "feeding_kd_at_most": 0.3,
                      "feeding_min_deaths": 12})
    cfg = Config.load()
    assert ([k.key for k in cfg.api_keys], *thresholds(cfg)) == (["k"], 0.6, 1.5)
    cfg.save()
    assert "feeding_min_deaths" not in json.loads(p.read_text(encoding="utf-8"))  # 舊欄位直接捨棄
