"""黑名單（#25）：本人手動標記、不想再遇到的玩家。

Riot 的 PUUID 依 Key 加密，同一個人在不同 Key 下是不同的 PUUID；Riot ID 則可能被改掉。
所以每一筆同時記 Riot ID 與「Key → PUUID」，比對時：
- 這組 Key 有這筆的 PUUID：以 PUUID 比對（改名也認得），比對到就把 Riot ID 更新成目前的名字；
- 這組 Key 沒有這筆的 PUUID（例如剛換新 Key）：以 Riot ID 比對（不分大小寫），比對到就記下這組 Key 的 PUUID。
稽查本來就會查每個人的 Riot ID 與 PUUID，所以不需要額外的 Riot API 請求，也不需要舊 Key 還能用。

不碰 Qt；呼叫端負責存檔。
"""
from dataclasses import dataclass, field


@dataclass
class Entry:
    riot_id: str                                          # 最後一次見到時的 Riot ID（名稱#標籤）
    puuids: dict[str, str] = field(default_factory=dict)  # Key → 該 Key 加密的 PUUID


@dataclass(frozen=True)
class Match:
    """Blacklist.check 的回報。"""
    listed: bool   # 在不在黑名單
    changed: bool  # 比對時有沒有補齊這組 Key 的 PUUID 或更新 Riot ID（有就該存檔）


def _same_name(a: str, b: str) -> bool:
    return a.casefold() == b.casefold()


class Blacklist:
    def __init__(self, entries: list[Entry] | None = None):
        self.entries = entries or []

    def _find(self, key: str, puuid: str, riot_id: str) -> Entry | None:
        for e in self.entries:
            if e.puuids.get(key) == puuid:
                return e
        for e in self.entries:  # 這組 Key 還沒有版本的才以 Riot ID 比對；有版本卻不符，就是別的帳號
            if key not in e.puuids and _same_name(e.riot_id, riot_id):
                return e
        return None

    def check(self, key: str, puuid: str, riot_id: str) -> Match:
        """這個人（puuid 出自 key）在不在黑名單；比對到就順手補齊這組 Key 的 PUUID 與目前的 Riot ID。

        回報裡的 changed 說明有沒有補齊或改動資料，呼叫端據此決定要不要存檔。
        """
        if not (e := self._find(key, puuid, riot_id)):
            return Match(listed=False, changed=False)
        changed = e.puuids.get(key) != puuid or e.riot_id != riot_id
        e.puuids[key], e.riot_id = puuid, riot_id
        return Match(listed=True, changed=changed)

    def toggle(self, key: str, puuid: str, riot_id: str) -> bool:
        """在黑名單就移除、不在就加入；回傳切換後是否在黑名單。"""
        if e := self._find(key, puuid, riot_id):
            self.entries.remove(e)
            return False
        self.entries.append(Entry(riot_id, {key: puuid}))
        return True

    def drop_key(self, key: str):
        """刪除 Key 時一併刪掉這組 Key 的 PUUID；整筆保留，之後仍能以 Riot ID 比對。"""
        for e in self.entries:
            e.puuids.pop(key, None)

    def to_json(self) -> list[dict]:
        return [{"riot_id": e.riot_id, "puuids": dict(e.puuids)} for e in self.entries]

    @classmethod
    def from_json(cls, data) -> "Blacklist":
        """格式不符的資料（含 v1.4.2 只有 PUUID 的舊格式）直接捨棄，不搬移。"""
        entries = []
        for d in data if isinstance(data, list) else []:
            if not isinstance(d, dict) or not isinstance(d.get("riot_id"), str) or not d["riot_id"].strip():
                continue
            puuids = d.get("puuids") if isinstance(d.get("puuids"), dict) else {}
            entries.append(Entry(d["riot_id"], {str(k): str(v) for k, v in puuids.items() if k and v}))
        return cls(entries)
