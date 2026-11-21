from bp_auditor.analysis import (STREAK_MIN, detect_premades, find_encounter, game_summaries,
                                 game_puuids, mastery, ranks, recent_stats, split_game, streak)


def match(mid, team100, team200, *, win100=True, champ="Ahri", kda=(2, 1, 3), queue=450, mode="ARAM",
          remake=False, surrender=False, minutes=30):
    """team100/team200: puuid 清單。remake=True：重開局（提早投降）；surrender=True：一般投降。
    minutes：整局時長（match-v5 的 gameDuration 以秒為單位）。"""
    ps = []
    for team, members, win in ((100, team100, win100), (200, team200, not win100)):
        for pu in members:
            ps.append(dict(puuid=pu, teamId=team, win=win, championName=champ, summonerLevel=100,
                           kills=kda[0], deaths=kda[1], assists=kda[2],
                           gameEndedInEarlySurrender=remake, gameEndedInSurrender=surrender))
    return {"metadata": {"matchId": mid}, "info": {"participants": ps, "queueId": queue, "gameMode": mode,
                                                   "gameDuration": round(minutes * 60), "gameEndTimestamp": 1}}


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


def test_game_summary_items_in_slot_order_skipping_empty_slots():
    m = match("1", ["a"], [])
    m["info"]["participants"][0].update(item0=3089, item1=0, item2=3020, item3=0, item4=0, item5=3157, item6=3364)
    assert game_summaries("a", [m])[0].items == [3089, 3020, 3157, 3364]


def test_game_summary_keystone_and_summoner_spells():
    m = match("1", ["a"], [])
    m["info"]["participants"][0].update(summoner1Id=32, summoner2Id=4, perks={"styles": [
        {"description": "primaryStyle", "style": 8100, "selections": [{"perk": 8128}, {"perk": 8139}]},
        {"description": "subStyle", "style": 8000, "selections": [{"perk": 8014}]}]})
    g = game_summaries("a", [m])[0]
    assert (g.keystone, g.spells) == (8128, [32, 4])


def test_game_summary_without_perks_or_spells():
    # 競技場等模式可能沒有符文；舊資料可能缺欄位
    m = match("1", ["a"], [])
    m["info"]["participants"][0].update(perks={"styles": []})
    g = game_summaries("a", [m, match("2", ["a"], [])])
    assert [(x.items, x.keystone, x.spells) for x in g] == [([], None, []), ([], None, [])]


def test_game_summaries_flag_remake_but_not_normal_surrender():
    ms = [match("1", ["a"], ["x"], remake=True), match("2", ["a"], ["x"], surrender=True), match("3", ["a"], ["x"])]
    assert [g.remake for g in game_summaries("a", ms)] == [True, False, False]


def _poke(kills, deaths, assists, minutes=20, *, remake=False, **thresholds):
    """單場 K/D/A 與整局時長（分鐘）→ 該場是否為戳場（門檻未給就用預設：每分鐘死亡 ≥ 0.6、KDA ≤ 1.5）。"""
    ms = [match("1", ["a"], ["x"], kda=(kills, deaths, assists), remake=remake, minutes=minutes)]
    return game_summaries("a", ms, **thresholds).pop().poke


def test_poke_game_when_both_equal_thresholds():
    assert _poke(6, 12, 12) is True  # 每分鐘 0.6、KDA 1.5，兩個都剛好等於預設門檻


def test_not_poke_when_death_rate_below_threshold():
    assert _poke(0, 11, 0) is False  # KDA 0，但每分鐘只死 0.55 次


def test_not_poke_when_kda_above_threshold():
    assert _poke(6, 12, 13) is False  # 每分鐘 0.6，但 KDA 1.58


def test_same_deaths_only_shorter_game_reaches_death_rate():
    assert _poke(0, 10, 0, 15) is True   # 每分鐘 0.67
    assert _poke(0, 10, 0, 25) is False  # 每分鐘 0.4


def test_support_with_many_assists_not_poke():
    assert _poke(1, 13, 40) is False  # 每分鐘 0.65，但助攻多，KDA 3.15


def test_no_death_never_a_poke_game():
    assert _poke(0, 0, 0, poke_rate=0) is False  # 死亡速率門檻設成 0 也一樣：沒死過就不算


def test_remake_never_a_poke_game():
    assert _poke(0, 3, 0, 3, remake=True) is False  # 每分鐘 1 次、KDA 0，但是重開局


def test_custom_poke_thresholds():
    assert _poke(2, 10, 5) is False                            # 每分鐘 0.5、KDA 0.7：死亡速率未達預設
    assert _poke(2, 10, 5, poke_rate=0.5) is True
    assert _poke(2, 10, 5, poke_rate=0.5, poke_kda=0.6) is False


def test_game_summary_minutes_and_death_rate_from_whole_game_duration():
    m = match("1", ["a"], ["x"], kda=(2, 12, 4), minutes=20)
    m["info"]["participants"][0]["timePlayed"] = 300  # 該玩家自己的在線時間不採用
    g = game_summaries("a", [m])[0]
    assert (g.minutes, g.death_rate, g.poke) == (20, 0.6, True)


def test_pre_11_20_game_duration_in_milliseconds():
    m = match("1", ["a"], ["x"], kda=(0, 12, 0))
    m["info"]["gameDuration"] = 20 * 60_000
    del m["info"]["gameEndTimestamp"]
    g = game_summaries("a", [m])[0]
    assert (g.minutes, g.poke) == (20, True)


def test_missing_game_duration_never_poke():
    m = match("1", ["a"], ["x"], kda=(0, 10, 0))
    del m["info"]["gameDuration"]
    assert game_summaries("a", [m])[0].poke is False


def _games(*results):
    """results：新 → 舊，'W' 贏、'L' 輸、'R' 重開局 → 玩家 a 的單場摘要。"""
    return game_summaries("a", [match(str(i), ["a"], ["x"], win100=r == "W", remake=r == "R")
                                for i, r in enumerate(results)])


def test_streak_of_2_not_reported_3_reported_with_count_and_direction():
    assert STREAK_MIN == 3
    assert streak(_games("W", "W", "L", "W")) is None
    assert streak(_games("L", "L", "W")) is None
    s = streak(_games("W", "W", "W", "L", "W"))
    assert (s.win, s.games) == (True, 3)
    s = streak(_games("L", "L", "L", "L", "W"))
    assert (s.win, s.games) == (False, 4)


def test_streak_remake_in_losing_streak_neither_breaks_nor_counts():
    s = streak(_games("L", "R", "L", "L", "W"))
    assert (s.win, s.games) == (False, 3)
    s = streak(_games("R", "L", "L", "R", "W"))
    assert s is None


def test_streak_all_wins_counts_every_game_and_none_without_games():
    s = streak(_games("W", "R", "W", "W", "W", "W"))
    assert (s.win, s.games) == (True, 5)
    assert streak([]) is None
    assert streak(_games("R", "R", "R")) is None


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


def test_game_puuids_maps_room_labels_to_this_keys_puuids():
    """換 Key 重跑查對面：用新 Key 重查一次對局，把房間標籤（沿用房間裡的大小寫）對到新 Key 的 PUUID。"""
    from bp_auditor.parsing import RiotId
    game = {"participants": [
        {"puuid": "k2-me", "teamId": 100, "riotId": "我#TW2"},
        {"puuid": "k2-a", "teamId": 100, "riotId": "a#1"},
        {"puuid": None, "teamId": 200, "riotId": "Soraka"}]}
    room = [RiotId("我", "TW2"), RiotId("A", "1"), RiotId("離開了", "1")]
    assert game_puuids(game, room) == {"我#TW2": "k2-me", "A#1": "k2-a"}
    assert game_puuids(None, room) == {}
