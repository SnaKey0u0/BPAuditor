"""Riot API 的傳輸層重用 HTTPS 連線（#14）：用假的連線，不真的連網。"""
import gzip
import http.client

import pytest

from bp_auditor.riot import ConnectionPool

URL = "https://sea.api.riotgames.com/lol/match/v5/matches/m1?x=1"


class FakeResponse:
    def __init__(self, status=200, body=b"{}", headers=None, will_close=False):
        self.status, self._body, self.will_close = status, body, will_close
        self._headers = headers or {}

    def read(self):
        return self._body

    def getheaders(self):
        return list(self._headers.items())


class FakeConn:
    """replies：依序回應；元素是例外時 request 就拋出它。"""

    def __init__(self, host, timeout, replies):
        self.host, self.timeout, self.replies = host, timeout, replies
        self.sent, self.closed = [], False

    def request(self, method, path, headers):
        self.sent.append((method, path, headers))
        if isinstance(self.replies[0], BaseException):
            raise self.replies.pop(0)

    def getresponse(self):
        return self.replies.pop(0)

    def close(self):
        self.closed = True


class Factory:
    def __init__(self, *replies_per_conn):
        self.plan, self.made = list(replies_per_conn), []

    def __call__(self, host, timeout):
        conn = FakeConn(host, timeout, self.plan.pop(0))
        self.made.append(conn)
        return conn


def test_同一個host連續請求_重用同一條連線():
    f = Factory([FakeResponse(body=b"1"), FakeResponse(body=b"2")])
    fetch = ConnectionPool(connect=f)

    assert fetch(URL, "K", 10)[2] == b"1"
    assert fetch(URL, "K", 10)[2] == b"2"

    assert len(f.made) == 1
    method, path, headers = f.made[0].sent[0]
    assert (method, path, headers["X-Riot-Token"]) == ("GET", "/lol/match/v5/matches/m1?x=1", "K")


def test_不同host各用各的連線():
    f = Factory([FakeResponse()], [FakeResponse()])
    fetch = ConnectionPool(connect=f)

    fetch(URL, "K", 10)
    fetch("https://asia.api.riotgames.com/riot/account/v1/x", "K", 10)

    assert [c.host for c in f.made] == ["sea.api.riotgames.com", "asia.api.riotgames.com"]


def test_閒置連線被伺服器關掉_換一條重送_使用者看不到錯誤():
    f = Factory([FakeResponse(), http.client.RemoteDisconnected("closed")], [FakeResponse(body=b"ok")])
    fetch = ConnectionPool(connect=f)
    fetch(URL, "K", 10)

    assert fetch(URL, "K", 10)[2] == b"ok"
    assert f.made[0].closed and len(f.made) == 2


def test_新連線就失敗_照樣拋出_交給客戶端重試():
    f = Factory([ConnectionResetError("reset")])
    with pytest.raises(OSError):
        ConnectionPool(connect=f)(URL, "K", 10)


def test_協定錯誤轉成OSError_客戶端當成連線失敗():
    f = Factory([http.client.IncompleteRead(b"")])
    with pytest.raises(OSError):
        ConnectionPool(connect=f)(URL, "K", 10)


def test_伺服器要關的連線不放回去():
    f = Factory([FakeResponse(will_close=True)], [FakeResponse()])
    fetch = ConnectionPool(connect=f)
    fetch(URL, "K", 10)
    fetch(URL, "K", 10)

    assert f.made[0].closed and len(f.made) == 2


def test_gzip回應解壓縮後交出():
    body = gzip.compress(b'{"a": 1}')
    f = Factory([FakeResponse(body=body, headers={"Content-Encoding": "gzip"})])

    code, headers, data = ConnectionPool(connect=f)(URL, "K", 10)

    assert (code, data) == (200, b'{"a": 1}')
    assert f.made[0].sent[0][2]["Accept-Encoding"] == "gzip"
