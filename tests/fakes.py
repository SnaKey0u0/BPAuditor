"""測試共用的假 Riot（#28）：站在 RiotClient／KeyRotation 的 fetch seam 上，不連網。

模擬 ADR 0002 的「PUUID 依 Key 加密」：同一個帳號在不同 Key 下是不同的 PUUID（`puuid(key, 帳號)`，
例如 `"1111:A"`），用別組 Key 的 PUUID 查詢時 Riot 回 400（Exception decrypting）。
對戰詳情、觀戰資料裡的 PUUID 也一律是發出請求那組 Key 的版本。

    riot = FakeRiot()
    riot.add_player("A", tag="1")                  # 帳號 "A"，Riot ID A#1
    riot.add_match("m0", ["A", "B"], ["C"])         # 也加進每位參與者的對戰 id
    riot.fail(K1, 429, retry_after=60, start=3)     # K1 的第 3 個請求回 429
    client = riot.client(K1)                        # 真的 RiotClient，fetch 是這個假件

每個請求都記在 requests（(key, url)），用 count()／duplicates() 計數。
"""
import json
import threading
import urllib.parse
from collections import Counter

from bp_auditor.riot import RateLimiter, RiotClient
from tests.test_analysis import match

DEFAULT_MASTERY = {"championLevel": 5, "championPoints": 1000, "lastPlayTime": 0}


def key_tag(key: str) -> str:
    """Key 的短代號：最後一段的前 4 碼（RGAPI-1111…-111111111111 → "1111"、RGAPI-test → "test"）。"""
    return key.rsplit("-", 1)[-1][:4]


def puuid(key: str, account: str) -> str:
    """這組 Key 下該帳號的 PUUID。"""
    return f"{key_tag(key)}:{account}"


class FakeRiot:
    """假的 Riot 傳輸層：fetch(url, key, timeout) → (狀態碼, 標頭, 內容)。物件本身也可以直接當 fetch 呼叫。

    accounts：帳號 → 目前的 (名稱, 標籤)；history：帳號 → 對戰 id（新到舊）。
    沒用 add_match 定義的對戰 id，參與者是 history 裡有它的所有帳號，全在同一隊（teamId 100）。
    ranks：帳號 → 牌位（預設 default_ranks）；mastery：帳號 → 熟練度（預設 DEFAULT_MASTERY，None 代表沒玩過 → 404）。
    down：這些 Key 的請求都連線失敗；fail()：某組 Key 的第幾個請求起回指定狀態碼（429／401／403…）。
    """

    def __init__(self):
        self.accounts: dict[str, tuple[str, str]] = {}
        self.history: dict[str, list[str]] = {}
        self.matches: dict[str, tuple[list[str], list[str], dict]] = {}
        self.ranks: dict[str, list[dict]] = {}
        self.default_ranks: list[dict] = []
        self.mastery: dict[str, dict | None] = {}
        self.game: dict | None = None  # 進行中的對局；參與者記帳號（account），回應時換成該 Key 的 PUUID
        self.down: set[str] = set()
        self.trips: dict[str, tuple[int, int, dict, int]] = {}
        self.requests: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    # ─── 準備資料 ───

    def add_player(self, account: str, name: str | None = None, tag: str = "TW", history: list[str] = ()):
        self.accounts[account] = (name or account, tag)
        self.history.setdefault(account, []).extend(history)

    def add_match(self, mid: str, team100: list[str], team200: list[str] = (), **opts):
        """opts 照 tests.test_analysis.match（win100、champ、kda…）。參與者都會拿到這個對戰 id。"""
        self.matches[mid] = (list(team100), list(team200), opts)
        for a in [*team100, *team200]:
            ids = self.history.setdefault(a, [])
            if mid not in ids:
                ids.append(mid)

    def start_game(self, allies: list[str], enemies: list[str], *, hidden_enemy=False, game_id=42,
                   ally_champion=103, enemy_champion=1):
        """有一場進行中的對局（觀戰 API 查得到）；hidden_enemy：對面多一位身分被隱藏的人。"""
        ps = [{"account": a, "riotId": self.riot_id(a), "teamId": 100, "championId": ally_champion} for a in allies]
        ps += [{"account": a, "riotId": self.riot_id(a), "teamId": 200, "championId": enemy_champion}
               for a in enemies]
        if hidden_enemy:
            ps.append({"account": None, "riotId": "Ahri", "teamId": 200, "championId": 103})
        self.game = {"gameId": game_id, "participants": ps}

    def active_game(self, key: str) -> dict | None:
        """觀戰 API 用 key 查到的對局（PUUID 是 key 的版本）；沒有對局時 None。"""
        if self.game is None:
            return None
        ps = [{**{k: v for k, v in p.items() if k != "account"},
               "puuid": puuid(key, p["account"]) if p["account"] else None} for p in self.game["participants"]]
        return {**self.game, "participants": ps}

    def fail(self, key: str, status: int, *, start: int = 1, times: int = 1, retry_after=None, headers=None):
        """key 的第 start 個請求起（從 1 數，含之前所有請求）連續 times 個回 status。"""
        headers = dict(headers or {})
        if retry_after is not None:
            headers["Retry-After"] = str(retry_after)
        self.trips[key] = (start, status, headers, times)

    def puuid(self, key: str, account: str) -> str:
        return puuid(key, account)

    def riot_id(self, account: str) -> str:
        name, tag = self.accounts[account]
        return f"{name}#{tag}"

    def client(self, key: str) -> RiotClient:
        """走這個假件的真 RiotClient，限速放寬到不會等。"""
        return RiotClient(key, RateLimiter(limits=((10 ** 6, 1.0),)), fetch=self.fetch)

    # ─── 計數 ───

    def count(self, fragment: str = "", key: str | None = None) -> int:
        """URL 含 fragment（而且是 key 發出）的請求數。"""
        return sum(fragment in u and (key is None or k == key) for k, u in self.requests)

    @property
    def total(self) -> int:
        return len(self.requests)

    def duplicates(self) -> dict[str, int]:
        return {u: n for u, n in Counter(u for _, u in self.requests).items() if n > 1}

    def clear(self):
        self.requests.clear()

    # ─── 傳輸層 ───

    def __call__(self, url: str, key: str, timeout: float):
        return self.fetch(url, key, timeout)

    def fetch(self, url: str, key: str, timeout: float):
        with self._lock:
            self.requests.append((key, url))
            n = self.count(key=key)
        if key in self.down:
            raise ConnectionError("連線失敗")
        if key in self.trips:
            start, status, headers, times = self.trips[key]
            if start <= n < start + times:
                return status, dict(headers), b"{}"
        u = urllib.parse.urlsplit(url)
        parts = urllib.parse.unquote(u.path).strip("/").split("/")
        query = urllib.parse.parse_qs(u.query)

        if parts[-1] == "platform-data":
            return _ok({})
        if "by-riot-id" in parts:
            name, tag = parts[-2], parts[-1]
            account = next((a for a, (n_, t) in self.accounts.items()
                            if (n_.casefold(), t.casefold()) == (name.casefold(), tag.casefold())), None)
            return _ok({"puuid": puuid(key, account)}) if account else _not_found()
        if "matches" in parts and parts[-1] == "ids":
            account = self._decrypt(key, parts[-2])
            if account is None:
                return _undecryptable()
            count = int(query.get("count", ["20"])[0])
            return _ok(self.history.get(account, [])[:count])
        if "matches" in parts:
            body = self._match(key, parts[-1])
            return _ok(body) if body else _not_found()
        if "spectator" in parts:
            account = self._decrypt(key, parts[-1])
            if account is None:
                return _undecryptable()
            game = self.active_game(key)
            if game and any(p["account"] == account for p in self.game["participants"]):
                return _ok(game)
            return _not_found()
        if "champion-masteries" in parts:
            account = self._decrypt(key, parts[-3])
            if account is None:
                return _undecryptable()
            m = self.mastery.get(account, DEFAULT_MASTERY)
            return _ok(m) if m is not None else _not_found()
        account = self._decrypt(key, parts[-1])
        if account is None:
            return _undecryptable()
        if "accounts" in parts:
            if account not in self.accounts:
                return _not_found()  # 帳號已刪除
            name, tag = self.accounts[account]
            return _ok({"puuid": puuid(key, account), "gameName": name, "tagLine": tag})
        if "summoners" in parts:
            return _ok({"puuid": puuid(key, account), "summonerLevel": 30})
        if "entries" in parts:
            return _ok(self.ranks.get(account, self.default_ranks))
        raise AssertionError(f"沒預期的請求：{url}")

    def _decrypt(self, key: str, pu: str) -> str | None:
        """PUUID → 帳號；不是這組 Key 加密的（Riot 解不開）回 None。"""
        prefix, sep, account = pu.partition(":")
        return account if sep and prefix == key_tag(key) else None

    def _match(self, key: str, mid: str) -> dict | None:
        if mid in self.matches:
            team100, team200, opts = self.matches[mid]
        else:
            team100, team200, opts = [a for a, ids in self.history.items() if mid in ids], [], {}
            if not team100:
                return None
        return match(mid, [puuid(key, a) for a in team100], [puuid(key, a) for a in team200], **opts)


def _ok(data):
    return 200, {}, json.dumps(data).encode()


def _not_found():
    return 404, {}, b'{"status": {"status_code": 404}}'


def _undecryptable():
    return 400, {}, b'{"status": {"message": "Bad Request - Exception decrypting", "status_code": 400}}'
