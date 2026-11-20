"""Key 清單：使用者存的多組 Personal API Key，各自的驗證狀態。使用中的是哪一組、何時輪替見 rotation.py。

不碰 Qt、不發請求；驗證（打 Riot）與畫面由 gui 負責，這裡只管清單本身，方便測試。
見 docs/adr/0002-key-pool-rerun-audit-per-key.md。
"""
from dataclasses import dataclass

# 每組 Key 的狀態。驗證中照常可用（與改版前相同：剛貼上的 Key 不擋稽查）；生效中與失效都不拿來發請求。
CHECKING = "checking"  # 剛加入，驗證中
WARMING = "warming"  # 剛產生的 Key 回 401/403：Riot 還沒啟用，每 15 秒重驗
OK = "ok"
BAD = "bad"  # 回 401/403 且不是剛產生的：失效，請使用者重新產生；不自動刪除
_USABLE = (CHECKING, OK)


@dataclass
class ApiKey:
    key: str
    added_at: float = 0.0  # epoch 秒；剛產生的 Key 要 1–2 分鐘才生效，用來判斷 401 是「還沒生效」還是「失效」
    status: str = CHECKING

    @property
    def usable(self) -> bool:
        return self.status in _USABLE


@dataclass
class PoolSummary:
    text: str
    tone: str  # "ok"、"busy"（驗證／生效中）、"error"（沒有 Key 或全部失效）


class KeyPool:
    """有順序的 Key 清單（#1、#2…）；同一組 Key 只會出現一次。"""

    def __init__(self, entries: list[ApiKey] | None = None):
        self._entries: list[ApiKey] = list(entries or [])

    def __iter__(self):
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, key: str) -> ApiKey | None:
        key = key.strip()
        return next((k for k in self._entries if k.key == key), None)

    def add(self, key: str, now: float) -> ApiKey | None:
        """加到清單最後；空字串或已經在清單裡就不加，回 None。"""
        key = key.strip()
        if not key or self.get(key):
            return None
        entry = ApiKey(key, now)
        self._entries.append(entry)
        return entry

    def remove(self, key: str) -> bool:
        entry = self.get(key)
        if entry:
            self._entries.remove(entry)
        return entry is not None

    def set_status(self, key: str, status: str):
        if entry := self.get(key):
            entry.status = status

    def first_usable(self) -> ApiKey | None:
        """清單中第一組可用的 Key；都不能用時回 None（使用中的 Key 由 rotation.KeyRotation 決定）。"""
        return next((k for k in self._entries if k.usable), None)

    @property
    def all_bad(self) -> bool:
        """清單裡有 Key，但全部失效：要請使用者重新產生。"""
        return bool(self._entries) and all(k.status == BAD for k in self._entries)

    def summary(self) -> PoolSummary:
        """整個清單的狀態，給側欄與設定頁。"""
        if not self._entries:
            return PoolSummary("未設定 Key", "error")
        if self.all_bad:
            return PoolSummary("Key 全部失效，請重新產生", "error")
        ok = sum(k.status == OK for k in self._entries)
        if not ok:
            if any(k.status == CHECKING for k in self._entries):
                return PoolSummary("驗證 Key 中…", "busy")
            return PoolSummary("新 Key 生效中…", "busy")
        return PoolSummary(f"{ok} / {len(self._entries)} 組可用", "ok")

    # ─── 存檔 ───
    def to_json(self) -> list[dict]:
        """只記得「失效」：驗證中／生效中是這次執行的暫時狀態，下次啟動不會自動重驗，載入時當作可用。"""
        return [{"key": k.key, "added_at": k.added_at, "bad": k.status == BAD} for k in self._entries]

    @classmethod
    def from_json(cls, data) -> "KeyPool":
        pool = cls()
        for d in data or []:
            key = str(d.get("key", "")).strip() if isinstance(d, dict) else ""
            if key and not pool.get(key):
                pool._entries.append(ApiKey(key, float(d.get("added_at") or 0.0), BAD if d.get("bad") else OK))
        return pool


def mask(key: str) -> str:
    """設定頁顯示用：RGAPI-1a2b…9f0e。"""
    return f"{key[:10]}…{key[-4:]}"
