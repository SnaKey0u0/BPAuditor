import json

import pytest

from bp_auditor.config import Config, config_path
from bp_auditor.keys import BAD, CHECKING, OK, UNVERIFIED, WARMING, KeyPool, mask

K1 = "RGAPI-11111111-1111-1111-1111-111111111111"
K2 = "RGAPI-22222222-2222-2222-2222-222222222222"
K3 = "RGAPI-33333333-3333-3333-3333-333333333333"


@pytest.fixture(autouse=True)
def temp_appdata(tmp_path, monkeypatch):
    """設定檔寫到暫存目錄，絕不碰真正的 %APPDATA%\\BPAuditor\\config.json。"""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert config_path().is_relative_to(tmp_path)


def write_raw(data: dict):
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")


def read_raw() -> dict:
    return json.loads(config_path().read_text(encoding="utf-8"))


def test_舊設定檔的單一Key_載入後成為只有一組的清單_存檔再載入仍一致():
    write_raw({"api_key": K1, "key_saved_at": 1000.0, "personal_key": True, "recent_games": 7})

    cfg = Config.load()
    assert [k.key for k in cfg.api_keys] == [K1]
    assert cfg.recent_games == 7

    cfg.save()
    raw = read_raw()
    assert not {"api_key", "key_saved_at", "personal_key"} & raw.keys()  # 舊欄位捨棄
    assert [k.key for k in Config.load().api_keys] == [K1]


def test_舊設定檔的Key是空字串_載入為空清單():
    write_raw({"api_key": "", "recent_games": 7})

    assert len(Config.load().api_keys) == 0


def test_新增重複的Key被拒絕_清單不變():
    cfg = Config()
    assert cfg.api_keys.add(K1, now=1.0) is not None
    assert cfg.api_keys.add(K2, now=2.0) is not None

    assert cfg.api_keys.add(f"  {K1}\n", now=3.0) is None  # 前後空白不算不同的 Key
    assert [k.key for k in cfg.api_keys] == [K1, K2]


def test_刪除Key後_清單與存檔都正確():
    cfg = Config()
    for i, k in enumerate((K1, K2, K3)):
        cfg.api_keys.add(k, now=float(i))
    cfg.save()

    assert cfg.api_keys.remove(K2)
    cfg.save()

    assert [k.key for k in cfg.api_keys] == [K1, K3]
    assert [d["key"] for d in read_raw()["api_keys"]] == [K1, K3]
    assert [k.key for k in Config.load().api_keys] == [K1, K3]
    assert not cfg.api_keys.remove(K2)  # 已經不在清單裡


def pool_of(*keys):
    pool = KeyPool()
    for i, k in enumerate(keys):
        pool.add(k, now=float(i))
    return pool


def test_第一組可用的Key_會跳過失效的Key():
    pool = pool_of(K1, K2, K3)
    pool.set_status(K1, BAD)

    assert pool.first_usable().key == K2


def test_第一組可用的Key_會跳過還在生效中的新Key_全部不能用時回None():
    pool = pool_of(K1, K2)
    pool.set_status(K1, WARMING)
    assert pool.first_usable().key == K2

    pool.set_status(K2, BAD)
    assert pool.first_usable() is None
    assert KeyPool().first_usable() is None


def test_驗證中的Key仍可使用_與改版前只有一組Key時相同():
    pool = pool_of(K1)  # 剛加入，還在驗證中

    assert pool.get(K1).status == CHECKING
    assert pool.first_usable().key == K1


def test_失效的Key存檔再載入仍是失效_其他Key載入後為可用():
    cfg = Config()
    cfg.api_keys = pool_of(K1, K2, K3)
    cfg.api_keys.set_status(K1, BAD)
    cfg.api_keys.set_status(K2, WARMING)  # 關程式時還在驗證／生效中：下次啟動不會自動重驗，當作可用
    cfg.save()

    loaded = Config.load().api_keys
    assert [k.status for k in loaded] == [BAD, OK, OK]
    assert loaded.first_usable().key == K2


def test_舊設定檔轉換來的Key載入後為可用():
    write_raw({"api_key": K1})

    assert Config.load().api_keys.get(K1).status == OK


def test_摘要_沒有Key時顯示未設定():
    s = KeyPool().summary()
    assert (s.text, s.tone) == ("未設定 Key", "error")
    assert not KeyPool().all_bad  # 沒有 Key 不算「全部失效」，不顯示重新產生的橫幅


def test_摘要_顯示幾組可用():
    pool = pool_of(K1, K2, K3)
    for k in (K1, K2):
        pool.set_status(k, OK)
    pool.set_status(K3, BAD)

    assert (pool.summary().text, pool.summary().tone) == ("2 / 3 組可用", "ok")
    assert not pool.all_bad


def test_摘要_全部失效時顯示錯誤():
    pool = pool_of(K1, K2)
    pool.set_status(K1, BAD)
    pool.set_status(K2, BAD)

    assert pool.all_bad
    assert pool.summary().tone == "error"
    assert "重新產生" in pool.summary().text


def test_摘要_驗證中的Key也算可用_另外標驗證中():
    pool = pool_of(K1, K2)
    pool.set_status(K2, OK)
    assert (pool.summary().text, pool.summary().tone) == ("2 / 2 組可用・驗證中…", "busy")

    pool.set_status(K2, WARMING)
    assert (pool.summary().text, pool.summary().tone) == ("1 / 2 組可用・驗證中…", "busy")


def test_摘要_沒有可用的Key但有新Key生效中():
    pool = pool_of(K1)
    pool.set_status(K1, WARMING)
    assert (pool.summary().text, pool.summary().tone) == ("新 Key 生效中…", "busy")


def test_未能驗證的Key仍可用_摘要算進可用數_存檔後下次啟動當作可用():
    cfg = Config(api_keys=pool_of(K1, K2))
    cfg.api_keys.set_status(K1, UNVERIFIED)
    cfg.api_keys.set_status(K2, OK)

    assert cfg.api_keys.get(K1).usable
    assert cfg.api_keys.first_usable().key == K1
    assert (cfg.api_keys.summary().text, cfg.api_keys.summary().tone) == ("2 / 2 組可用", "ok")
    cfg.save()
    assert Config.load().api_keys.get(K1).status == OK


def test_Key清單的snapshot是複本_之後新增刪除不影響():
    pool = pool_of(K1, K2)
    snap = pool.snapshot()
    pool.add(K3, 0.0); pool.remove(K1)

    assert [k.key for k in snap] == [K1, K2]
    assert [k.key for k in pool] == [K2, K3]


def test_遮罩過的Key只露出開頭與結尾():
    assert mask("RGAPI-1a2b3c4d-0000-0000-0000-000000009f0e") == "RGAPI-1a2b…9f0e"
