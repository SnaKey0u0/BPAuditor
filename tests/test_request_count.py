"""一次稽查發幾次 Riot 請求（#13）：用假的傳輸層走真的 RiotClient，數 HTTP 請求。

Key 的額度是 100 次 / 120 秒，房間＋查對面約 80 次，多一次重複請求就少一分餘裕。
情境：5 人房間（含本人）、近 5 場；A、B 有 3 場同隊，C 與本人相遇過 1 場。進遊戲後對面 5 人，E1、E2 有 3 場同隊。
"""
import json
import re
from collections import Counter

from bp_auditor.audit import ME, Audit, Me
from bp_auditor.parsing import RiotId
from bp_auditor.riot import RateLimiter, RiotClient

GAMES = 5
ROOM = ["我", "A", "B", "C", "D"]
ENEMIES = ["E1", "E2", "E3", "E4", "E5"]


def world():
    ids = {n: [f"{n}-{i}" for i in range(GAMES)] for n in ROOM + ENEMIES}
    ids["B"][:3] = ids["A"][:3]
    ids["C"][2] = ids["我"][2]
    ids["E2"][:3] = ids["E1"][:3]
    return ids


class FakeRiot:
    """PUUID 是「p-名稱」；每位玩家的對戰 id 由 world() 決定。urls 記下每一個請求。"""

    def __init__(self):
        self.ids, self.urls = world(), Counter()

    def fetch(self, url, key, timeout):
        self.urls[url] += 1
        path = re.sub(r"^https://[^/]+/", "", url)
        if "/by-riot-id/" in path:
            return self._ok({"puuid": "p-" + _unquote(path.split("/")[-2])})
        if "/accounts/by-puuid/" in path:
            return self._ok({"gameName": path.split("/")[-1][2:], "tagLine": "TW"})
        if path.endswith(f"/ids?start=0&count={GAMES}"):
            return self._ok(self.ids[path.split("/by-puuid/")[1].split("/")[0][2:]])
        if "/matches/" in path:
            mid = path.split("/")[-1]
            ps = [{"puuid": "p-" + n, "teamId": 100, "win": True, "kills": 1, "deaths": 1, "assists": 1,
                   "championName": "Ahri", "summonerLevel": 30} for n, ms in self.ids.items() if mid in ms]
            return self._ok({"metadata": {"matchId": mid}, "info": {"participants": ps, "queueId": 450,
                                                                    "gameDuration": 900, "gameEndTimestamp": 1}})
        if "/league/" in path:
            return self._ok([])
        if "/champion-masteries/" in path:
            return self._ok({"championLevel": 5, "championPoints": 1000, "lastPlayTime": 0})
        raise AssertionError(f"沒預期的請求：{url}")

    @staticmethod
    def _ok(data):
        return 200, {}, json.dumps(data).encode()

    @property
    def total(self):
        return sum(self.urls.values())

    def duplicates(self):
        return {u: n for u, n in self.urls.items() if n > 1}


def _unquote(s):
    from urllib.parse import unquote
    return unquote(s)


def rid(n):
    return RiotId(n, "TW")


def audit(riot, room, **kw):
    outcome = {}
    client = RiotClient("RGAPI-test", RateLimiter(limits=((10 ** 6, 1.0),)), fetch=riot.fetch)
    Audit(client, room, Me(rid("我"), "p-我"), GAMES, on_outcome=lambda o: outcome.update(o=o), **kw).execute()
    return outcome["o"]


def test_房間稽查_本人不重複查帳號與對戰id():
    riot = FakeRiot()
    audit(riot, [rid(n) for n in ROOM])

    assert riot.duplicates() == {}
    # 5 人各：對戰 id＋牌位（10）；帳號：A–D（4），本人用 PUUID 查 Riot ID（1）；對戰詳情 21 場（重疊的只查一次）
    assert riot.total == 36


def test_房間多了新人_只查新人_本人不再查():
    riot = FakeRiot()
    first = audit(riot, [rid(n) for n in ROOM[:4]])
    riot.urls.clear()

    audit(riot, [rid(n) for n in ROOM], query=[rid("D")], prior=first.histories)

    # D：帳號、對戰 id、牌位、5 場對戰；本人的近期戰績已在 prior，不再查 Riot ID 與對戰 id
    assert riot.total == 8


def test_查對面_沿用我方_本人不再查():
    riot = FakeRiot()
    prior = audit(riot, [rid(n) for n in ROOM]).histories
    assert ME in prior
    riot.urls.clear()

    game = [rid(n) for n in ROOM + ENEMIES]
    audit(riot, game, query=[rid(n) for n in ENEMIES], prior=prior,
          puuids={str(r): "p-" + r.name for r in game}, champions={str(r): 103 for r in game},
          sides=[[ME] + [str(rid(n)) for n in ROOM[1:]], [str(rid(n)) for n in ENEMIES]])

    assert riot.duplicates() == {}
    # 對面 5 人各：對戰 id＋牌位（10）；10 人的本場英雄熟練度（10）；對面對戰詳情 22 場
    assert riot.total == 42
