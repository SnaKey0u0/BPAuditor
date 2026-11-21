"""玩家卡片的顯示模型（card_view）：英雄方塊排法、單場提示框，以及文案、色調與標籤顯示條件；不需要 QApplication。"""
import pytest

from bp_auditor import card_view as cv
from bp_auditor.analysis import Encounter, GameSummary, Mastery, Rank, recent_stats
from bp_auditor.audit import PlayerResult
from bp_auditor.card_view import grid, stats_line
from bp_auditor.champions import Champions
from bp_auditor.parsing import RiotId
from bp_auditor.riot import RiotError
from tests.test_analysis import match

T, GAP, GGAP, M = 32, 6, 16, 6  # 方塊邊長、組內間隔、組間間隔、邊距
STEP = T + GAP
GROUP_W = 5 * T + 4 * GAP


def cells(n, width):
    return grid(n, width, tile=T, gap=GAP, group_gap=GGAP, margin=M)


def wide_enough(groups):
    return 2 * M + groups * GROUP_W + (groups - 1) * GGAP


def test_two_groups_side_by_side_with_wider_gap():
    xs = [x for x, _ in cells(10, wide_enough(2))]
    assert xs[:5] == [M + i * STEP for i in range(5)]
    assert xs[5] == M + GROUP_W + GGAP  # 第二組從組間間隔之後開始
    assert {y for _, y in cells(10, wide_enough(2))} == {M}


def test_whole_group_wraps_when_two_do_not_fit():
    pos = cells(10, wide_enough(2) - 1)
    assert [x for x, _ in pos[5:]] == [x for x, _ in pos[:5]]  # 第二組整組換列，不拆開
    assert {y for _, y in pos[5:]} == {M + STEP}


def test_at_most_two_groups_per_row():
    pos = cells(20, 10_000)
    assert {y for _, y in pos[:10]} == {M}
    assert {y for _, y in pos[10:]} == {M + STEP}


def test_partial_last_group():
    pos = cells(7, wide_enough(1))
    assert len(pos) == 7
    assert pos[5] == (M, M + STEP) and pos[6] == (M + STEP, M + STEP)


def test_partial_group_fits_beside_full_group_using_its_own_width():
    width = 2 * M + GROUP_W + GGAP + 2 * T + GAP  # 一組滿的 + 只有 2 個的第二組，剛好放得下
    assert {y for _, y in cells(7, width)} == {M}
    assert {y for _, y in cells(7, width - 1)} == {M, M + STEP}


def test_falls_back_to_plain_wrap_when_one_group_does_not_fit():
    pos = cells(5, 2 * M + 3 * T + 2 * GAP)  # 只放得下 3 個
    assert [p[1] for p in pos] == [M, M, M, M + STEP, M + STEP]
    assert pos[3][0] == M


def test_at_least_one_per_row():
    assert [y for _, y in cells(2, 1)] == [M, M + STEP]


def g(**kw):
    base = dict(win=True, champion="Ahri", kills=1, deaths=5, assists=2, mode="ARAM", minutes=18 + 32 / 60)
    return GameSummary(**base | kw)


def test_stats_line_shows_length_and_death_rate():
    assert stats_line(g()) == "時長 18:32 · 0.27 死/分 · KDA 0.60"


def test_stats_line_for_remake_is_shown_as_is():
    assert stats_line(g(remake=True, deaths=1, minutes=3 + 12 / 60)) == "時長 3:12 · 0.31 死/分 · KDA 3.00"


def test_stats_line_long_game_and_zero_length():
    assert stats_line(g(minutes=65.5)) == "時長 65:30 · 0.08 死/分 · KDA 0.60"
    assert stats_line(g(minutes=0, kills=3, deaths=0)) == "時長 0:00 · 0.00 死/分 · KDA 5.00"  # 0 死以 1 計


# ─── 卡片顯示模型：文案、色調與標籤顯示條件 ───

CHAMPS = Champions({"Ahri": "阿璃", "Zed": "劫"}, key_to_id={103: "Ahri"})


def m(i, win=True, kda=(2, 1, 3), minutes=30, remake=False):
    return match(str(i), ["a"], ["x"], win100=win, kda=kda, minutes=minutes, remake=remake)


def result(matches=(), **kw):
    matches = list(matches)
    return PlayerResult(RiotId("甲", "TW"), "a", matches, recent_stats("a", matches), **({"ranks": {}} | kw))


def view(r, rate=0.6, kda=2.0):
    return cv.card_view(r, CHAMPS, rate, kda)


def test_poke_tag_shown_when_any_game_is_poke_with_thresholds_in_tip():
    v = view(result([m(1), m(2, False, kda=(0, 12, 3), minutes=15)]))
    assert v.poke == cv.Tag("戳", tip="每分鐘死亡 ≥ 0.60 次，且 KDA ≤ 2.00")
    assert [g.poke for g in v.games] == [False, True]


def test_poke_tag_hidden_without_poke_and_follows_thresholds():
    games = [m(1, False, kda=(0, 12, 3), minutes=15)]
    assert view(result([m(1)])).poke is None
    assert view(result(games), rate=1.0).poke is None  # 0.8 死/分 未達門檻
    assert view(result(games), rate=0.5, kda=0.1).poke is None  # KDA 0.25 超過門檻
    assert view(result(games), rate=0.5, kda=0.25).poke.tip == "每分鐘死亡 ≥ 0.50 次，且 KDA ≤ 0.25"


def test_poke_tag_never_for_remake():
    assert view(result([m(1, False, kda=(0, 9, 0), minutes=3, remake=True)])).poke is None


def test_streak_text_and_tone():
    assert view(result([m(i) for i in range(4)] + [m(9, False)])).streak == cv.Tag("連勝中(4)", cv.GOOD)
    assert view(result([m(i, False) for i in range(3)])).streak == cv.Tag("連敗中(3)", cv.BAD)


def test_streak_needs_threshold_and_skips_remakes():
    assert view(result([m(1), m(2), m(3, False)])).streak is None  # 只連 2 場
    assert view(result([m(1), m(2, remake=True, minutes=3), m(3), m(4)])).streak == cv.Tag("連勝中(3)", cv.GOOD)


@pytest.mark.parametrize("ago, ally, text, tone", [
    (0, True, "上一場的隊友 · 阿璃 3/4/5", cv.GOOD),
    (3, False, "4 場前的敵人 · 阿璃 3/4/5", cv.BAD),
    (1, True, "2 場前的隊友 · 阿璃 3/4/5", cv.GOOD),
])
def test_encounter_text(ago, ally, text, tone):
    v = view(result(encounter=Encounter(ago, ally, "Ahri", 3, 4, 5)))
    assert v.encounter == cv.EncounterTag("Ahri", text, tone)


def test_encounter_unknown_champion_keeps_id_and_none_without_encounter():
    assert view(result(encounter=Encounter(0, False, "Nobody", 1, 0, 2))).encounter.text == "上一場的敵人 · Nobody 1/0/2"
    assert view(result()).encounter is None


@pytest.mark.parametrize("kind, text", [("not_found", "查無此人"), ("network", "連線失敗"), ("server", "伺服器錯誤"),
                                        ("blocked", "連線被擋"), ("bad_request", "查詢失敗")])
def test_error_short_text_with_detail_in_tip(kind, text):
    v = view(PlayerResult(RiotId("甲", "TW"), error=RiotError(kind, "原文")))
    assert v.headline == cv.Headline(text, cv.BAD, tip="原文")
    assert (v.level, v.rank, v.ratios, v.games, v.streak, v.poke, v.encounter) == ("", None, (), [], None, None, None)


def test_key_error_shows_only_a_dash():
    v = view(PlayerResult(RiotId("甲", "TW"), error=RiotError("key", "401")))
    assert v.headline == cv.Headline("—", cv.MUTED)


def test_rank_text_with_unranked_queue_and_tip():
    v = view(result(ranks={"單雙": Rank("GOLD", "II", 45, 30, 20)}))
    assert v.rank.queues == (cv.QueueRank("單雙", "金 II 45LP", True), cv.QueueRank("彈性", "未定級", False))
    assert v.rank.tip == "單雙 30勝20敗（60%）"


def test_rank_all_unranked_and_not_found():
    assert view(result(ranks={})).rank == cv.RankView((cv.QueueRank("單雙", "未定級", False),
                                                       cv.QueueRank("彈性", "未定級", False)))
    assert view(result(ranks=None)).rank == cv.RankView(note="牌位查不到")


def test_rank_tip_without_games_has_no_percentage():
    v = view(result(ranks={"單雙": Rank("MASTER", "I", 120, 0, 0), "彈性": Rank("IRON", "IV", 0, 1, 3)}))
    assert [q.text for q in v.rank.queues] == ["大師 120LP", "鐵 IV 0LP"]
    assert v.rank.tip == "單雙 0勝0敗\n彈性 1勝3敗（25%）"


def test_headline_win_rate_record_and_level():
    v = view(result([m(1), m(2), m(3), m(4, False)]))
    assert v.headline == cv.Headline("75%", cv.GOOD, record="3勝1敗")
    assert v.level == "Lv 100"


def test_no_games_and_unknown_level():
    v = view(PlayerResult(RiotId("甲", "TW"), "a", [], recent_stats("a", []), ranks={}))
    assert v.headline == cv.Headline("近期沒有對戰", cv.MUTED)
    assert (v.ratios, v.level) == ((), "Lv ?")


def test_all_remakes_still_list_tiles():
    v = view(result([m(1, remake=True, minutes=3)]))
    assert v.headline.text == "近期沒有對戰" and len(v.games) == 1


@pytest.mark.parametrize("rate, tone", [(0.39, cv.BAD), (0.4, cv.NEUTRAL), (0.6, cv.NEUTRAL), (0.61, cv.GOOD)])
def test_win_rate_tone(rate, tone):
    assert cv.win_rate_tone(rate) == tone


def test_kda_and_kd_tone_and_text():
    good = view(result([m(1, kda=(1, 1, 2))]))  # KDA 3.00、KD 1.00 剛好達標
    assert good.ratios == (cv.Ratio("KDA", "3.00", cv.GOOD), cv.Ratio("KD", "1.00", cv.GOOD))
    bad = view(result([m(1, kda=(2, 3, 6))]))  # KDA 2.67、KD 0.67
    assert bad.ratios == (cv.Ratio("KDA", "2.67", cv.BAD), cv.Ratio("KD", "0.67", cv.BAD))


def test_champion_line_mastery_and_states():
    now = 1_000_000
    line = cv.champion_line(103, Mastery(7, 123456, now - 3 * 86400), loaded=True, hidden=False, champions=CHAMPS, now=now)
    assert line == cv.ChampionLine("Ahri", "阿璃", ("熟練 7 級", "12.3 萬點"), "3 天前玩過")
    assert cv.champion_line(103, None, loaded=False, hidden=False, champions=CHAMPS).note == "查詢中…"
    assert cv.champion_line(103, None, loaded=True, hidden=False, champions=CHAMPS).note == "沒玩過（或查不到）"
    assert cv.champion_line(103, None, loaded=True, hidden=True, champions=CHAMPS) == cv.ChampionLine("Ahri", "阿璃")
    unknown = cv.champion_line(1, None, loaded=True, hidden=False, champions=CHAMPS)  # 英雄資料還沒載入
    assert (unknown.champion, unknown.name) == (None, "本場英雄")


def test_ago_and_points():
    assert [cv.ago(0, now) for now in (59 * 60, 5 * 3600, 2 * 86400)] == ["剛剛", "5 小時前", "2 天前"]
    assert (cv.points(900), cv.points(20000)) == ("900 點", "2.0 萬點")


def test_game_tip_fields():
    t = cv.game_tip(g(win=False, champion="Ahri", items=[1001], keystone=8112, spells=[4]), CHAMPS)
    assert (t.score, t.result, t.result_tone, t.name, t.mode, t.note) == ("1 / 5 / 2", "敗", cv.BAD, "阿璃", "ARAM", "")
    assert [i.file for i in (*t.items, t.rune, *t.spells)] == ["item/1001.png", "rune/8112.png", "spell/4.png"]
    assert t.stats == stats_line(g())
    remake = cv.game_tip(g(remake=True), CHAMPS)
    assert (remake.result_tone, remake.rune, remake.note) == (cv.GOOD, None, "重開局（不計入統計）")
