from bp_auditor.audit import ME, Audit, Me
from bp_auditor.parsing import RiotId
from tests.fakes import FakeRiot, puuid

KEY = "RGAPI-test"
GOLD = {"queueType": "RANKED_SOLO_5x5", "tier": "GOLD", "rank": "I", "leaguePoints": 10}


def p(account):
    """這次稽查所用 Key（KEY）下該帳號的 PUUID。"""
    return puuid(KEY, account)


def world() -> FakeRiot:
    """4 場對戰：A、B 與本人（帳號 "me"，現在叫 新名字#TW2）一直同隊，C 是對手；C 沒玩過任何英雄。"""
    riot = FakeRiot()
    for account, name, tag in (("a", "A", "1"), ("b", "B", "1"), ("c", "C", "1"), ("me", "新名字", "TW2")):
        riot.add_player(account, name, tag)
    for i in range(4):
        riot.add_match(f"m{i}", ["a", "b", "me"], ["c"])
    riot.default_ranks = [GOLD]
    riot.mastery["c"] = None
    return riot


def run(room, me, riot=None, **kw):
    seen = {"players": [], "me": None, "outcome": None, "mastery": {}}
    Audit((riot or world()).client(KEY), room, me, 4, on_player=seen["players"].append,
          on_me=lambda m: seen.__setitem__("me", m), on_outcome=lambda o: seen.__setitem__("outcome", o),
          on_mastery=seen["mastery"].__setitem__, **kw).run()
    return seen


def test_renamed_me_is_detected_by_puuid_and_joins_premade_as_you():
    a, b, renamed_me = RiotId("A", "1"), RiotId("B", "1"), RiotId("新名字", "TW2")
    seen = run([a, b, renamed_me], Me(RiotId("舊名字", "TW2"), p("me")))
    assert seen["me"] == Me(renamed_me, p("me"))
    assert [p.is_me for p in seen["players"] if p.riot_id == renamed_me] == [True]
    assert seen["outcome"].premades[0].members == ["A#1", "B#1", ME]


def test_puuid_is_filled_in_when_only_riot_id_known():
    seen = run([RiotId("C", "1")], Me(RiotId("新名字", "TW2")))
    assert seen["me"].puuid == p("me")
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
    seen = run([RiotId("A", "1"), me], Me(me, puuid("RGAPI-old", "me")))
    assert seen["me"] == Me(me, p("me"))
    assert [p.is_me for p in seen["players"] if p.riot_id == me] == [True]


def test_player_gets_rank():
    seen = run([RiotId("A", "1")], Me())
    assert str(seen["players"][0].ranks["單雙"]) == "金 I 10LP"


def test_game_audit_uses_known_puuids_splits_sides_and_fills_prior_mastery():
    """進遊戲：A 已在房間階段查過（prior），只補熟練度；B、C 用觀戰 API 給的 PUUID，不查帳號。"""
    a, b, c = RiotId("A", "1"), RiotId("B", "1"), RiotId("C", "1")
    prior = run([a], Me())["outcome"].histories
    riot = world()
    seen = run([a, b, c], Me(), riot=riot, query=[b, c], prior=prior,
               puuids={"A#1": p("a"), "B#1": p("b"), "C#1": p("c")}, champions={"A#1": 1, "B#1": 2, "C#1": 3},
               sides=[["A#1", "B#1"], ["C#1"]])
    assert riot.count("/by-riot-id/") == 0
    assert seen["mastery"]["A#1"].level == 5
    assert {str(p.riot_id): p.mastery for p in seen["players"]}["C#1"] is None
    assert [g.members for g in seen["outcome"].premades] == [["A#1", "B#1"]]  # A、B 與 C 分屬兩隊，不併組


def test_game_reused_me_card_gets_mastery_by_known_puuid():
    """本人在房間階段已查過（存成「你」），進遊戲只補熟練度。"""
    me = RiotId("新名字", "TW2")
    prior = run([RiotId("A", "1"), me], Me(me, p("me")))["outcome"].histories
    assert ME in prior
    seen = run([RiotId("A", "1"), me], Me(me, p("me")), query=[], prior=prior,
               puuids={"A#1": p("a"), "新名字#TW2": p("me")}, champions={"A#1": 1, "新名字#TW2": 103})
    assert set(seen["mastery"]) == {"A#1", "新名字#TW2"}
    assert seen["outcome"].premades[0].members == ["A#1", ME]
