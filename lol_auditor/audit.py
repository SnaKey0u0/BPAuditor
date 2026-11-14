"""一次稽查的流程：並行查房間成員，查完一人回報一人，最後回報疑似多排。

同一房間多了新人時，只查新的人（`query`），舊成員的近期戰績由 `prior` 帶入，只存在記憶體。
"""
import threading
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable

from .analysis import Encounter, PremadeGroup, RecentStats, detect_premades, find_encounter, recent_stats
from .parsing import RiotId
from .riot import RiotClient, RiotError

DEFAULT_GAMES = 10
ME = "你"  # 疑似多排群組中代表本人的標籤

History = tuple[str, list[dict]]  # (puuid, 近期戰績)


@dataclass
class Me:
    """本人：以 PUUID 認定；只有 Riot ID 時會在稽查中補查 PUUID。"""
    riot_id: RiotId | None = None
    puuid: str = ""


@dataclass
class PlayerResult:
    riot_id: RiotId
    puuid: str | None = None
    matches: list[dict] | None = None
    stats: RecentStats | None = None
    encounter: Encounter | None = None
    error: RiotError | None = None
    is_me: bool = False  # 本人改名後以新名字出現在房間時，查完才知道是本人


@dataclass
class AuditOutcome:
    premades: list[PremadeGroup]
    histories: dict[str, History] = field(default_factory=dict)  # 房間成員標籤（含「你」）→ 近期戰績


class Audit:
    def __init__(self, client: RiotClient, room: list[RiotId], me: Me, games: int = DEFAULT_GAMES, *,
                 query: list[RiotId] | None = None, prior: dict[str, History] | None = None,
                 on_player: Callable[[PlayerResult], None] = lambda r: None,
                 on_me: Callable[[Me], None] = lambda m: None,
                 on_outcome: Callable[[AuditOutcome], None] = lambda o: None,
                 on_done: Callable[[], None] = lambda: None):
        self.client, self.room, self.me, self.games = client, room, me, games
        self.query = room if query is None else query
        self.prior = dict(prior or {})
        self.on_player, self.on_me, self.on_outcome, self.on_done = on_player, on_me, on_outcome, on_done
        self._match_pool = ThreadPoolExecutor(max_workers=10)
        self._matches: dict[str, Future] = {}
        self._lock = threading.Lock()

    # ─── 共用：同一場對戰在一次稽查內只查一次 ───
    def _match(self, mid: str) -> Future:
        with self._lock:
            if mid not in self._matches:
                self._matches[mid] = self._match_pool.submit(self.client.match, mid)
            return self._matches[mid]

    def _fetch(self, ids: list[str]) -> list[dict]:
        return [f.result() for f in [self._match(mid) for mid in ids]]

    def _history(self, rid: RiotId) -> tuple[str, list[dict]]:
        puuid = self.client.account_puuid(rid.name, rid.tag)
        return puuid, self._fetch(self.client.match_ids(puuid, self.games))

    def _resolve_me(self) -> tuple[Me, list[str]]:
        """查出本人目前的 Riot ID（偵測改名）與近期對戰 id。"""
        if self.me.puuid:
            current = RiotId(*self.client.riot_id_of(self.me.puuid))
            me = Me(current, self.me.puuid)
        else:
            rid = self.me.riot_id
            me = Me(rid, self.client.account_puuid(rid.name, rid.tag))
        if me.riot_id != self.me.riot_id or me.puuid != self.me.puuid:
            self.on_me(me)
        return me, self.client.match_ids(me.puuid, self.games)

    def run(self):
        """阻塞執行；請在背景執行緒呼叫。"""
        try:
            self._run()
        finally:
            self._match_pool.shutdown(wait=False)
            self.on_done()

    def _run(self):
        has_me = bool(self.me.puuid or self.me.riot_id)
        with ThreadPoolExecutor(max_workers=6) as pool:
            me_future = pool.submit(self._resolve_me) if has_me else None
            futures = {pool.submit(self._history, m): m for m in self.query}

            def me_now() -> tuple[Me, list[str]] | None:
                try:
                    return me_future.result() if me_future else None
                except RiotError:
                    return None  # 本人查不到不影響其他資訊

            new: dict[str, History] = {}
            me_history: History | None = self.prior.pop(ME, None)
            for f in as_completed(futures):  # 查完一人回報一人
                m = futures[f]
                r = PlayerResult(m)
                try:
                    r.puuid, r.matches = f.result()
                    r.stats = recent_stats(r.puuid, r.matches)
                    if r.stats.level is None:
                        r.stats.level = self.client.summoner_level(r.puuid)
                    mine = me_now()
                    if mine and r.puuid == mine[0].puuid:
                        r.is_me = True
                        me_history = (r.puuid, r.matches)
                    elif mine:
                        r.encounter = find_encounter(mine[0].puuid, mine[1], r.puuid, r.matches)
                    if not r.is_me:
                        new[str(m)] = (r.puuid, r.matches)
                except RiotError as e:
                    r.error = e
                self.on_player(r)

            mine = me_now()
            labels = [str(m) for m in self.room]
            histories = {l: h for l in labels if (h := new.get(l) or self.prior.get(l))}
            if mine:
                me_rid, me_ids = mine[0].riot_id, mine[1]
                in_room = any(m.same_as(me_rid) for m in self.room) or me_history is not None
                if in_room:
                    histories.pop(str(me_rid), None)
                    if me_history is None:
                        try:
                            me_history = (mine[0].puuid, self._fetch(me_ids))
                        except RiotError:
                            pass
                    if me_history:
                        histories[ME] = me_history
        self.on_outcome(AuditOutcome(detect_premades(histories), histories))
