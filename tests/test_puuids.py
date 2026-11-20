"""本人與黑名單的 PUUID 依 Key 分開存（#10，ADR 0002）。

FakeRiot 模擬「PUUID 依 Key 加密」：同一個帳號在不同 Key 下拿到不同的 PUUID。
"""
import json

import pytest

from bp_auditor.audit import Audit, Me
from bp_auditor.config import Config, config_path
from bp_auditor.parsing import RiotId
from bp_auditor.puuids import backfill, lookup_me, spread
from bp_auditor.riot import RiotError
from tests.test_analysis import match

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


def test_舊設定檔的本人與黑名單PUUID_載入後歸到清單的1號Key():
    write_raw({"api_keys": [{"key": K1}, {"key": K2}], "my_riot_id": "我#TW2", "my_puuid": "me-1",
               "blacklist": ["bad-1", "bad-2"]})

    cfg = Config.load()

    assert cfg.my_puuid(K1) == "me-1"
    assert cfg.my_puuid(K2) == ""
    assert cfg.is_blacklisted(K1, "bad-1") and cfg.is_blacklisted(K1, "bad-2")
    assert not cfg.is_blacklisted(K2, "bad-1")


def test_更舊的單一Key設定檔_本人與黑名單一樣歸到1號():
    write_raw({"api_key": K1, "my_puuid": "me-1", "blacklist": ["bad-1"]})

    cfg = Config.load()
    cfg.save()
    cfg = Config.load()  # 存檔再載入仍一致

    assert cfg.my_puuid(K1) == "me-1"
    assert cfg.is_blacklisted(K1, "bad-1")


# ─── 假的 Riot：PUUID 依 Key 加密 ───


class FakeRiot:
    """帳號 → 目前的 Riot ID；某組 Key 下的 PUUID 是「Key 末四碼:帳號」，別組 Key 解不開（Riot 回 400）。"""

    def __init__(self, accounts: dict[str, tuple[str, str]]):
        self.accounts = dict(accounts)
        self.down: set[str] = set()  # 這些 Key 的請求都連線失敗

    def puuid(self, key, account):
        return f"{key[-4:]}:{account}"

    def client(self, key):
        return FakeClient(self, key)


class FakeClient:
    def __init__(self, riot, key):
        self.riot, self.key = riot, key

    def _check(self):
        if self.key in self.riot.down:
            raise RiotError("network", "連線失敗")

    def riot_id_of(self, puuid):
        self._check()
        prefix, _, account = puuid.partition(":")
        if prefix != self.key[-4:]:
            raise RiotError("bad_request", "Exception decrypting")
        if account not in self.riot.accounts:
            raise RiotError("not_found", "查無此人")  # 帳號已刪除
        return self.riot.accounts[account]

    def account_puuid(self, name, tag):
        self._check()
        account = next((a for a, rid in self.riot.accounts.items() if rid == (name, tag)), None)
        if account is None:
            raise RiotError("not_found", "查無此人")
        return self.riot.puuid(self.key, account)


def test_新增Key時_黑名單每一筆都換算出新Key的版本_查不到的那筆不中斷其他筆():
    riot = FakeRiot({"amy": ("Amy", "1"), "bob": ("Bob改名", "2")})  # bob 改過名；gone 帳號已刪除
    cfg = Config()
    cfg.api_keys.add(K1, now=0)
    for account in ("amy", "gone", "bob"):
        cfg.toggle_blacklist(K1, riot.puuid(K1, account))
    cfg.api_keys.add(K2, now=1)

    cfg.add_versions(backfill(cfg.blacklist.snapshot(), K2, [K1], riot.client))

    assert cfg.is_blacklisted(K2, riot.puuid(K2, "amy"))
    assert cfg.is_blacklisted(K2, riot.puuid(K2, "bob"))  # 用對方目前的 Riot ID 換算
    assert not cfg.is_blacklisted(K2, riot.puuid(K2, "gone"))
    assert cfg.is_blacklisted(K1, riot.puuid(K1, "gone"))  # 其他 Key 的版本照樣保留
    assert Config.load().is_blacklisted(K2, riot.puuid(K2, "amy"))  # 已存檔


def config_with_two_keys(riot):
    """K1、K2 兩組 Key，本人與黑名單（amy）在兩組 Key 下都有版本。"""
    cfg = Config()
    cfg.my_riot_id = "我#TW2"
    for i, k in enumerate((K1, K2)):
        cfg.api_keys.add(k, now=i)
    found, _ = lookup_me(RiotId("我", "TW2"), [K1, K2], riot.client)
    cfg.my_puuids.update(found)
    cfg.toggle_blacklist(K1, riot.puuid(K1, "amy"))
    cfg.add_versions(spread(K1, riot.puuid(K1, "amy"), [K2], riot.client))
    return cfg


WORLD = {"me": ("我", "TW2"), "amy": ("Amy", "1"), "bob": ("Bob", "2")}


def test_用KeyB的PUUID判斷本人與黑名單_結果正確():
    riot = FakeRiot(WORLD)
    cfg = config_with_two_keys(riot)

    assert cfg.my_puuid(K2) == riot.puuid(K2, "me")
    assert cfg.my_puuid(K1) == riot.puuid(K1, "me")
    assert cfg.is_blacklisted(K2, riot.puuid(K2, "amy"))
    assert not cfg.is_blacklisted(K2, riot.puuid(K1, "amy"))  # 別組 Key 的版本不算
    assert not cfg.is_blacklisted(K2, riot.puuid(K2, "bob"))


def test_刪除Key時_它那個版本從本人與黑名單中移除():
    riot = FakeRiot(WORLD)
    cfg = config_with_two_keys(riot)

    assert cfg.remove_key(K1)
    cfg.save()
    cfg = Config.load()

    assert cfg.my_puuid(K1) == ""
    assert not cfg.is_blacklisted(K1, riot.puuid(K1, "amy"))
    assert cfg.my_puuid(K2) == riot.puuid(K2, "me")
    assert cfg.is_blacklisted(K2, riot.puuid(K2, "amy"))
    assert all(K1 not in json.dumps(e) for e in cfg.blacklist)


def test_刪除Key後_只剩它那個版本的黑名單那筆一併移除():
    riot = FakeRiot(WORLD)
    cfg = config_with_two_keys(riot)
    cfg.toggle_blacklist(K1, riot.puuid(K1, "gone"))  # 帳號已刪除，K2 換算不到

    cfg.remove_key(K1)

    assert len(cfg.blacklist) == 1


def test_加入黑名單後_用每組Key各存一份_某組查不到不影響其他組():
    riot = FakeRiot(WORLD)
    cfg = Config()
    for i, k in enumerate((K1, K2, K3)):
        cfg.api_keys.add(k, now=i)
    bob = riot.puuid(K2, "bob")

    assert cfg.toggle_blacklist(K2, bob)
    riot.down.add(K1)
    cfg.add_versions(spread(K2, bob, [K1, K3], riot.client))

    assert cfg.is_blacklisted(K3, riot.puuid(K3, "bob"))
    assert not cfg.is_blacklisted(K1, riot.puuid(K1, "bob"))


def test_移出黑名單後才回來的換算結果_不會把人加回去():
    riot = FakeRiot(WORLD)
    cfg = Config()
    cfg.api_keys.add(K1, now=0); cfg.api_keys.add(K2, now=1)
    amy = riot.puuid(K1, "amy")
    cfg.toggle_blacklist(K1, amy)
    found = spread(K1, amy, [K2], riot.client)  # 背景換算中

    cfg.toggle_blacklist(K1, amy)  # 使用者又按一次，移出
    cfg.add_versions(found)

    assert not cfg.is_blacklisted(K2, riot.puuid(K2, "amy"))
    assert len(cfg.blacklist) == 0


def test_本人每組Key各查一次_某組查不到時回報錯誤但保留查到的():
    riot = FakeRiot(WORLD)
    riot.down.add(K2)

    found, error = lookup_me(RiotId("我", "TW2"), [K1, K2], riot.client)

    assert found == {K1: riot.puuid(K1, "me")}
    assert error.kind == "network"


def test_刪掉1號讓2號成為使用中的Key_相遇與黑名單仍然正確():
    """手動驗證項目的無介面版本：稽查用 #2 的假客戶端，本人與黑名單都取 #2 的版本。"""
    riot = FakeRiot(WORLD)
    cfg = config_with_two_keys(riot)
    cfg.remove_key(K1)
    key = cfg.api_keys.first_usable().key
    assert key == K2

    p = lambda account: riot.puuid(key, account)  # 這組 Key 抓到的對戰，裡面的 PUUID 也是這組 Key 的版本
    games = {f"m{i}": match(f"m{i}", [p("me"), p("amy")], [p("bob")]) for i in range(3)}

    class AuditClient(FakeClient):
        def match_ids(self, puuid, count):
            return list(games)[:count]

        def match(self, mid):
            return games[mid]

        def summoner_level(self, puuid):
            return 1

        def league_entries(self, puuid):
            return []

    seen = []
    Audit(AuditClient(riot, key), [RiotId("Amy", "1"), RiotId("Bob", "2")],
          Me(RiotId("我", "TW2"), cfg.my_puuid(key)), 3, on_player=seen.append).run()

    by = {str(r.riot_id): r for r in seen}
    assert by["Amy#1"].encounter.ally is True and by["Bob#2"].encounter.ally is False
    assert cfg.is_blacklisted(key, by["Amy#1"].puuid)
    assert not cfg.is_blacklisted(key, by["Bob#2"].puuid)
