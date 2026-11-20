"""本人與黑名單的 PUUID 依 Key 分開存（ADR 0002）。

Riot 的 PUUID 依 Key 持有者加密：同一位玩家在不同 Personal Key 下是不同的 PUUID。
所以每位玩家存成「Key → 該 Key 的 PUUID」（Versions），比對時一律用這次稽查所用 Key 的版本。

不碰 Qt；換算（打 Riot）的函式都收 `client_for(key)`，回傳換算結果（Version），
由呼叫端在主執行緒套用到設定（Config.add_versions），方便測試、也不會在背景改設定。
"""
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass

from .parsing import RiotId
from .riot import RiotError

Versions = dict[str, str]  # Key → 該 Key 加密的 PUUID；查不到的 Key 不會出現


@dataclass(frozen=True)
class Version:
    """換算結果：在 known_key 下 PUUID 是 known_puuid 的那位玩家，在 key 下的 PUUID 是 puuid。"""
    known_key: str
    known_puuid: str
    key: str
    puuid: str


class Blacklist:
    """黑名單：每一筆是同一位玩家在各組 Key 下的 PUUID。只有「在／不在」，沒有備註。"""

    def __init__(self, entries: Iterable[Versions] | None = None):
        self._entries: list[Versions] = [dict(e) for e in entries or [] if e]

    def __iter__(self) -> Iterator[Versions]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def _find(self, key: str, puuid: str) -> Versions | None:
        if not key or not puuid:
            return None
        return next((e for e in self._entries if e.get(key) == puuid), None)

    def contains(self, key: str, puuid: str) -> bool:
        return self._find(key, puuid) is not None

    def toggle(self, key: str, puuid: str) -> bool:
        """加入（只先存 key 這一版，其他 Key 的版本之後用 spread 補）或移除；回傳之後是否在黑名單。"""
        entry = self._find(key, puuid)
        if entry is not None:
            self._entries.remove(entry)
            return False
        if key and puuid:
            self._entries.append({key: puuid})
            return True
        return False

    def add_version(self, v: Version) -> bool:
        """把換算結果記到對應的那一筆；那一筆已經被移出黑名單時不做事。"""
        entry = self._find(v.known_key, v.known_puuid)
        if entry is None or not v.puuid:
            return False
        entry[v.key] = v.puuid
        return True

    def drop_key(self, key: str):
        """刪除 Key：一併刪掉它那個版本；沒有任何版本的那筆再也比對不到，直接移除。"""
        for e in self._entries:
            e.pop(key, None)
        self._entries = [e for e in self._entries if e]

    def snapshot(self) -> list[Versions]:
        """給背景執行緒用的複本。"""
        return [dict(e) for e in self._entries]

    def to_json(self) -> list[Versions]:
        return self.snapshot()

    @classmethod
    def from_json(cls, data, legacy_key: str = "") -> "Blacklist":
        """舊設定檔的黑名單是一串 PUUID（只出自一組 Key）：歸到 legacy_key（清單的 #1）。"""
        entries = []
        for d in data or []:
            if isinstance(d, str):
                if d and legacy_key:
                    entries.append({legacy_key: d})
            elif isinstance(d, dict):
                entries.append({str(k): str(v) for k, v in d.items() if k and v})
        return cls(entries)


# ─── 換算（打 Riot） ───
ClientFor = Callable[[str], object]  # Key → RiotClient（或測試用的假客戶端）


def convert(puuid: str, source, target) -> str:
    """把 source Key 的 PUUID 換成 target Key 的版本：先查回對方**目前的** Riot ID，再用 target 查。"""
    name, tag = source.riot_id_of(puuid)
    return target.account_puuid(name, tag)


def backfill(entries: Iterable[Versions], key: str, sources: list[str], client_for: ClientFor) -> list[Version]:
    """新增 Key 後：替還沒有 key 版本的每一筆，用一組既有的可用 Key（sources，依序嘗試）換算出新版。
    查不到的那一筆（例如帳號已刪除）就留空，不影響其他筆。"""
    found = []
    target = client_for(key)
    for e in entries:
        if e.get(key):
            continue
        for src in sources:
            if src == key or not e.get(src):
                continue
            try:
                found.append(Version(src, e[src], key, convert(e[src], client_for(src), target)))
                break
            except RiotError:
                continue
    return found


def spread(key: str, puuid: str, targets: list[str], client_for: ClientFor) -> list[Version]:
    """加入黑名單後：用每組可用的 Key（targets）各換算一份；某組查不到就跳過。"""
    found = []
    source = client_for(key)
    for t in targets:
        if t == key:
            continue
        try:
            found.append(Version(key, puuid, t, convert(puuid, source, client_for(t))))
        except RiotError:
            continue
    return found


def lookup_me(rid: RiotId, keys: list[str], client_for: ClientFor) -> tuple[Versions, RiotError | None]:
    """用本人的 Riot ID 在每組 Key 下各查一次 PUUID。回傳 (查到的版本, 第一個錯誤)；某組查不到不影響其他組。"""
    found, error = {}, None
    for k in keys:
        try:
            found[k] = client_for(k).account_puuid(rid.name, rid.tag)
        except RiotError as e:
            error = error or e
    return found, error
