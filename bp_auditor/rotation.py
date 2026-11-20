"""Key 輪替：每組 Key 各有自己的限速；一直用目前這組，直到它冷卻才換下一組（依序循環），
換 Key 時整次稽查用新 Key 從頭重跑（ADR 0002）。

不碰 Qt；時鐘、睡眠與 Riot 傳輸層都可以換成假的，方便測試。
"""
import math
import threading
import time
from typing import Callable, TypeVar

from .keys import BAD, CHECKING, WARMING, ApiKey, KeyPool
from .riot import KeySwitch, RateLimiter, RiotClient, RiotError

# 冷卻要等超過這麼久才換 Key。每秒 20 次的限速幾乎每次稽查都會碰到、只要等不到 1 秒，
# 為它換 Key 重跑只會白費舊 Key 已用掉的請求；真正的冷卻是 120 秒 100 次的視窗或 429。
SWITCH_AFTER = 2.0

T = TypeVar("T")


class KeyRotation:
    """Key 清單的輪替狀態：使用中的 Key，與每組 Key 各自的限速器。執行緒安全。"""

    def __init__(self, pool: KeyPool | Callable[[], KeyPool], *, clock=time.monotonic, sleep=time.sleep,
                 fetch=None, limits=None, switch_after: float = SWITCH_AFTER):
        self._pool = pool if callable(pool) else (lambda: pool)
        self.clock, self.sleep, self._fetch = clock, sleep, fetch
        self._limits, self.switch_after = limits, switch_after
        self._limiters: dict[str, RateLimiter] = {}
        self._current = ""
        self._lock = threading.RLock()

    # ─── 限速 ───
    def limiter(self, key: str) -> RateLimiter:
        with self._lock:
            if key not in self._limiters:
                kw = {"limits": self._limits} if self._limits else {}
                self._limiters[key] = RateLimiter(clock=self.clock, sleep=self.sleep, **kw)
            return self._limiters[key]

    def cooldown(self, key: str) -> float:
        """這組 Key 還要冷卻幾秒；0 代表現在就能用。"""
        return self.limiter(key).cooldown()

    def cooling(self, key: str) -> bool:
        return self.cooldown(key) >= self.switch_after

    def client(self, key: str, switch: Callable[[str, float], str | None] | None = None) -> RiotClient:
        """這組 Key 的客戶端；switch 為 None 時冷卻就等（與只有一組 Key 時相同）。"""
        return RiotClient(key, self.limiter(key), switch=switch, fetch=self._fetch)

    # ─── 挑 Key ───
    def _cycle(self, after: str, include: bool) -> list[str]:
        """可用的 Key，從 after 開始依序循環（include：after 自己排第一個）。after 不在清單裡時從頭開始。"""
        keys = [k.key for k in self._pool()]
        usable = {k.key for k in self._pool() if k.usable}
        if after in keys:
            i = keys.index(after)
            keys = keys[i:] + keys[:i] if include else keys[i + 1:] + keys[:i]
        return [k for k in keys if k in usable]

    def current(self) -> str:
        """使用中的 Key：目前這組沒冷卻就繼續用；冷卻了換下一組沒在冷卻的；全部冷卻時挑最早解除的。
        沒有可用的 Key（全部失效或清單是空的）時回空字串。"""
        with self._lock:
            order = self._cycle(self._current, include=True)
            if not order:
                return ""
            self._current = next((k for k in order if not self.cooling(k)), None) or min(order, key=self.cooldown)
            return self._current

    def set_current(self, key: str):
        with self._lock:
            self._current = key

    def switch_target(self, key: str, wait: float) -> str | None:
        """key 要等 wait 秒（冷卻；失效時是無限久）：該換哪一組？
        有沒在冷卻的就換它（依序循環）；全部冷卻時換最早解除、而且比自己早的；都沒有就回 None，留下來等。"""
        if wait < self.switch_after:
            return None
        with self._lock:
            others = self._cycle(key, include=False)
            if free := next((k for k in others if not self.cooling(k)), None):
                return free
            earliest = min(others, key=self.cooldown, default=None)
            return earliest if earliest and self.cooldown(earliest) < wait else None

    def mark_bad(self, key: str):
        """預設的失效處理：標成失效，之後挑 Key 時跳過。"""
        for k in self._pool():
            if k.key == key:
                k.status = BAD


def key_row_status(entry: ApiKey, active: bool, cooldown: float, switch_after: float = SWITCH_AFTER) -> tuple[str, str]:
    """設定頁 Key 清單每一列的狀態：(文字, 色調)。色調：active、ok、cooling、busy、grey、error。
    cooldown 是這組 Key 還要冷卻幾秒；短於 switch_after 的（每秒限速的短暫等待）不算冷卻。"""
    if entry.status == BAD:
        return "失效，請重新產生", "error"
    if entry.status == WARMING:
        return "新 Key 生效中…", "busy"
    cooling = f"冷卻 {math.ceil(cooldown)} 秒" if cooldown >= switch_after else ""
    if active:
        return ("✓ 使用中・" + cooling, "cooling") if cooling else ("✓ 使用中", "active")
    if cooling:
        return cooling, "cooling"
    return ("驗證中…", "grey") if entry.status == CHECKING else ("✓ 可用", "ok")


def run_rotating(rotation: KeyRotation, job: Callable[[str, RiotClient], T], *,
                 on_restart: Callable[[str], None] = lambda key: None,
                 on_key_error: Callable[[str], None] | None = None) -> T:
    """用使用中的 Key 跑一次 job(key, client)（一次完整的稽查）。

    job 途中 Key 冷卻（本地限速或 429 要等太久）而有別組 Key 可換，或回 401/403（先用 on_key_error 把它標成
    不可用，預設是失效）時，整個 job 用新 Key 從頭重跑，舊 Key 查到的結果都不要（on_restart 通知畫面丟掉）。
    全部冷卻時等最早解除的那一組；只有一組 Key 時冷卻就等，不重跑。
    同一次稽查因冷卻換 Key 最多換「Key 數」次，之後就留下來等：一次稽查的請求數超過單組 Key 的額度時，
    才不會在幾組 Key 之間永遠重跑下去。沒有可用的 Key 時拋 RiotError('key')。
    """
    on_key_error = on_key_error or rotation.mark_bad
    budget = len([k for k in rotation._pool() if k.usable])
    switches = 0

    def switch(key: str, wait: float) -> str | None:
        if wait != math.inf and switches >= budget:
            return None
        return rotation.switch_target(key, wait)

    while True:
        key = rotation.current()
        if not key:
            raise RiotError("key", "沒有可用的 API Key，請重新產生")
        try:
            return job(key, rotation.client(key, switch))
        except KeySwitch as s:
            if s.reason == "bad":
                on_key_error(s.key)
            else:
                switches += 1
            rotation.set_current(s.target)
            on_restart(rotation.current())
