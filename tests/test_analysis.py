from bp_auditor.analysis import detect_premades, find_encounter, mastery, ranks, recent_stats, split_game


def match(mid, team100, team200, *, win100=True, champ="Ahri", kda=(2, 1, 3)):
    """team100/team200: puuid 清單。"""
    ps = []
    for team, members, win in ((100, team100, win100), (200, team200, not win100)):
        for pu in members:
            ps.append(dict(puuid=pu, teamId=team, win=win, championName=champ, summonerLevel=100,
                           kills=kda[0], deaths=kda[1], assists=kda[2]))
    return {"metadata": {"matchId": mid}, "info": {"participants": ps}}


def test_recent_stats():
    ms = [match("1", ["a"], ["x"], win100=True), match("2", ["x"], ["a"], win100=True)]
    s = recent_stats("a", ms)
    assert (s.games, s.wins, s.results, s.level) == (2, 1, [True, False], 100)
    assert s.kda == 5.0


def test_premade_streak_from_most_recent():
    # A、B 最近 2 場同隊，第 3 場分開 → 連續 2 場
    ms = [match("1", ["a", "b"], []), match("2", [], ["a", "b"]), match("3", ["a"], ["b"])]
    groups = detect_premades({"A": ("a", ms), "B": ("b", ms), "C": ("c", [])})
    assert [(g.members, g.streak, g.total) for g in groups] == [(["A", "B"], 2, 2)]


def test_premade_by_total_without_streak():
    ms = [match("1", ["a"], ["b"]), match("2", ["a", "b"], []), match("3", ["a", "b"], []),
          match("4", ["a"], []), match("5", ["a", "b"], [])]
    groups = detect_premades({"A": ("a", ms), "B": ("b", [])})
    assert [(g.streak, g.total) for g in groups] == [(0, 3)]


def test_not_premade_when_rarely_together():
    ms = [match("1", ["a"], ["b"]), match("2", ["a", "b"], []), match("3", ["a"], [])]
    assert detect_premades({"A": ("a", ms), "B": ("b", ms)}) == []


def test_three_stack_reported_as_one_group():
    ms = [match(str(i), ["a", "b", "c"], []) for i in range(3)]
    groups = detect_premades({"A": ("a", ms), "B": ("b", ms), "C": ("c", ms)})
    assert [(g.members, g.streak) for g in groups] == [(["A", "B", "C"], 3)]


def test_find_encounter_most_recent_and_side():
    member_ms = [match("m9", ["x"], ["me"]), match("m5", ["x", "me"], [])]
    e = find_encounter("me", ["m1", "m5", "m9"], "x", member_ms)
    assert (e.games_ago, e.ally) == (1, True)
    assert find_encounter("me", ["m1"], "x", member_ms) is None


def _histories(games, players):
    """games: [(match_id, [同隊的人])]，新 → 舊；每位玩家只拿到自己有出場的對戰。"""
    ms = [match(mid, [p.lower() for p in team], ["stranger"]) for mid, team in games]
    return {p: (p.lower(), [m for m, (_, team) in zip(ms, games) if p in team]) for p in players}


def test_duo_and_trio_reported_as_two_groups():
    g = [(f"x{i}", ["A", "B"]) for i in range(3)] + [(f"y{i}", ["C", "D", "E"]) for i in range(3)]
    groups = detect_premades(_histories(g, "ABCDE"))
    assert [g.members for g in groups] == [["C", "D", "E"], ["A", "B"]]


def test_rotating_trios_merged_into_one_group_with_max_pair_stats():
    # 四人輪流三排，四人同場只有最舊的 1 場 → 仍合併成一組，場數取兩兩配對的最大值
    g = [("g1", ["A", "B", "C"]), ("g2", ["A", "B", "D"]), ("g3", ["A", "C", "D"]),
         ("g4", ["B", "C", "D"]), ("g5", ["A", "B", "C", "D"])]
    groups = detect_premades(_histories(g, "ABCDE"))
    assert [(g.members, g.streak, g.total) for g in groups] == [(["A", "B", "C", "D"], 2, 3)]


def test_groups_bridged_by_one_pair_merged():
    g = ([(f"x{i}", ["A", "B"]) for i in range(3)] + [(f"y{i}", ["C", "D"]) for i in range(3)]
         + [(f"q{i}", ["B", "C"]) for i in range(3)])
    groups = detect_premades(_histories(g, "ABCDE"))
    assert [g.members for g in groups] == [["A", "B", "C", "D"]]


def test_ranks_by_queue_and_format():
    rs = ranks([{"queueType": "RANKED_SOLO_5x5", "tier": "GOLD", "rank": "II", "leaguePoints": 45, "wins": 10, "losses": 8},
                {"queueType": "RANKED_FLEX_SR", "tier": "MASTER", "rank": "I", "leaguePoints": 120},
                {"queueType": "CHERRY", "tier": "GOLD", "rank": "I"}])
    assert {q: str(r) for q, r in rs.items()} == {"單雙": "金 II 45LP", "彈性": "大師 120LP"}
    assert ranks([]) == {}


def test_mastery_none_when_never_played():
    assert mastery(None) is None
    m = mastery({"championLevel": 7, "championPoints": 123456, "lastPlayTime": 1_700_000_000_000})
    assert (m.level, m.points, m.last_play) == (7, 123456, 1_700_000_000)


def test_split_game_keeps_hidden_players_and_skips_bots():
    game = {"participants": [
        {"puuid": "me", "teamId": 100, "riotId": "我#TW2", "championId": 103},
        {"puuid": "a", "teamId": 100, "riotId": "A#1", "championId": 1},
        {"puuid": "x", "teamId": 200, "riotId": "X#1", "championId": 2},
        {"puuid": None, "teamId": 100, "riotId": "Soraka", "championId": 16, "bot": False},  # 身分被隱藏
        {"puuid": "", "teamId": 200, "riotId": "", "championId": 3, "bot": True}]}
    allies, enemies = split_game(game, "me")
    assert [(p.label, p.puuid, p.champion_key) for p in allies] == [("我#TW2", "me", 103), ("A#1", "a", 1),
                                                                      ("Soraka", "", 16)]
    assert [p.label for p in enemies] == ["X#1"]
