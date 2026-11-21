"""一次稽查的流程：並行查房間成員，查完一人回報一人，最後回報可能多排。

同一房間多了新人時，只查新的人（`query`），舊成員的近期戰績由 `prior` 帶入，只存在記憶體。
進遊戲後查對面也走同一個流程：`room` 是兩隊全部，`sides` 讓可能多排分隊判斷，
`puuids` 是觀戰 API 已給的 PUUID（不必再查帳號），`champions` 是本場英雄（查熟練度用）。
"""
import threading
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable

from .analysis import (Encounter, Mastery, PremadeGroup, Rank, RecentStats, detect_premades, find_encounter,
                       mastery, ranks, recent_stats)
from .parsing import RiotId
from .riot import RiotClient, RiotError

DEFAULT_GAMES = 5
ME = "你"  # 可能多排群組中代表本人的標籤

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
    ranks: dict[str, Rank] | None = None  # None = 查不到；{} = 沒有定級
    mastery: Mastery | None = None        # 本場英雄的熟練度；只在遊戲中查
    error: RiotError | None = None
    is_me: bool = False  # 本人也有卡片；改名後以新名字出現在房間時，查完才知道是本人


@dataclass
class AuditOutcome:
    premades: list[PremadeGroup]
    histories: dict[str, History] = field(default_factory=dict)  # 房間成員標籤（含「你」）→ 近期戰績


class Audit:
    def __init__(self, client: RiotClient, room: list[RiotId], me: Me, games: int = DEFAULT_GAMES, *,
                 query: list[RiotId] | None = None, prior: dict[str, History] | None = None,
                 puuids: dict[str, str] | None = None, champions: dict[str, int] | None = None,
                 sides: list[list[str]] | None = None,
                 on_player: Callable[[PlayerResult], None] = lambda r: None,
                 on_me: Callable[[Me], None] = lambda m: None,
                 on_outcome: Callable[[AuditOutcome], None] = lambda o: None,
                 on_mastery: Callable[[str, Mastery | None], None] = lambda label, m: None,
                 on_done: Callable[[], None] = lambda: None):
        self.client, self.room, self.me, self.games = client, room, me, games
        self.query = room if query is None else query
        self.prior = dict(prior or {})
        self.puuids, self.champions, self.sides = puuids or {}, champions or {}, sides
        self.on_player, self.on_me, self.on_outcome, self.on_done = on_player, on_me, on_outcome, on_done
        self.on_mastery = on_mastery
        self._match_pool = ThreadPoolExecutor(max_workers=10)
        self._matches: dict[str, Future] = {}
        self._ids: dict[str, Future] = {}
        self._me_future: Future | None = None
        self._lock = threading.Lock()

    # ─── 共用：同一場對戰在一次稽查內只查一次 ───
    def _match(self, mid: str) -> Future:
        with self._lock:
            if mid not in self._matches:
                self._matches[mid] = self._match_pool.submit(self.client.match, mid)
            return self._matches[mid]

    def _fetch(self, ids: list[str]) -> list[dict]:
        return [f.result() for f in [self._match(mid) for mid in ids]]

    def _match_ids(self, puuid: str) -> list[str]:
        """同一個人的對戰 id 在一次稽查內只查一次（本人的卡片與 _resolve_me 共用）。"""
        with self._lock:
            f, owner = self._ids.get(puuid), puuid not in self._ids
            if owner:
                f = self._ids[puuid] = Future()
        if owner:
            try:
                f.set_result(self.client.match_ids(puuid, self.games))
            except BaseException as e:
                f.set_exception(e)
        return f.result()

    def _puuid(self, rid: RiotId) -> str:
        """觀戰 API 已給的、或房間裡寫著本人的 Riot ID 時沿用 _resolve_me 查到的，都不必再查帳號。"""
        if puuid := self.puuids.get(str(rid)):
            return puuid
        if self._me_future and self.me.riot_id and rid.same_as(self.me.riot_id):
            try:
                me, _ = self._me_future.result()
            except RiotError:
                me = None  # 本人查不到：當成一般玩家，用 Riot ID 查
            if me and me.riot_id and rid.same_as(me.riot_id):  # 本人改名、舊名字被別人拿去時不會認錯
                return me.puuid
        return self.client.account_puuid(rid.name, rid.tag)

    def _player(self, rid: RiotId) -> tuple[str, list[dict], dict[str, Rank] | None, Mastery | None]:
        """(puuid, 近期戰績, 牌位, 本場英雄熟練度)。"""
        puuid = self._puuid(rid)
        futures = [self._match(mid) for mid in self._match_ids(puuid)]
        rank, mast = self._ranks(puuid), self._mastery(str(rid), puuid)  # 對戰在背景下載時順便查
        return puuid, [f.result() for f in futures], rank, mast

    def _resolve_me(self) -> tuple[Me, list[str]]:
        """查出本人目前的 Riot ID（偵測改名）與近期對戰 id。
        之前已查過本人的近期戰績（prior 有「你」：同一房間多了新人、或進了遊戲）時直接沿用，改名那時已經偵測過。"""
        if (prior := self.prior.get(ME)) and self.me.riot_id and self.me.puuid and prior[0] == self.me.puuid:
            return self.me, [m["metadata"]["matchId"] for m in prior[1]]
        me = None
        if self.me.puuid:
            try:
                me = Me(RiotId(*self.client.riot_id_of(self.me.puuid)), self.me.puuid)
            except RiotError as e:
                # PUUID 依 Key 所屬產品加密：換 Key（例如改用 Personal Key）後舊 PUUID 解不開，改用 Riot ID 重查
                if e.kind not in ("bad_request", "not_found") or not self.me.riot_id:
                    raise
        if me is None:
            rid = self.me.riot_id
            me = Me(rid, self.client.account_puuid(rid.name, rid.tag))
        if me.riot_id != self.me.riot_id or me.puuid != self.me.puuid:
            self.on_me(me)
        return me, self._match_ids(me.puuid)

    def _ranks(self, puuid: str) -> dict[str, Rank] | None:
        try:
            return ranks(self.client.league_entries(puuid))
        except RiotError:
            return None  # 牌位查不到不影響戰績

    def _mastery(self, label: str, puuid: str) -> Mastery | None:
        if label not in self.champions:
            return None
        try:
            return mastery(self.client.champion_mastery(puuid, self.champions[label]))
        except RiotError:
            return None

    def _prior_mastery(self, label: str):
        """之前已查過戰績的人（例如房間成員進了遊戲）只補查本場英雄的熟練度。"""
        self.on_mastery(label, self._mastery(label, self.puuids.get(label) or self.prior[label][0]))

    def run(self):
        """阻塞執行；請在背景執行緒呼叫。"""
        try:
            self.execute()
        finally:
            self.on_done()

    def execute(self):
        """同 run，但不呼叫 on_done：換 Key 時（KeySwitch，見 rotation.run_rotating）整次稽查會用新 Key 重跑。"""
        try:
            self._run()
        finally:
            self._match_pool.shutdown(wait=False)

    def _run(self):
        has_me = bool(self.me.puuid or self.me.riot_id)
        with ThreadPoolExecutor(max_workers=6) as pool:
            me_future = self._me_future = pool.submit(self._resolve_me) if has_me else None
            futures = {pool.submit(self._player, m): m for m in self.query}
            queried = {str(m) for m in self.query}
            extra = [pool.submit(self._prior_mastery, l) for l in self.champions
                     if l not in queried and (l in self.puuids or l in self.prior)]

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
                    r.puuid, r.matches, r.ranks, r.mastery = f.result()
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
            for f in extra:
                f.result()

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
        sides = self.sides or [list(histories)]
        premades = [g for side in sides for g in detect_premades({l: histories[l] for l in side if l in histories})]
        self.on_outcome(AuditOutcome(premades, histories))
