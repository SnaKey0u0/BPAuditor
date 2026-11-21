from bp_auditor.analysis import GameSummary
from bp_auditor.tiles import grid, stats_line

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
    assert stats_line(g()) == "時長 18:32 · 0.27 死/分"


def test_stats_line_for_remake_is_shown_as_is():
    assert stats_line(g(remake=True, deaths=1, minutes=3 + 12 / 60)) == "時長 3:12 · 0.31 死/分"


def test_stats_line_long_game_and_zero_length():
    assert stats_line(g(minutes=65.5)) == "時長 65:30 · 0.08 死/分"
    assert stats_line(g(minutes=0)) == "時長 0:00 · 0.00 死/分"
