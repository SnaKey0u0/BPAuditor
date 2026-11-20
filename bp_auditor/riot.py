"""Riot 官方 API 的最小客戶端（台服 tw2 / match 走 sea），內建開發 Key 的限速。"""
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque

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


class RateLimiter:
    """滑動視窗限速：預設為開發 Key 的 20 次/秒、100 次/120 秒。"""

    def __init__(self, limits=((20, 1.0), (100, 120.0))):
        self._limits = limits
        self._stamps = deque()
        self._lock = threading.Lock()
        self._blocked_until = 0.0  # 因限流而等待到何時（monotonic 秒），給介面顯示倒數

    def wait_remaining(self) -> float:
        """目前還要等多久才能再發請求；0 代表沒有在等。"""
        return max(0.0, self._blocked_until - time.monotonic())

    def block_for(self, seconds: float):
        with self._lock:
            self._blocked_until = max(self._blocked_until, time.monotonic() + seconds)

    def acquire(self):
        while True:
            with self._lock:
                now = time.monotonic()
                longest = max(w for _, w in self._limits)
                while self._stamps and now - self._stamps[0] > longest:
                    self._stamps.popleft()
                wait = 0.0
                for count, window in self._limits:
                    recent = [t for t in self._stamps if now - t < window]
                    if len(recent) >= count:
                        wait = max(wait, window - (now - recent[-count]))
                if wait <= 0 and now >= self._blocked_until:
                    self._stamps.append(now)
                    return
                wait = max(wait, self._blocked_until - now)
                self._blocked_until = max(self._blocked_until, now + wait)
            time.sleep(min(wait, 1.0) + 0.01)


class RiotClient:
    def __init__(self, api_key: str, limiter: RateLimiter | None = None, timeout: float = 10.0):
        self._key = api_key.strip()
        self._limiter = limiter or RateLimiter()
        self._timeout = timeout

    def _get(self, url: str):
        for attempt in range(3):
            self._limiter.acquire()
            req = urllib.request.Request(url, headers={"X-Riot-Token": self._key, "User-Agent": "bp-auditor"})
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    body = e.read()[:200].decode("utf-8", "replace").strip()
                    if "1010" in body:
                        raise RiotError("blocked", f"被 Riot 防火牆擋下（HTTP {e.code} / {body}）") from e
                    raise RiotError("key", f"API Key 無法使用（HTTP {e.code}）。新 Key 有時要 1–2 分鐘才生效。") from e
                if e.code == 404:
                    raise RiotError("not_found", "查無此人") from e
                if e.code == 400:
                    raise RiotError("bad_request", f"Riot 拒絕這個請求（HTTP 400）") from e
                if e.code == 429:  # 伺服器端限流（例如同一把 Key 在別處也在用）
                    self._limiter.block_for(float(e.headers.get("Retry-After", "2")))
                    continue
                if 500 <= e.code < 600 and attempt < 2:
                    time.sleep(1)
                    continue
                raise RiotError("server", f"Riot 伺服器錯誤 {e.code}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < 2:
                    continue
                raise RiotError("network", f"連線失敗：{e}") from e
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
