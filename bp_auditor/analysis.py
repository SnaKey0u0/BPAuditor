"""純函式：從 match-v5 對戰資料算出近期戰績、指定英雄戰績、疑似多排、相遇。

所有 `matches` 參數都是「新 → 舊」排序的 match-v5 對戰 dict 清單。
"""
from dataclasses import dataclass
from itertools import combinations

PREMADE_MIN_STREAK = 2
PREMADE_MIN_TOTAL = 3


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


@dataclass
class RecentStats:
    games: int
    wins: int
    kda: float
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
        results=[p["win"] for p in ps],
        level=ps[0].get("summonerLevel") if ps else None,
    )


@dataclass
class ChampionStats:
    games: int
    wins: int
    kda: float


def champion_stats(puuid: str, matches: list[dict], champion_id: str) -> ChampionStats:
    """champion_id 為 Data Dragon 的英雄 id（如 'Ahri'），比對時不分大小寫。"""
    cid = champion_id.casefold()
    ps = [p for m in matches if (p := participant(m, puuid)) and p["championName"].casefold() == cid]
    return ChampionStats(len(ps), sum(p["win"] for p in ps), _kda(ps))


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

    groups = []
    for comp in comps.values():
        if len(comp) < 2:
            continue
        if len(comp) > 2:
            s, t = _group_stats(histories, comp)
            if _flagged(s, t):
                groups.append(PremadeGroup(comp, s, t))
                continue
        for (a, b), (s, t) in pairs.items():
            if a in comp:
                groups.append(PremadeGroup([a, b], s, t))
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
