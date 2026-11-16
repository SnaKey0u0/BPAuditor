from bp_auditor.analysis import (FeedingThresholds, detect_premades, find_encounter, game_summaries, mastery, ranks,
                                 recent_stats, split_game)


def match(mid, team100, team200, *, win100=True, champ="Ahri", kda=(2, 1, 3), queue=450, mode="ARAM",
          remake=False, surrender=False):
    """team100/team200: puuid 清單。remake=True：重開局（提早投降）；surrender=True：一般投降。"""
    ps = []
    for team, members, win in ((100, team100, win100), (200, team200, not win100)):
        for pu in members:
            ps.append(dict(puuid=pu, teamId=team, win=win, championName=champ, summonerLevel=100,
                           kills=kda[0], deaths=kda[1], assists=kda[2],
                           gameEndedInEarlySurrender=remake, gameEndedInSurrender=surrender))
    return {"metadata": {"matchId": mid}, "info": {"participants": ps, "queueId": queue, "gameMode": mode}}


def test_recent_stats():
    ms = [match("1", ["a"], ["x"], win100=True), match("2", ["x"], ["a"], win100=True)]
    s = recent_stats("a", ms)
    assert (s.games, s.wins, s.results, s.level) == (2, 1, [True, False], 100)
    assert s.kda == 5.0
    assert s.kd == 2.0


def _stats_tuple(s):
    return (s.games, s.wins, s.win_rate, s.kda, s.kd, s.results)


def test_recent_stats_exclude_remake_same_as_without_it():
    normal = [match("1", ["a"], ["x"], kda=(4, 2, 6)), match("3", ["x"], ["a"], kda=(1, 3, 2))]
    remake = match("2", ["a"], ["x"], win100=True, kda=(9, 0, 9), remake=True)
    with_remake = recent_stats("a", [normal[0], remake, normal[1]])
    assert _stats_tuple(with_remake) == _stats_tuple(recent_stats("a", normal))
    assert (with_remake.games, with_remake.wins) == (2, 1)


def test_recent_stats_all_remakes_same_as_no_games():
    ms = [match("1", ["a"], ["x"], remake=True), match("2", ["x"], ["a"], remake=True)]
    s = recent_stats("a", ms)
    assert _stats_tuple(s) == _stats_tuple(recent_stats("a", []))
    assert (s.games, s.win_rate) == (0, 0.0)


def test_game_summaries_newest_first_with_result_champion_and_kda():
    ms = [match("1", ["a"], ["x"], win100=True, champ="Ahri", kda=(7, 2, 9)),
          match("2", ["x"], ["a"], win100=True, champ="Garen", kda=(1, 12, 4))]
    gs = game_summaries("a", ms)
    assert [(g.win, g.champion, g.kills, g.deaths, g.assists) for g in gs] == [
        (True, "Ahri", 7, 2, 9), (False, "Garen", 1, 12, 4)]


def test_game_summaries_skip_matches_without_the_player():
    ms = [match("1", ["x"], ["y"]), match("2", ["a"], ["x"], champ="Lux"), match("3", [], ["x"])]
    assert [g.champion for g in game_summaries("a", ms)] == ["Lux"]
    assert game_summaries("a", []) == []


def test_game_summaries_mode_from_queue_falling_back_to_game_mode():
    ms = [match("1", ["a"], [], queue=450), match("2", ["a"], [], queue=420),
          match("3", ["a"], [], queue=9999, mode="NEXUSBLITZ")]
    assert [g.mode for g in game_summaries("a", ms)] == ["ARAM", "單雙", "NEXUSBLITZ"]


def test_game_summaries_flag_remake_but_not_normal_surrender():
    ms = [match("1", ["a"], ["x"], remake=True), match("2", ["a"], ["x"], surrender=True), match("3", ["a"], ["x"])]
    assert [g.remake for g in game_summaries("a", ms)] == [True, False, False]


def _feeding(kda, remake=False, **thresholds):
    """單場 K/D/A → 該場是否為送頭場（門檻未給就用預設 2／0.5／10）。"""
    ms = [match("1", ["a"], ["x"], kda=kda, remake=remake)]
    return game_summaries("a", ms, FeedingThresholds(**thresholds)).pop().feeding


def test_feeding_game_when_only_kda_condition_met():
    assert _feeding((6, 10, 12)) is True  # KDA 1.8 < 2，KD 0.6 > 0.5


def test_feeding_game_when_only_kd_condition_met():
    assert _feeding((4, 10, 30)) is True  # KDA 3.4 ≥ 2，KD 0.4 ≤ 0.5


def test_feeding_game_when_both_conditions_met():
    assert _feeding((1, 12, 2)) is True


def test_not_feeding_game_when_deaths_below_threshold():
    assert _feeding((0, 9, 0)) is False


def test_feeding_game_kd_equal_to_threshold_counts():
    assert _feeding((5, 10, 20)) is True  # KDA 2.5，KD 剛好 0.5


def test_not_feeding_game_when_kda_equal_to_threshold():
    assert _feeding((6, 10, 14)) is False  # KDA 剛好 2.0，KD 0.6


def test_feeding_game_zero_deaths_counted_as_one():
    assert _feeding((0, 0, 1), min_deaths=0) is True  # KDA 1.0、KD 0.0，不會除以零
    assert _feeding((3, 0, 3), min_deaths=0) is False  # KDA 6、KD 3


def test_custom_feeding_thresholds_change_the_judgement():
    assert _feeding((1, 12, 2), min_deaths=15) is False
    assert _feeding((3, 5, 2), min_deaths=5) is True
    assert _feeding((6, 10, 14), kda_below=2.5) is True
    assert _feeding((5, 10, 20), kd_at_most=0.4) is False


def test_remake_never_a_feeding_game():
    assert _feeding((0, 12, 0), remake=True) is False


def test_game_summaries_default_thresholds_and_per_game_kda_kd():
    g = game_summaries("a", [match("1", ["a"], ["x"], kda=(3, 12, 5))])[0]
    assert (g.feeding, g.kda, g.kd) == (True, 8 / 12, 3 / 12)
    assert game_summaries("a", [match("1", ["a"], ["x"], kda=(3, 0, 5))])[0].kda == 8.0


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
