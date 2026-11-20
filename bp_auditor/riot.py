"""Riot 官方 API 的最小客戶端（台服 tw2 / match 走 sea），內建 Key 的限速；冷卻或失效時可交給輪替換 Key（rotation）。"""
import json
import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from typing import Callable

ACCOUNT_HOST = "https://asia.api.riotgames.com"
PLATFORM_HOST = "https://tw2.api.riotgames.com"
MATCH_HOST = "https://sea.api.riotgames.com"
KEY_PAGE = "https://developer.riotgames.com"
_KEY_RE = re.compile(r"RGAPI-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def find_api_key(text: str) -> str | None:
    """從任意文字（例如剪貼簿）中找出 Riot API Key。"""
    m = _KEY_RE.search(text or "")
    return m.group(0) if m else None


class RiotError(Exception):
    """kind: 'key'（Key 無效或過期）、'not_found'、'bad_request'（例如別把 Key 的 PUUID）、'network'、'server'。"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


class KeySwitch(Exception):
    """這組 Key 不能再用、而且有別組 Key 可換：整次稽查要用 target 從頭重跑（ADR 0002）。
    reason：'cooling'（本地限速或 429 要等太久）或 'bad'（回 401/403）。不是 RiotError，稽查裡吞錯誤的地方不會吞掉它。"""

    def __init__(self, key: str, target: str, reason: str, wait: float = 0.0):
        super().__init__(f"{reason}: switch to another key")
        self.key, self.target, self.reason, self.wait = key, target, reason, wait


Fetch = Callable[[str, str, float], tuple[int, dict, bytes]]  # (url, key, timeout) → (HTTP 狀態碼, 標頭, 內容)


def urllib_fetch(url: str, key: str, timeout: float) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, headers={"X-Riot-Token": key, "User-Agent": "bp-auditor"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()


DEFAULT_RETRY_AFTER = 2.0  # 429 沒給 Retry-After，或給的不是秒數（例如 HTTP 日期）時先等這麼久


def retry_after_seconds(value: str) -> float:
    """429 的 Retry-After 標頭 → 要等幾秒。Riot 給的是秒數；空的、HTTP 日期等讀不懂的值用預設值，不讓稽查當掉。"""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER
    return seconds if math.isfinite(seconds) and seconds >= 0 else DEFAULT_RETRY_AFTER


class RateLimiter:
    """滑動視窗限速：預設為 Key 的 20 次/秒、100 次/120 秒。每組 Key 各一個（見 rotation.KeyRotation）。

    clock／sleep 可換成假的，測試不必真的等。
    """

    def __init__(self, limits=((20, 1.0), (100, 120.0)), *, clock=time.monotonic, sleep=time.sleep):
        self._limits = limits
        self._stamps = deque()
        self._lock = threading.Lock()
        self._blocked_until = 0.0  # 因限流而等待到何時（clock 秒），給介面顯示倒數
        self.clock, self.sleep = clock, sleep

    def wait_remaining(self) -> float:
        """目前有請求正在等多久；0 代表沒有在等。給畫面顯示倒數。"""
        return max(0.0, self._blocked_until - self.clock())

    def block_for(self, seconds: float):
        """seconds 秒內不發請求，並給畫面顯示倒數：Riot 回 429（Retry-After），或本地限速有請求要等。"""
        with self._lock:
            self._blocked_until = max(self._blocked_until, self.clock() + seconds)

    def _wait(self, now: float) -> float:
        longest = max(w for _, w in self._limits)
        while self._stamps and now - self._stamps[0] > longest:
            self._stamps.popleft()
        wait = max(0.0, self._blocked_until - now)
        for count, window in self._limits:
            recent = [t for t in self._stamps if now - t < window]
            if len(recent) >= count:
                wait = max(wait, window - (now - recent[-count]))
        return wait

    def cooldown(self) -> float:
        """冷卻：下一個請求還要等多久才能發（本地限速或 429）；0 代表現在就能發。"""
        with self._lock:
            return self._wait(self.clock())

    def try_acquire(self) -> float:
        """能發就記下這次請求並回 0；不能發就回還要等幾秒，不睡。"""
        with self._lock:
            now = self.clock()
            wait = self._wait(now)
            if wait <= 0:
                self._stamps.append(now)
            return wait

    def acquire(self):
        while (wait := self.try_acquire()) > 0:
            self.block_for(wait)
            self.sleep(min(wait, 1.0) + 0.01)


class RiotClient:
    """switch：冷卻（要等 wait 秒）或失效（wait 為無限大）時問要不要換 Key，回要換成哪一組或 None（留下來等）。
    沒給 switch 時冷卻就等，與只有一組 Key 時相同。fetch 可換成假的傳輸層。"""

    def __init__(self, api_key: str, limiter: RateLimiter | None = None, timeout: float = 10.0, *,
                 switch: Callable[[str, float], str | None] | None = None, fetch: Fetch | None = None):
        self._key = api_key.strip()
        self._limiter = limiter or RateLimiter()
        self._timeout = timeout
        self._switch, self._fetch = switch, fetch or urllib_fetch
        self._abandoned: KeySwitch | None = None  # 換過 Key 之後，這個客戶端的其他請求（其他執行緒）也立刻放棄

    def _give_up(self, reason: str, wait: float) -> None:
        """有別組 Key 可換就拋 KeySwitch；沒得換就回去照舊（等，或拋原本的錯誤）。"""
        target = self._switch(self._key, wait) if self._switch and not self._abandoned else None
        if target:
            self._abandoned = KeySwitch(self._key, target, reason, wait)
        if self._abandoned:
            raise self._abandoned

    def _acquire(self):
        while True:
            if self._abandoned:
                raise self._abandoned
            wait = self._limiter.try_acquire()
            if wait <= 0:
                return
            self._give_up("cooling", wait)
            self._limiter.block_for(wait)
            self._limiter.sleep(min(wait, 1.0) + 0.01)

    def _get(self, url: str):
        for attempt in range(3):
            self._acquire()
            try:
                code, headers, body = self._fetch(url, self._key, self._timeout)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                if attempt < 2:
                    continue
                raise RiotError("network", f"連線失敗：{e}") from e
            if 200 <= code < 300:
                return json.loads(body.decode("utf-8"))
            if code in (401, 403):
                text = body[:200].decode("utf-8", "replace").strip()
                if "1010" in text:
                    raise RiotError("blocked", f"被 Riot 防火牆擋下（HTTP {code} / {text}）")
                self._give_up("bad", math.inf)
                raise RiotError("key", f"API Key 無法使用（HTTP {code}）。新 Key 有時要 1–2 分鐘才生效。")
            if code == 404:
                raise RiotError("not_found", "查無此人")
            if code == 400:
                raise RiotError("bad_request", f"Riot 拒絕這個請求（HTTP 400）")
            if code == 429:  # 伺服器端限流（例如同一把 Key 在別處也在用）：冷卻到 Retry-After
                retry = next((v for k, v in headers.items() if k.lower() == "retry-after"), "")
                self._limiter.block_for(retry_after_seconds(retry))
                continue
            if 500 <= code < 600 and attempt < 2:
                self._limiter.sleep(1)
                continue
            raise RiotError("server", f"Riot 伺服器錯誤 {code}")
        raise RiotError("server", "請求太多，稍後再試")

    def check_key(self):
        """打一次最便宜的端點確認 Key 可用；不可用時拋 RiotError。"""
        self._get(f"{PLATFORM_HOST}/lol/status/v4/platform-data")

    def account_puuid(self, name: str, tag: str) -> str:
        q = lambda s: urllib.parse.quote(s, safe="")
        return self._get(f"{ACCOUNT_HOST}/riot/account/v1/accounts/by-riot-id/{q(name)}/{q(tag)}")["puuid"]

    def riot_id_of(self, puuid: str) -> tuple[str, str]:
        """PUUID → 目前的（遊戲名稱, 標籤）；玩家改名後 PUUID 不變。"""
        a = self._get(f"{ACCOUNT_HOST}/riot/account/v1/accounts/by-puuid/{puuid}")
        return a["gameName"], a["tagLine"]

    def summoner_level(self, puuid: str) -> int:
        return self._get(f"{PLATFORM_HOST}/lol/summoner/v4/summoners/by-puuid/{puuid}")["summonerLevel"]

    def match_ids(self, puuid: str, count: int) -> list[str]:
        return self._get(f"{MATCH_HOST}/lol/match/v5/matches/by-puuid/{puuid}/ids?start=0&count={count}")

    def match(self, match_id: str) -> dict:
        return self._get(f"{MATCH_HOST}/lol/match/v5/matches/{match_id}")

    def league_entries(self, puuid: str) -> list[dict]:
        return self._get(f"{PLATFORM_HOST}/lol/league/v4/entries/by-puuid/{puuid}")

    def champion_mastery(self, puuid: str, champion_key: int) -> dict | None:
        """沒玩過這隻英雄時 Riot 回 404，這裡回 None。"""
        try:
            return self._get(f"{PLATFORM_HOST}/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}"
                             f"/by-champion/{champion_key}")
        except RiotError as e:
            if e.kind == "not_found":
                return None
            raise

    def active_game(self, puuid: str) -> dict | None:
        """本人進行中的對局（載入畫面開始才有）；不在遊戲中時回 None。"""
        try:
            return self._get(f"{PLATFORM_HOST}/lol/spectator/v5/active-games/by-summoner/{puuid}")
        except RiotError as e:
            if e.kind == "not_found":
                return None
            raise
