"""本人的 PUUID 依 Key 分開存（#10，ADR 0002）。

共用的假 Riot（tests.fakes.FakeRiot）模擬「PUUID 依 Key 加密」：同一個帳號在不同 Key 下拿到不同的 PUUID。
"""
import json

import pytest

from bp_auditor.audit import Audit, Me
from bp_auditor.config import Config, config_path
from bp_auditor.parsing import RiotId
from bp_auditor.puuids import lookup_me
from tests.fakes import FakeRiot

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


def test_舊設定檔的本人PUUID_載入後歸到清單的1號Key():
    write_raw({"api_keys": [{"key": K1}, {"key": K2}], "my_riot_id": "我#TW2", "my_puuid": "me-1"})

    cfg = Config.load()

    assert cfg.my_puuid(K1) == "me-1"
    assert cfg.my_puuid(K2) == ""


def test_更舊的單一Key設定檔_本人一樣歸到1號():
    write_raw({"api_key": K1, "my_puuid": "me-1"})

    cfg = Config.load()
    cfg.save()
    cfg = Config.load()  # 存檔再載入仍一致

    assert cfg.my_puuid(K1) == "me-1"


# ─── 假的 Riot（tests.fakes）：PUUID 依 Key 加密 ───


def world() -> FakeRiot:
    riot = FakeRiot()
    for account, (name, tag) in WORLD.items():
        riot.add_player(account, name, tag)
    return riot


def config_with_two_keys(riot):
    """K1、K2 兩組 Key，本人在兩組 Key 下都有版本。"""
    cfg = Config()
    cfg.my_riot_id = "我#TW2"
    for i, k in enumerate((K1, K2)):
        cfg.api_keys.add(k, now=i)
    found, _ = lookup_me(RiotId("我", "TW2"), [K1, K2], riot.client)
    cfg.my_puuids.update(found)
    return cfg


WORLD = {"me": ("我", "TW2"), "amy": ("Amy", "1"), "bob": ("Bob", "2")}


def test_本人每組Key各存一份():
    riot = world()
    cfg = config_with_two_keys(riot)

    assert cfg.my_puuid(K2) == riot.puuid(K2, "me")
    assert cfg.my_puuid(K1) == riot.puuid(K1, "me")


def test_刪除Key時_本人它那個版本一併移除():
    riot = world()
    cfg = config_with_two_keys(riot)

    assert cfg.remove_key(K1)
    cfg.save()
    cfg = Config.load()

    assert cfg.my_puuid(K1) == ""
    assert cfg.my_puuid(K2) == riot.puuid(K2, "me")


def test_本人每組Key各查一次_某組查不到時回報錯誤但保留查到的():
    riot = world()
    riot.down.add(K2)

    found, error = lookup_me(RiotId("我", "TW2"), [K1, K2], riot.client)

    assert found == {K1: riot.puuid(K1, "me")}
    assert error.kind == "network"


def test_刪掉1號讓2號成為使用中的Key_相遇仍然正確():
    """手動驗證項目的無介面版本：稽查用 #2 的假客戶端，本人取 #2 的版本。"""
    riot = world()
    cfg = config_with_two_keys(riot)
    cfg.remove_key(K1)
    key = cfg.api_keys.first_usable().key
    assert key == K2

    for i in range(3):  # 這組 Key 抓到的對戰，裡面的 PUUID 也是這組 Key 的版本
        riot.add_match(f"m{i}", ["me", "amy"], ["bob"])

    seen = []
    Audit(riot.client(key), [RiotId("Amy", "1"), RiotId("Bob", "2")],
          Me(RiotId("我", "TW2"), cfg.my_puuid(key)), 3, on_player=seen.append).run()

    by = {str(r.riot_id): r for r in seen}
    assert by["Amy#1"].encounter.ally is True and by["Bob#2"].encounter.ally is False
