"""Key 生命週期（#29）與本人的 PUUID 依 Key 分開存（#10，ADR 0002）。不需要 QApplication。

共用的假 Riot（tests.fakes.FakeRiot）模擬「PUUID 依 Key 加密」：同一個帳號在不同 Key 下拿到不同的 PUUID。
介面該做的事（存檔、重畫、重驗、補查本人、跑排隊的稽查）是 KeyManager 回傳的動作；這裡的 drive 代替介面執行。
"""
import json

import pytest

from bp_auditor.audit import Audit, Me
from bp_auditor.config import Config, config_path
from bp_auditor.key_manager import (RECHECK_MS, Alert, KeyManager, LookUpMe, MeLookedUp, RedrawKeys, RequeueAudit,
                                    Recheck, RunPending, Save, ShowMe, lookup_me)
from bp_auditor.keys import BAD, OK, UNVERIFIED, WARMING, WARMUP
from bp_auditor.parsing import RiotId
from bp_auditor.riot import RiotError
from bp_auditor.rotation import KeyRotation
from tests.fakes import FakeRiot

K1 = "RGAPI-11111111-1111-1111-1111-111111111111"
K2 = "RGAPI-22222222-2222-2222-2222-222222222222"
K3 = "RGAPI-33333333-3333-3333-3333-333333333333"
NOW = 1_000_000.0
DENIED = "API Key 無法使用（HTTP 401）"


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

WORLD = {"me": ("我", "TW2"), "amy": ("Amy", "1"), "bob": ("Bob", "2")}
ME = RiotId("我", "TW2")


def world() -> FakeRiot:
    riot = FakeRiot()
    for account, (name, tag) in WORLD.items():
        riot.add_player(account, name, tag)
    return riot


def manager(riot: FakeRiot, *, me: str = "我#TW2") -> KeyManager:
    cfg = Config()
    cfg.my_riot_id = me
    return KeyManager(cfg, KeyRotation(lambda: cfg.api_keys, fetch=riot, limits=((10 ** 6, 1.0),)))


def drive(m: KeyManager, actions: list, riot: FakeRiot) -> list:
    """代替介面執行動作：補查本人照做（結果交回 KeyManager），其他動作原樣收集起來給測試檢查。"""
    done = []
    for a in actions:
        done.append(a)
        if isinstance(a, LookUpMe):
            found, error = lookup_me(a.riot_id, list(a.keys), riot.client)
            done += drive(m, m.me_looked_up(str(a.riot_id), found, error), riot)
    return done


def loaded(m: KeyManager, *keys: str):
    """之前就存在清單裡的 Key（不是剛產生的），而且已驗證可用。"""
    for k in keys:
        m.cfg.api_keys.add(k, now=0.0)
        m.cfg.api_keys.set_status(k, OK)


def config_with_two_keys(riot):
    """K1、K2 兩組 Key，本人在兩組 Key 下都有版本。"""
    m = manager(riot)
    loaded(m, K1, K2)
    drive(m, [LookUpMe(ME, (K1, K2))], riot)
    return m.cfg


def test_本人每組Key各存一份():
    riot = world()
    cfg = config_with_two_keys(riot)

    assert cfg.my_puuid(K2) == riot.puuid(K2, "me")
    assert cfg.my_puuid(K1) == riot.puuid(K1, "me")


def test_刪除Key時_本人與黑名單它那個版本一併移除():
    riot = world()
    m = manager(riot)
    loaded(m, K1, K2)
    drive(m, [LookUpMe(ME, (K1, K2))], riot)
    m.cfg.blacklist.toggle(K1, riot.puuid(K1, "bob"), "Bob#2")
    m.cfg.blacklist.check(K2, riot.puuid(K2, "bob"), "Bob#2")

    assert m.remove_key(K1) == [Save(), RedrawKeys(hide_banner=True)]
    m.cfg.save()
    cfg = Config.load()

    assert [k.key for k in cfg.api_keys] == [K2]
    assert cfg.my_puuid(K1) == ""
    assert cfg.my_puuid(K2) == riot.puuid(K2, "me")
    assert cfg.blacklist.to_json() == [{"riot_id": "Bob#2", "puuids": {K2: riot.puuid(K2, "bob")}}]  # 整筆保留
    assert m.remove_key(K1) == []  # 已經不在清單裡


def test_本人每組Key各查一次_某組查不到時回報錯誤但保留查到的():
    riot = world()
    riot.down.add(K2)

    found, error = lookup_me(ME, [K1, K2], riot.client)

    assert found == {K1: riot.puuid(K1, "me")}
    assert error.kind == "network"


def test_補查本人_都查不到時設定頁顯示錯誤():
    m = manager(world(), me="沒這人#TW")
    loaded(m, K1)
    assert m.me_looked_up("沒這人#TW", {}, RiotError("not_found", "查無此人")) == [MeLookedUp("找不到這個 Riot ID")]
    assert m.me_looked_up("沒這人#TW", {}, RiotError("network", "連線失敗")) == [MeLookedUp("連線失敗")]
    assert m.me_looked_up("別人#TW", {K1: "x"}, None) == []  # 查詢期間換了本人：結果不要
    assert m.cfg.my_puuids == {}


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


# ─── Key 生命週期 ───


def test_剛產生的Key回401_生效中_重驗成功後補本人PUUID並跑排隊的稽查():
    riot = world()
    m = manager(riot)

    assert m.add_key(K1, NOW) == [Save(), RedrawKeys(hide_banner=True), Recheck(K1)]
    assert m.add_key(K1, NOW) is None  # 已經在清單裡

    # Riot 還沒啟用這組 Key：生效中，15 秒後重驗；沒有其他可用的 Key，稽查先排隊
    actions = m.key_checked(K1, "key", DENIED, NOW + 10)
    assert m.cfg.api_keys.get(K1).status == WARMING
    assert actions == [Save(), RedrawKeys(hide_banner=True), Recheck(K1, RECHECK_MS)]
    assert m.warming() and m.active_key() == ""

    # 重驗成功：可用，補查本人在這組 Key 的 PUUID，排隊的稽查可以跑了
    actions = drive(m, m.key_checked(K1, "", "", NOW + 25), riot)
    assert m.cfg.api_keys.get(K1).status == OK
    assert not m.warming() and m.active_key() == K1
    assert LookUpMe(ME, (K1,)) in actions and RunPending() in actions and actions[-1] == Alert()
    assert m.cfg.my_puuid(K1) == riot.puuid(K1, "me")


def test_舊Key回401_失效_不重驗():
    m = manager(world())
    loaded(m, K1)

    actions = m.key_checked(K1, "key", DENIED, NOW)

    assert m.cfg.api_keys.get(K1).status == BAD
    assert actions == [Save(), RedrawKeys(), Alert()]
    assert m.active_key() == "" and not m.warming()


def test_驗證時連線出錯_未能驗證但照樣可用():
    riot = world()
    m = manager(riot)
    m.add_key(K1, NOW)

    actions = m.key_checked(K1, "network", "連線失敗", NOW + 1)

    assert m.cfg.api_keys.get(K1).status == UNVERIFIED and m.active_key() == K1
    assert RunPending() not in actions and LookUpMe(ME, (K1,)) in actions


def test_已經有本人版本的Key_驗證後不再補查():
    m = manager(world())
    m.add_key(K1, NOW)
    m.cfg.my_puuids[K1] = "me-1"
    assert not any(isinstance(a, LookUpMe) for a in m.key_checked(K1, "", "", NOW + 1))


def test_驗證期間或補查期間刪掉的Key_結果不存():
    riot = world()
    m = manager(riot)
    m.add_key(K1, NOW); m.add_key(K2, NOW)
    m.remove_key(K2)

    assert m.key_checked(K2, "", "", NOW + 1) == []
    assert m.cfg.api_keys.get(K2) is None

    found, _ = lookup_me(ME, [K1, K2], riot.client)
    assert m.me_looked_up("我#TW2", found, None) == [Save(), MeLookedUp("")]
    assert m.cfg.my_puuids == {K1: riot.puuid(K1, "me")}

    m.remove_key(K1)  # 稽查期間刪掉：查到的本人不存這組的版本
    m.me_found(Me(ME, riot.puuid(K1, "me")), K1)
    assert m.cfg.my_puuids == {}


def audit_job(riot):
    """一次假的稽查：用這組 Key 查一個人的 PUUID。"""
    return lambda k, client: (k, client.account_puuid("Amy", "1"))


def test_稽查中回401_輪替跳過它_主執行緒才標成失效():
    riot = world()
    m = manager(riot)
    loaded(m, K1, K2, K3)
    riot.fail(K1, 401, times=99)
    failed = []

    key, puuid = m.run(audit_job(riot), failed.append)  # 背景執行緒做的事

    assert (key, puuid) == (K2, riot.puuid(K2, "amy"))  # 整次稽查用 K2 重跑
    assert failed == [K1]
    assert m.cfg.api_keys.get(K1).status == OK  # 背景執行緒不改 Key 清單
    assert m.rotation.usable_count() == 2  # 但輪替已經跳過它

    actions = m.key_failed(K1, NOW, switched=True)  # 訊號回到主執行緒
    assert m.cfg.api_keys.get(K1).status == BAD
    assert actions == [Save(), RedrawKeys()]
    assert m.rotation.usable_count() == 2
    assert m.key_failed(K1, NOW, switched=True) == []  # 已經失效：不重複處理


def test_稽查中剛產生的Key回401_生效中_沒得換時Key生效後整房重查():
    riot = world()
    m = manager(riot)
    m.add_key(K1, NOW)

    actions = m.key_failed(K1, NOW + 5, switched=False)  # 只有一組：落在每個人的結果上

    assert m.cfg.api_keys.get(K1).status == WARMING
    assert actions == [RequeueAudit(), Save(), RedrawKeys(hide_banner=True), Recheck(K1, RECHECK_MS)]
    assert m.key_failed(K1, NOW + 5, switched=False) == []  # 其他人的結果再回報一次：不重複排程

    m.add_key(K2, NOW)  # 有別組可換時：稽查已經用別組重跑，不必重查
    assert RequeueAudit() not in m.key_failed(K2, NOW + 5, switched=True)
    assert m.cfg.api_keys.get(K2).status == WARMING


def test_過了生效期才回401_失效():
    m = manager(world())
    m.add_key(K1, NOW)
    assert m.key_failed(K1, NOW + WARMUP + 1, switched=False) == [Save(), RedrawKeys()]
    assert m.cfg.api_keys.get(K1).status == BAD


def test_稽查中查到本人改名_存新的Riot_ID並補查其他Key():
    riot = world()
    m = manager(riot, me="舊名#TW2")
    loaded(m, K1, K2)

    actions = drive(m, m.me_found(Me(ME, riot.puuid(K1, "me")), K1), riot)

    assert m.cfg.my_riot_id == "我#TW2"
    assert ShowMe("我#TW2", "你的 Riot ID 已更新為 我#TW2") in actions
    assert LookUpMe(ME, (K2,)) in actions
    assert m.cfg.my_puuids == {K1: riot.puuid(K1, "me"), K2: riot.puuid(K2, "me")}
    assert m.me_found(Me(ME, riot.puuid(K1, "me")), K1) == [Save(), ShowMe("我#TW2", "")]  # 沒改名、都有版本


def test_換了本人_清掉舊的PUUID_每組可用的Key重查():
    riot = world()
    m = manager(riot, me="別人#TW")
    loaded(m, K1, K2)
    m.cfg.my_puuids = {K1: "old-1", K2: "old-2"}

    assert m.set_me("我#TW2") == [Save(), LookUpMe(ME, (K1, K2))]
    assert m.cfg.my_puuids == {}
    assert m.set_me("") == [Save()]


def test_指定這次稽查用的Key():
    m = manager(world())
    loaded(m, K1, K2)
    assert m.use() == K1
    assert m.use(K2) == K2 and m.active_key() == K2
