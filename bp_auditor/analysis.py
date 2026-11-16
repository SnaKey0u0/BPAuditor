"""純函式：從 Riot API 的回應算出近期戰績（統計與單場摘要）、牌位、英雄熟練度、可能多排、相遇，以及拆開進行中對局的兩隊。

所有 `matches` 參數都是「新 → 舊」排序的 match-v5 對戰 dict 清單。
"""
from dataclasses import dataclass, field
from itertools import combinations

PREMADE_MIN_STREAK = 2
PREMADE_MIN_TOTAL = 3
QUEUES = {450: "ARAM", 420: "單雙", 440: "彈性", 400: "一般", 430: "一般", 490: "快速", 1700: "競技場",
          900: "URF", 1900: "URF", 1300: "極限"}


def participant(match: dict, puuid: str) -> dict | None:
    for p in match["info"]["participants"]:
        if p["puuid"] == puuid:
            return p
    return None


def _kda(ps: list[dict]) -> float:
    k = sum(p["kills"] for p in ps)
    d = sum(p["deaths"] for p in ps)
    a = sum(p["assists"] for p in ps)
    return (k + a) / max(d, 1)


def _kd(ps: list[dict]) -> float:
    return sum(p["kills"] for p in ps) / max(sum(p["deaths"] for p in ps), 1)


@dataclass
class RecentStats:
    games: int
    wins: int
    kda: float
    kd: float
    results: list[bool]  # 新 → 舊
    level: int | None

    @property
    def win_rate(self) -> float:
        return self.wins / self.games if self.games else 0.0


def recent_stats(puuid: str, matches: list[dict]) -> RecentStats:
    ps = [p for m in matches if (p := participant(m, puuid))]
    return RecentStats(
        games=len(ps),
        wins=sum(p["win"] for p in ps),
        kda=_kda(ps),
        kd=_kd(ps),
        results=[p["win"] for p in ps],
        level=ps[0].get("summonerLevel") if ps else None,
    )


@dataclass
class GameSummary:
    """近期戰績中的一場（單場摘要）；介面只渲染這份摘要，不自己讀對戰原始資料。"""
    win: bool
    champion: str  # match-v5 的 championName
    kills: int
    deaths: int
    assists: int
    mode: str      # 佇列的顯示名稱（例如 ARAM、單雙）；不認得的佇列用 gameMode
    items: list[int] = field(default_factory=list)  # 結束時的裝備 id，依格子順序，空格略過
    keystone: int | None = None                     # 主符文 id；該模式沒有符文時為 None
    spells: list[int] = field(default_factory=list)  # 兩個召喚師技能的數字 key


def _mode(match: dict) -> str:
    info = match["info"]
    return QUEUES.get(info.get("queueId"), info.get("gameMode", ""))


def _items(p: dict) -> list[int]:
    return [i for n in range(7) if (i := p.get(f"item{n}", 0))]


def _keystone(p: dict) -> int | None:
    styles = (p.get("perks") or {}).get("styles") or []
    selections = styles[0].get("selections") or [] if styles else []
    return selections[0].get("perk") if selections else None


def _spells(p: dict) -> list[int]:
    return [s for n in (1, 2) if (s := p.get(f"summoner{n}Id", 0))]


def game_summaries(puuid: str, matches: list[dict]) -> list[GameSummary]:
    """近期戰績的每一場 → 單場摘要，新 → 舊；玩家不在的對戰略過。"""
    return [GameSummary(p["win"], p["championName"], p["kills"], p["deaths"], p["assists"], _mode(m),
                        items=_items(p), keystone=_keystone(p), spells=_spells(p))
            for m in matches if (p := participant(m, puuid))]


TIERS = {"IRON": "鐵", "BRONZE": "銅", "SILVER": "銀", "GOLD": "金", "PLATINUM": "白金", "EMERALD": "翡翠",
         "DIAMOND": "鑽石", "MASTER": "大師", "GRANDMASTER": "宗師", "CHALLENGER": "菁英"}
RANK_QUEUES = {"RANKED_SOLO_5x5": "單雙", "RANKED_FLEX_SR": "彈性"}


@dataclass
class Rank:
    tier: str      # 例如 "GOLD"
    division: str  # "I"–"IV"；大師以上沒有分級
    lp: int
    wins: int
    losses: int

    def __str__(self):
        name = TIERS.get(self.tier, self.tier)
        div = "" if self.tier in ("MASTER", "GRANDMASTER", "CHALLENGER") else f" {self.division}"
        return f"{name}{div} {self.lp}LP"


def ranks(entries: list[dict]) -> dict[str, Rank]:
    """league-v4 entries → 佇列名稱（單雙、彈性）→ 牌位；沒有定級的佇列不會出現。"""
    return {RANK_QUEUES[e["queueType"]]: Rank(e["tier"], e.get("rank", ""), e.get("leaguePoints", 0),
                                              e.get("wins", 0), e.get("losses", 0))
            for e in entries if e.get("queueType") in RANK_QUEUES}


@dataclass
class Mastery:
    level: int
    points: int
    last_play: float  # epoch 秒


def mastery(entry: dict | None) -> Mastery | None:
    """champion-mastery-v4 的單一英雄熟練度；None 代表沒玩過。"""
    if not entry:
        return None
    return Mastery(entry.get("championLevel", 0), entry.get("championPoints", 0), entry.get("lastPlayTime", 0) / 1000)


@dataclass
class GamePlayer:
    label: str        # Riot ID 字串「名稱#標籤」
    puuid: str         # 空字串 = Riot 隱藏了此人身分（觀戰資料沒有 PUUID，Riot ID 只寫英雄名），無法查詢
    champion_key: int  # Data Dragon 的英雄數字 key


def split_game(game: dict, me_puuid: str) -> tuple[list[GamePlayer], list[GamePlayer]]:
    """spectator-v5 的進行中對局 → (我方, 對面)，我方含本人；電腦玩家略過，身分被隱藏的玩家保留（puuid 為空）。"""
    ps = [p for p in game.get("participants", []) if not p.get("bot")]
    my_team = next((p["teamId"] for p in ps if p["puuid"] == me_puuid), None)
    allies, enemies = [], []
    for p in ps:
        gp = GamePlayer(p.get("riotId", ""), p.get("puuid") or "", p.get("championId", 0))
        (allies if p["teamId"] == my_team else enemies).append(gp)
    return allies, enemies


@dataclass
class PremadeGroup:
    members: list[str]  # 房間成員的標籤（例如 Riot ID 字串）
    streak: int         # 從最近一場往回連續同隊場數
    total: int          # 近期戰績內同隊場數


def _together(match: dict, puuids: list[str]) -> bool:
    teams = set()
    for pu in puuids:
        p = participant(match, pu)
        if p is None:
            return False
        teams.add(p["teamId"])
    return len(teams) == 1


def _group_stats(histories: dict[str, tuple[str, list[dict]]], labels: list[str]) -> tuple[int, int]:
    """在每位成員各自的近期戰績中算連續/總同隊場數，取最大值。"""
    puuids = [histories[l][0] for l in labels]
    best_streak = best_total = 0
    for l in labels:
        flags = [_together(m, puuids) for m in histories[l][1]]
        streak = next((i for i, f in enumerate(flags) if not f), len(flags))
        best_streak = max(best_streak, streak)
        best_total = max(best_total, sum(flags))
    return best_streak, best_total


def _flagged(streak: int, total: int) -> bool:
    return streak >= PREMADE_MIN_STREAK or total >= PREMADE_MIN_TOTAL


def detect_premades(histories: dict[str, tuple[str, list[dict]]]) -> list[PremadeGroup]:
    """histories: 標籤 → (puuid, 近期戰績)。只比對房間成員彼此，不論當時與本人同隊或敵對。"""
    labels = list(histories)
    pairs = {}
    for a, b in combinations(labels, 2):
        s, t = _group_stats(histories, [a, b])
        if _flagged(s, t):
            pairs[(a, b)] = (s, t)

    parent = {l: l for l in labels}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        parent[find(a)] = find(b)
    comps: dict[str, list[str]] = {}
    for l in labels:
        comps.setdefault(find(l), []).append(l)

    # 有配對連起來的人併成一組，場數取組內兩兩配對的最大值
    groups = []
    for comp in comps.values():
        stats = [st for (a, _), st in pairs.items() if a in comp]
        if stats:
            groups.append(PremadeGroup(comp, max(s for s, _ in stats), max(t for _, t in stats)))
    return sorted(groups, key=lambda g: (-len(g.members), -g.streak))


@dataclass
class Encounter:
    games_ago: int      # 0 = 本人的上一場
    ally: bool
    champion_id: str
    kills: int
    deaths: int
    assists: int


def find_encounter(self_puuid: str, self_match_ids: list[str], member_puuid: str,
                   member_matches: list[dict]) -> Encounter | None:
    """找出房間成員最近一次出現在本人近期對戰中的那場（只看已抓到的對戰）。"""
    by_id = {m["metadata"]["matchId"]: m for m in member_matches}
    for i, mid in enumerate(self_match_ids):
        m = by_id.get(mid)
        if not m:
            continue
        me, them = participant(m, self_puuid), participant(m, member_puuid)
        if me and them:
            return Encounter(i, me["teamId"] == them["teamId"], them["championName"],
                             them["kills"], them["deaths"], them["assists"])
    return None
