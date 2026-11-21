import json

import pytest

from bp_auditor.config import Config, config_path

K = "RGAPI-11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def temp_appdata(tmp_path, monkeypatch):
    """設定檔一律寫到暫存資料夾，絕不碰真正的 %APPDATA%\\BPAuditor\\config.json。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


def test_舊設定檔有舊格式黑名單_其他設定照常載入_舊黑名單捨棄():
    """v1.4.2 的黑名單只有 PUUID，不搬移（#25：假設大家都是第一次安裝）。"""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"api_keys": [{"key": K}], "recent_games": 7, "my_puuids": {K: "me-1"},
                                "blacklist": [{K: "bad-1"}, "bad-2"]}), encoding="utf-8")

    cfg = Config.load()
    cfg.save()

    assert cfg.recent_games == 7 and cfg.my_puuid(K) == "me-1"
    assert json.loads(path.read_text(encoding="utf-8"))["blacklist"] == []
