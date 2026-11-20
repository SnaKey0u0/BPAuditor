import json

import pytest

from bp_auditor.config import Config, config_path

K = "RGAPI-11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def temp_appdata(tmp_path, monkeypatch):
    """設定檔一律寫到暫存資料夾，絕不碰真正的 %APPDATA%\\BPAuditor\\config.json。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


def test_黑名單_切換一次加入_切換兩次回到原狀():
    cfg = Config()
    assert not cfg.is_blacklisted(K, "puuid-A")

    cfg.toggle_blacklist(K, "puuid-A")
    assert cfg.is_blacklisted(K, "puuid-A")

    cfg.toggle_blacklist(K, "puuid-A")
    assert not cfg.is_blacklisted(K, "puuid-A")


def test_黑名單_切換後立即存檔_重新載入仍在():
    Config().toggle_blacklist(K, "puuid-A")

    assert Config.load().is_blacklisted(K, "puuid-A")


def test_舊設定檔沒有黑名單欄位_載入為空名單():
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"api_key": "k", "recent_games": 7}), encoding="utf-8")

    cfg = Config.load()

    assert cfg.recent_games == 7
    assert len(cfg.blacklist) == 0
    assert not cfg.is_blacklisted(K, "puuid-A")
