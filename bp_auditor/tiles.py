"""近期戰績的英雄方塊：排法與單場提示框的時長／死亡速率（不碰 Qt，方便測試）。"""
from bp_auditor.analysis import GameSummary

GROUP = 5           # 幾場一組
GROUPS_PER_ROW = 2  # 一列最多幾組


def grid(n: int, width: int, *, tile: int, gap: int, group_gap: int, margin: int) -> list[tuple[int, int]]:
    """n 個方塊在 width 寬度內的左上角座標，由新到舊。

    每 GROUP 場一組，組內間隔 gap、組間間隔 group_gap；放得下就並排（最多 GROUPS_PER_ROW 組），
    放不下就整組換列。窄到連一組都放不下時，才改成一般的逐個換列（每列至少 1 個）。
    """
    step = tile + gap
    room = width - 2 * margin

    def first_row(k: int) -> int:
        """一列放 k 組時第一列的寬度；最後一組不滿時只算它實際的方塊數。"""
        last = min(GROUP, n - (k - 1) * GROUP)
        return (k - 1) * (group_w + group_gap) + last * step - gap

    group_w = GROUP * step - gap
    groups = next((k for k in range(min(GROUPS_PER_ROW, -(-n // GROUP)), 0, -1) if first_row(k) <= room), 0)
    if groups >= 1:
        return [(margin + (i // GROUP % groups) * (group_w + group_gap) + i % GROUP * step,
                 margin + i // (GROUP * groups) * step) for i in range(n)]
    cols = max(1, (room + gap) // step)
    return [(margin + i % cols * step, margin + i // cols * step) for i in range(n)]


def clock(minutes: float) -> str:
    """分鐘數 → m:ss（超過一小時仍以分鐘計，例：65:30）。"""
    total = round(minutes * 60)
    return f"{total // 60}:{total % 60:02d}"


def stats_line(g: GameSummary) -> str:
    """單場提示框的一行：整局時長與死亡速率。每一場都顯示（含重開局），不另外標記送頭場。"""
    return f"時長 {clock(g.minutes)} · {g.death_rate:.2f} 死/分"
