"""一次稽查發幾次 Riot 請求（#13）：用假的傳輸層走真的 RiotClient，數 HTTP 請求。

Key 的額度是 100 次 / 120 秒，房間＋查對面約 80 次，多一次重複請求就少一分餘裕。
情境：5 人房間（含本人）、近 5 場；A、B 有 3 場同隊，C 與本人相遇過 1 場。進遊戲後對面 5 人，E1、E2 有 3 場同隊。
"""
from bp_auditor.audit import ME, Audit, Me
from bp_auditor.parsing import RiotId
from tests.fakes import FakeRiot

GAMES = 5
ROOM = ["我", "A", "B", "C", "D"]
ENEMIES = ["E1", "E2", "E3", "E4", "E5"]


KEY = "RGAPI-test"


def world() -> FakeRiot:
    """帳號就是名稱（Riot ID「名稱#TW」）；每位玩家近 5 場的對戰 id，同一個 id 的人在同一隊。"""
    ids = {n: [f"{n}-{i}" for i in range(GAMES)] for n in ROOM + ENEMIES}
    ids["B"][:3] = ids["A"][:3]
    ids["C"][2] = ids["我"][2]
    ids["E2"][:3] = ids["E1"][:3]
    riot = FakeRiot()
    for n, history in ids.items():
        riot.add_player(n, history=history)
    return riot


def rid(n):
    return RiotId(n, "TW")


def audit(riot, room, **kw):
    outcome = {}
    Audit(riot.client(KEY), room, Me(rid("我"), riot.puuid(KEY, "我")), GAMES, on_outcome=lambda o: outcome.update(o=o), **kw).execute()
    return outcome["o"]


def test_房間稽查_本人不重複查帳號與對戰id():
    riot = world()
    audit(riot, [rid(n) for n in ROOM])

    assert riot.duplicates() == {}
    # 5 人各：對戰 id＋牌位（10）；帳號：A–D（4），本人用 PUUID 查 Riot ID（1）；對戰詳情 21 場（重疊的只查一次）
    assert riot.total == 36


def test_房間多了新人_只查新人_本人不再查():
    riot = world()
    first = audit(riot, [rid(n) for n in ROOM[:4]])
    riot.clear()

    audit(riot, [rid(n) for n in ROOM], query=[rid("D")], prior=first.histories)

    # D：帳號、對戰 id、牌位、5 場對戰；本人的近期戰績已在 prior，不再查 Riot ID 與對戰 id
    assert riot.total == 8


def test_查對面_沿用我方_本人不再查():
    riot = world()
    prior = audit(riot, [rid(n) for n in ROOM]).histories
    assert ME in prior
    riot.clear()

    game = [rid(n) for n in ROOM + ENEMIES]
    audit(riot, game, query=[rid(n) for n in ENEMIES], prior=prior,
          puuids={str(r): riot.puuid(KEY, r.name) for r in game}, champions={str(r): 103 for r in game},
          sides=[[ME] + [str(rid(n)) for n in ROOM[1:]], [str(rid(n)) for n in ENEMIES]])

    assert riot.duplicates() == {}
    # 對面 5 人各：對戰 id＋牌位（10）；10 人的本場英雄熟練度（10）；對面對戰詳情 22 場
    assert riot.total == 42
