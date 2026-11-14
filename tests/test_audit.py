from bp_auditor.audit import ME, Audit, Me
from bp_auditor.parsing import RiotId
from bp_auditor.riot import RiotError
from tests.test_analysis import match

# 4 場對戰：A、B 與本人（puuid "me"）一直同隊，C 是對手
MATCHES = {f"m{i}": match(f"m{i}", ["a", "b", "me"], ["c"]) for i in range(4)}
ACCOUNTS = {("A", "1"): "a", ("B", "1"): "b", ("C", "1"): "c", ("新名字", "TW2"): "me"}


class FakeClient:
    def __init__(self):
        self.calls = []

    def account_puuid(self, name, tag):
        self.calls.append(("account", name))
        return ACCOUNTS[(name, tag)]

    def riot_id_of(self, puuid):
        self.calls.append(("riot_id_of", puuid))
        rid = next((k for k, v in ACCOUNTS.items() if v == puuid), None)
        if rid is None:  # 別把 Key 加密出來的 PUUID，Riot 回 400 Exception decrypting
            raise RiotError("bad_request", "Exception decrypting")
        return rid

    def match_ids(self, puuid, count):
        return list(MATCHES)[:count]

    def match(self, mid):
        return MATCHES[mid]

    def summoner_level(self, puuid):
        return 1


def run(room, me, **kw):
    seen = {"players": [], "me": None, "outcome": None}
    Audit(FakeClient(), room, me, 4, on_player=seen["players"].append,
          on_me=lambda m: seen.__setitem__("me", m), on_outcome=lambda o: seen.__setitem__("outcome", o), **kw).run()
    return seen


def test_renamed_me_is_detected_by_puuid_and_joins_premade_as_you():
    a, b, renamed_me = RiotId("A", "1"), RiotId("B", "1"), RiotId("新名字", "TW2")
    seen = run([a, b, renamed_me], Me(RiotId("舊名字", "TW2"), "me"))
    assert seen["me"] == Me(renamed_me, "me")
    assert [p.is_me for p in seen["players"] if p.riot_id == renamed_me] == [True]
    assert seen["outcome"].premades[0].members == ["A#1", "B#1", ME]


def test_puuid_is_filled_in_when_only_riot_id_known():
    seen = run([RiotId("C", "1")], Me(RiotId("新名字", "TW2")))
    assert seen["me"].puuid == "me"
    assert seen["players"][0].encounter.ally is False


def test_grow_only_queries_new_member_and_reuses_prior():
    a, b = RiotId("A", "1"), RiotId("B", "1")
    first = run([a], Me())
    seen = run([a, b], Me(), query=[b], prior=first["outcome"].histories)
    assert [p.riot_id for p in seen["players"]] == [b]
    assert seen["outcome"].premades[0].members == ["A#1", "B#1"]


def test_puuid_from_another_key_falls_back_to_riot_id():
    """PUUID 依 Key 所屬產品加密：換成 Personal Key 後，舊 PUUID 解不開，要改用 Riot ID 重查並存下新 PUUID。"""
    me = RiotId("新名字", "TW2")
    seen = run([RiotId("A", "1"), me], Me(me, "old-key-puuid"))
    assert seen["me"] == Me(me, "me")
    assert [p.is_me for p in seen["players"] if p.riot_id == me] == [True]
