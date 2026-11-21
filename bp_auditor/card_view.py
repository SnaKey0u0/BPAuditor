"""玩家卡片的顯示模型（不碰 Qt，方便測試）：把稽查結果轉成卡片上要顯示的文案、色調與提示框欄位。

判定規則（戳場、連勝／連敗、相遇、牌位…）在 analysis.py；這裡只決定「怎麼呈現」：
- 文案（連勝中(N)、N 場前、錯誤簡稱、未定級…）都在這裡產生。
- 色調用語意值（GOOD／BAD／NEUTRAL／MUTED），由 widget 對應成實際顏色。
- 提示框給結構化欄位，由 widget 組成 HTML。
另外也含近期戰績英雄方塊的排法（grid）與單場提示框的時長／死亡速率（stats_line）。
"""
import time
from dataclasses import dataclass, field

from .analysis import GameSummary, Mastery, game_summaries, streak
from .audit import PlayerResult
from .champions import Champions, Icon

# 色調：widget 對應成顏色（GOOD 綠、BAD 紅、NEUTRAL 一般字色、MUTED 灰）
GOOD, BAD, NEUTRAL, MUTED = "good", "bad", "neutral", "muted"

KDA_GOOD, KD_GOOD = 3, 1           # 平均 KDA／KD 達到這個值算好（綠），否則紅
WIN_RATE_BAD, WIN_RATE_GOOD = 0.4, 0.6  # 勝率低於前者紅、高於後者綠，中間一般字色
ERROR_SHORT = {"not_found": "查無此人", "network": "連線失敗", "server": "伺服器錯誤", "blocked": "連線被擋"}
ERROR_OTHER = "查詢失敗"
RANK_QUEUES = ("單雙", "彈性")


@dataclass(frozen=True)
class Headline:
    """中段左邊的大字：有對戰時是勝率（record 為勝敗場數），否則是一行說明（錯誤簡稱、近期沒有對戰）。"""
    text: str
    tone: str
    record: str = ""  # 例：「3勝1敗」；只有勝率才有
    tip: str = ""     # 滑鼠移上去的說明（錯誤原文）


@dataclass(frozen=True)
class Ratio:
    """中段右邊的一行：平均 KDA 或 KD。"""
    label: str
    value: str
    tone: str


@dataclass(frozen=True)
class QueueRank:
    queue: str     # 單雙、彈性
    text: str      # 例：「金 II 45LP」；沒有定級時是「未定級」
    ranked: bool


@dataclass(frozen=True)
class RankView:
    """標題列的牌位；查不到時 queues 為空，改顯示 note。"""
    queues: tuple[QueueRank, ...] = ()
    note: str = ""
    tip: str = ""  # 滑鼠移上去顯示的勝敗，一個有定級的佇列一行；"" = 不顯示


@dataclass(frozen=True)
class Tag:
    """標籤列的一個小標籤。"""
    text: str
    tone: str = NEUTRAL
    tip: str = ""


@dataclass(frozen=True)
class EncounterTag:
    """相遇標籤：前面加上當時所用英雄的頭像。"""
    champion: str  # 英雄 id（頭像）
    text: str      # 例：「上一場的隊友 · 阿璃 3/4/5」
    tone: str      # 隊友 GOOD、敵人 BAD


@dataclass(frozen=True)
class CardView:
    """一張玩家卡片（稽查結果回來後）要顯示的內容。"""
    level: str                       # 接在名稱後面，例：「Lv 100」；查詢失敗時為 ""
    rank: RankView | None            # None = 不顯示牌位（查詢失敗）
    headline: Headline
    ratios: tuple[Ratio, ...] = ()   # 平均 KDA、KD；沒有對戰或查詢失敗時為空
    games: list[GameSummary] = field(default_factory=list)  # 英雄方塊，新 → 舊（含重開局）
    streak: Tag | None = None
    poke: Tag | None = None          # 近期戰績有任一場戳場
    encounter: EncounterTag | None = None


def error_text(kind: str) -> str:
    """查詢失敗的簡稱（顯示在勝率的位置）。"""
    return ERROR_SHORT.get(kind, ERROR_OTHER)


def win_rate_tone(rate: float) -> str:
    return BAD if rate < WIN_RATE_BAD else GOOD if rate > WIN_RATE_GOOD else NEUTRAL


def ratio_tone(value: float, good: float) -> str:
    return GOOD if value >= good else BAD


def streak_tag(games: list[GameSummary]) -> Tag | None:
    """連勝中(N)／連敗中(N)；未達門檻（analysis.STREAK_MIN）時為 None。"""
    st = streak(games)
    if st is None:
        return None
    return Tag(f"{'連勝中' if st.win else '連敗中'}({st.games})", GOOD if st.win else BAD)


def poke_tag(games: list[GameSummary], rate: float, kda: float) -> Tag | None:
    """近期戰績有任一場戳場才顯示；提示框寫出目前的門檻。"""
    if not any(g.poke for g in games):
        return None
    return Tag("戳", tip=f"每分鐘死亡 ≥ {rate:.2f} 次，且 KDA ≤ {kda:.2f}")


def encounter_tag(r: PlayerResult, champions: Champions) -> EncounterTag | None:
    """最近一次相遇：「上一場」或「N 場前」，隊友或敵人，所用英雄與 K/D/A。"""
    e = r.encounter
    if e is None:
        return None
    when = "上一場" if e.games_ago == 0 else f"{e.games_ago + 1} 場前"
    side = "隊友" if e.ally else "敵人"
    return EncounterTag(e.champion_id, f"{when}的{side} · {champions.name(e.champion_id)} {e.kills}/{e.deaths}/{e.assists}",
                        GOOD if e.ally else BAD)


def rank_view(ranks) -> RankView:
    """ranks：佇列名稱 → analysis.Rank；None = 查不到，{} = 都沒有定級。"""
    if ranks is None:
        return RankView(note="牌位查不到")
    queues, tips = [], []
    for q in RANK_QUEUES:
        r = ranks.get(q)
        queues.append(QueueRank(q, str(r) if r else "未定級", bool(r)))
        if r:
            games = r.wins + r.losses
            tips.append(f"{q} {r.wins}勝{r.losses}敗" + (f"（{round(r.wins / games * 100)}%）" if games else ""))
    return RankView(tuple(queues), tip="\n".join(tips))


def card_view(r: PlayerResult, champions: Champions, poke_rate: float, poke_kda: float) -> CardView:
    """稽查結果 → 卡片內容。poke_rate、poke_kda：設定頁的戳場門檻（每分鐘死亡次數、單場 KDA）。"""
    if r.error:
        if r.error.kind == "key":  # Key 的問題顯示在 Key 清單，卡片只留一槓
            return CardView("", None, Headline("—", MUTED))
        return CardView("", None, Headline(error_text(r.error.kind), BAD, tip=str(r.error)))
    s = r.stats
    if s.games:
        headline = Headline(f"{round(s.win_rate * 100)}%", win_rate_tone(s.win_rate), record=f"{s.wins}勝{s.games - s.wins}敗")
        ratios = (Ratio("KDA", f"{s.kda:.2f}", ratio_tone(s.kda, KDA_GOOD)),
                  Ratio("KD", f"{s.kd:.2f}", ratio_tone(s.kd, KD_GOOD)))
    else:
        headline, ratios = Headline("近期沒有對戰", MUTED), ()
    games = game_summaries(r.puuid, r.matches, poke_rate, poke_kda)  # 全是重開局時統計為空，方塊仍列出
    return CardView(f"Lv {s.level if s.level is not None else '?'}", rank_view(r.ranks), headline, ratios, games,
                    streak_tag(games), poke_tag(games, poke_rate, poke_kda), encounter_tag(r, champions))


# ─── 本場英雄（只在遊戲中） ───

@dataclass(frozen=True)
class ChampionLine:
    """本場英雄與熟練度的一行：頭像、英雄名，接著 facts（以 · 分隔），最後灰色的 note。"""
    champion: str | None  # 英雄 id（頭像）；英雄資料還沒載入時 None
    name: str
    facts: tuple[str, ...] = ()
    note: str = ""


def ago(epoch: float, now: float | None = None) -> str:
    secs = (time.time() if now is None else now) - epoch
    if secs < 3600:
        return "剛剛"
    if secs < 86400:
        return f"{secs / 3600:.0f} 小時前"
    return f"{secs / 86400:.0f} 天前"


def points(n: int) -> str:
    return f"{n / 10000:.1f} 萬點" if n >= 10000 else f"{n} 點"


def champion_line(key: int, m: Mastery | None, *, loaded: bool, hidden: bool, champions: Champions,
                  now: float | None = None) -> ChampionLine:
    """loaded：熟練度已查完（m 為 None 即沒玩過）；hidden：身分被隱藏的玩家只顯示英雄。"""
    cid = champions.by_key(key)
    name = champions.name(cid) if cid else "本場英雄"
    if hidden:
        return ChampionLine(cid, name)
    if m is not None:
        return ChampionLine(cid, name, (f"熟練 {m.level} 級", points(m.points)), ago(m.last_play, now) + "玩過")
    return ChampionLine(cid, name, note="沒玩過（或查不到）" if loaded else "查詢中…")


# ─── 單場提示框 ───

@dataclass(frozen=True)
class GameTip:
    """近期戰績一場的提示框：頭像、K/D/A 與勝敗、英雄與模式、裝備列、主符文與召喚師技能列、時長與死亡速率；
    重開局另加一行說明。戳場不另外寫，方塊的光暈已經標示。"""
    champion: str  # 英雄 id（頭像）
    score: str     # 例：「2 / 1 / 3」
    result: str    # 勝／敗
    result_tone: str
    name: str      # 英雄名
    mode: str
    items: tuple[Icon, ...]
    rune: Icon | None
    spells: tuple[Icon, ...]
    stats: str
    note: str = ""


def game_tip(g: GameSummary, champions: Champions) -> GameTip:
    c = champions
    return GameTip(g.champion, f"{g.kills} / {g.deaths} / {g.assists}", "勝" if g.win else "敗", GOOD if g.win else BAD,
                   c.name(g.champion), g.mode, tuple(c.item(i) for i in g.items),
                   c.rune(g.keystone) if g.keystone else None, tuple(c.spell(s) for s in g.spells),
                   stats_line(g), "重開局（不計入統計）" if g.remake else "")


def clock(minutes: float) -> str:
    """分鐘數 → m:ss（超過一小時仍以分鐘計，例：65:30）。"""
    total = round(minutes * 60)
    return f"{total // 60}:{total % 60:02d}"


def stats_line(g: GameSummary) -> str:
    """單場提示框的一行：整局時長、死亡速率與 KDA（戳場的兩個判定依據）。每一場都顯示（含重開局），不另外標記戳場。"""
    return f"時長 {clock(g.minutes)} · {g.death_rate:.2f} 死/分 · KDA {g.kda:.2f}"


# ─── 英雄方塊的排法 ───

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
