import json

import pytest

from bp_auditor.config import Config, config_path


@pytest.fixture(autouse=True)
def temp_appdata(tmp_path, monkeypatch):
    """設定檔寫到暫存目錄，絕不碰真正的 %APPDATA%\\BPAuditor\\config.json。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert config_path().is_relative_to(tmp_path)


def test_feeding_thresholds_survive_save_and_reload():
    cfg = Config()
    cfg.feeding_kda_below, cfg.feeding_kd_at_most, cfg.feeding_min_deaths = 1.5, 0.3, 12
    cfg.save()
    loaded = Config.load()
    assert (loaded.feeding_kda_below, loaded.feeding_kd_at_most, loaded.feeding_min_deaths) == (1.5, 0.3, 12)


def test_old_config_without_feeding_thresholds_loads_defaults():
    p = config_path()
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"api_key": "k", "recent_games": 7}), encoding="utf-8")
    cfg = Config.load()
    assert (cfg.recent_games, cfg.feeding_kda_below, cfg.feeding_kd_at_most, cfg.feeding_min_deaths) == (7, 2.0, 0.5, 10)
