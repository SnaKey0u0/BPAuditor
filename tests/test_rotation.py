"""Key 冷卻自動輪替，換 Key 時整次稽查重跑（#11，ADR 0002）。

全部用假時鐘（FakeClock）與共用的假 Riot（tests.fakes.FakeRiot），不發真的請求、不真的睡。
FakeRiot 模擬「PUUID 依 Key 加密」：同一個帳號在不同 Key 下拿到不同的 PUUID。
"""
import threading

import pytest

from bp_auditor.audit import Audit, Me
from bp_auditor.parsing import RiotId
from bp_auditor.keys import BAD, CHECKING, OK, UNVERIFIED, WARMING, ApiKey, KeyPool
from bp_auditor.riot import RateLimiter, RiotError
from bp_auditor.rotation import KeyRotation, key_row_status, run_rotating
from tests.fakes import FakeRiot, key_tag

K1 = "RGAPI-11111111-1111-1111-1111-111111111111"
K2 = "RGAPI-22222222-2222-2222-2222-222222222222"
K3 = "RGAPI-33333333-3333-3333-3333-333333333333"
K4 = "RGAPI-44444444-4444-4444-4444-444444444444"


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.slept = 0.0
        self._lock = threading.Lock()

    def __call__(self) -> float:
        return self.now

    def sleep(self, s: float):
        with self._lock:
            self.now += s; self.slept += s


def room_world() -> FakeRiot:
    """A、B 一直同隊（可能多排），C 是對手；共 4 場。"""
    riot = FakeRiot()
    for n in "ABC":
        riot.add_player(n, tag="1")
    for i in range(4):
        riot.add_match(f"m{i}", ["A", "B"], ["C"])
    return riot


def test_限速器_額滿時回報要等多久_不會自己睡():
    clock = FakeClock()
    lim = RateLimiter(limits=((2, 10.0),), clock=clock, sleep=clock.sleep)
    assert lim.try_acquire() == 0 and lim.try_acquire() == 0
    assert lim.try_acquire() == 10.0
    assert lim.cooldown() == 10.0
    assert clock.slept == 0
    clock.now += 4
    assert lim.cooldown() == 6.0


def test_限速器_429的RetryAfter也算冷卻():
    clock = FakeClock()
    lim = RateLimiter(limits=((100, 1.0),), clock=clock, sleep=clock.sleep)
    lim.block_for(30)
    assert lim.cooldown() == 30 and lim.try_acquire() == 30


def pool_of(*keys, bad=()) -> KeyPool:
    return KeyPool([ApiKey(k, 0.0, BAD if k in bad else OK) for k in keys])


def rotation_of(pool, clock=None, **kw) -> KeyRotation:
    clock = clock or FakeClock()
    return KeyRotation(pool, clock=clock, sleep=clock.sleep, **kw)


def test_輪替_一直用目前的Key_冷卻才依序換下一組_跳過失效與冷卻中的():
    clock = FakeClock()
    rot = rotation_of(pool_of(K1, K2, K3, K4, bad=[K2]), clock)
    assert rot.current() == K1
    assert rot.current() == K1  # 沒冷卻就不換
    rot.limiter(K1).block_for(60)
    assert rot.current() == K3  # K2 失效，跳過
    rot.limiter(K3).block_for(120); rot.limiter(K4).block_for(120)
    clock.now += 61  # K1 冷卻結束
    assert rot.current() == K1  # K4 也在冷卻：繞回 K1


def test_輪替_冷卻中換Key的目標_有空的就換_全部冷卻就挑最早解除的():
    clock = FakeClock()
    rot = rotation_of(pool_of(K1, K2, K3), clock)
    rot.limiter(K2).block_for(30)
    assert rot.switch_target(K1, 50) == K3  # K2 冷卻中，換有空的 K3
    rot.limiter(K3).block_for(10)
    assert rot.switch_target(K1, 50) == K3  # 全部冷卻：K3 最早解除
    assert rot.switch_target(K1, 5) is None  # 自己比別人都早解除：留下來等


def test_輪替_要等不到換Key門檻的限速不換Key():
    """要等不到 switch_after（2 秒）不算冷卻：每秒 20 次的限速幾乎每次稽查都會碰到、只要等不到 1 秒，
    為這個換 Key 重跑只會白費請求。等到門檻以上才換。"""
    rot = rotation_of(pool_of(K1, K2))
    assert rot.switch_after == 2.0
    assert rot.switch_target(K1, 0.8) is None
    assert rot.switch_target(K1, 1.99) is None
    assert rot.switch_target(K1, 2.0) == K2


def test_輪替_可用的Key數_驗證中與未能驗證的也算():
    pool = KeyPool([ApiKey(K1, 0.0, OK), ApiKey(K2, 0.0, CHECKING), ApiKey(K3, 0.0, UNVERIFIED),
                    ApiKey(K4, 0.0, BAD)])
    rot = rotation_of(pool)
    assert rot.usable_count() == 3
    pool.remove(K1)
    assert rot.usable_count() == 2  # 每次都看目前的清單


def test_輪替_走訪的是Key清單的複本_背景執行緒挑Key時介面刪除Key不會出錯():
    pool = pool_of(K1, K2, K3)

    class RemovingPool(KeyPool):
        def snapshot(self):
            snap = super().snapshot()
            self.remove(K1)  # 模擬介面執行緒在背景走訪清單途中刪掉 Key
            return snap

    removing = RemovingPool(list(pool))
    rot = rotation_of(removing)
    assert rot.current() == K1  # 依取到的複本挑 Key
    assert [k.key for k in removing] == [K2, K3]
    assert rot.current() == K2  # 下一次取新的複本：K1 已經不在


def test_輪替_只有一組Key時沒得換():
    rot = rotation_of(pool_of(K1))
    assert rot.switch_target(K1, 100) is None


def test_輪替_全部失效或沒有Key時沒有使用中的Key():
    assert rotation_of(pool_of(K1, K2, bad=[K1, K2])).current() == ""
    assert rotation_of(KeyPool()).current() == ""


ROOM = [RiotId("A", "1"), RiotId("B", "1"), RiotId("C", "1")]


class Run:
    """跑一次會輪替的房間稽查；記下每次重跑、最後一輪的結果。"""

    def __init__(self, pool, *, clock=None, limits=None, riot=None):
        self.clock, self.riot = clock or FakeClock(), riot or room_world()
        self.rot = KeyRotation(pool, clock=self.clock, sleep=self.clock.sleep, fetch=self.riot, limits=limits)
        self.jobs: list[str] = []
        self.restarts: list[str] = []
        self.players, self.outcome = [], None

    def __call__(self):
        def job(key, client):
            self.jobs.append(key)
            assert len(self.jobs) < 10, "重跑停不下來"
            Audit(client, ROOM, Me(), 4, on_player=self.players.append,
                  on_outcome=lambda o: setattr(self, "outcome", o)).execute()
            return key

        def restart(key):
            self.restarts.append(key)
            self.players, self.outcome = [], None  # 舊 Key 的結果整個丟掉

        return run_rotating(self.rot, job, on_restart=restart)

    def puuids(self) -> set[str]:
        """最終結果裡出現的所有 PUUID（卡片、近期戰績、近期戰績裡的每一位參與者）。"""
        found = {p.puuid for p in self.players}
        for pu, matches in self.outcome.histories.values():
            found.add(pu)
            found |= {p["puuid"] for m in matches for p in m["info"]["participants"]}
        return found


def test_目前的Key回429時_換下一組從頭重跑_最終結果的PUUID都出自同一組Key():
    run = Run(pool_of(K1, K2))
    run.riot.fail(K1, 429, retry_after=60, start=3)
    assert run() == K2
    assert run.jobs == [K1, K2] and run.restarts == [K2]
    assert {p.error for p in run.players} == {None} and len(run.players) == 3
    assert {pu.split(":")[0] for pu in run.puuids()} == {key_tag(K2)}
    assert [g.members for g in run.outcome.premades] == [["A#1", "B#1"]]
    assert run.rot.current() == K2  # 之後繼續用 K2，不切回去
    assert run.clock.slept == 0


def test_本地限速要等超過換Key門檻時_換下一組_不會先等待():
    """120 秒的視窗額滿、要等遠超過 switch_after（2 秒）：算冷卻，直接換 Key 重跑，不先等。"""
    run = Run(pool_of(K1, K2), limits=((20, 120.0),))
    for _ in range(15):  # K1 之前已經用掉 15 次，這次稽查（約 12 次）用到一半就額滿
        run.rot.limiter(K1).try_acquire()
    assert run() == K2
    assert run.restarts == [K2]
    assert run.clock.slept == 0
    assert {pu.split(":")[0] for pu in run.puuids()} == {key_tag(K2)}


def test_稽查中回401的Key標成失效_換下一組重跑():
    pool = pool_of(K1, K2, K3)
    run = Run(pool)
    run.riot.fail(K1, 401, start=2, times=99)
    assert run() == K2
    assert pool.get(K1).status == BAD
    assert {pu.split(":")[0] for pu in run.puuids()} == {key_tag(K2)}


def test_輪替_依序循環_跳過失效與冷卻中的Key():
    pool = pool_of(K1, K2, K3, K4, bad=[K2])
    run = Run(pool)
    run.rot.limiter(K3).block_for(300)
    run.riot.fail(K1, 429, retry_after=60, start=3)
    assert run() == K4  # K2 失效、K3 冷卻中
    assert run.restarts == [K4]


def test_全部冷卻時_等待最早解除的那一組():
    run = Run(pool_of(K1, K2, K3))
    run.rot.limiter(K2).block_for(30)
    run.rot.limiter(K3).block_for(90)
    run.riot.fail(K1, 429, retry_after=60, start=3)
    assert run() == K2  # K2 比 K1 的 60 秒早解除
    assert run.restarts == [K2]
    assert 30 <= run.clock.slept < 60
    assert {pu.split(":")[0] for pu in run.puuids()} == {key_tag(K2)}


def test_開始稽查時全部冷卻_直接用最早解除的那一組_不重跑():
    run = Run(pool_of(K1, K2))
    run.rot.limiter(K1).block_for(60)
    run.rot.limiter(K2).block_for(20)
    assert run() == K2
    assert run.restarts == []
    assert run.clock.slept >= 20


def test_全部失效或沒有Key時_拋出Key錯誤():
    for pool in (pool_of(K1, K2, bad=[K1, K2]), KeyPool()):
        with pytest.raises(RiotError) as e:
            Run(pool)()
        assert e.value.kind == "key"


def test_只有一組Key時_冷卻就等待_不會重跑():
    run = Run(pool_of(K1))
    run.riot.fail(K1, 429, retry_after=60, start=3)
    assert run() == K1
    assert run.jobs == [K1] and run.restarts == []
    assert run.clock.slept >= 60
    assert {p.error for p in run.players} == {None}


def test_唯一可用的Key回401時_照舊回報Key錯誤_不重跑():
    run = Run(pool_of(K1, K2, bad=[K2]))
    run.riot.fail(K1, 401, start=1, times=99)
    run()
    assert run.restarts == []
    assert {p.error.kind for p in run.players} == {"key"}


def test_一次稽查超過單組Key的額度時_換過每組Key就留下來等_不會永遠重跑():
    run = Run(pool_of(K1, K2), limits=((5, 120.0),))  # 這次稽查要約 13 次請求
    key = run()
    assert len(run.restarts) <= 2
    assert {p.error for p in run.players} == {None}
    assert {pu.split(":")[0] for pu in run.puuids()} == {key_tag(key)}


def test_設定頁每一列的狀態_使用中與冷卻倒數():
    ok = ApiKey(K1, 0.0, OK)
    assert key_row_status(ok, active=True, cooldown=0) == ("✓ 使用中", "active")
    assert key_row_status(ok, active=False, cooldown=0) == ("✓ 可用", "ok")
    assert key_row_status(ok, active=False, cooldown=37.2) == ("冷卻 38 秒", "cooling")
    assert key_row_status(ok, active=True, cooldown=37.2) == ("✓ 使用中・冷卻 38 秒", "cooling")  # 全部冷卻，等它
    assert key_row_status(ok, active=True, cooldown=0.6) == ("✓ 使用中", "active")  # 每秒限速的短暫等待不算冷卻
    assert key_row_status(ApiKey(K1, 0.0, BAD), active=False, cooldown=37) == ("失效，請重新產生", "error")
    assert key_row_status(ApiKey(K1, 0.0, CHECKING), active=False, cooldown=0) == ("驗證中…", "grey")
    assert key_row_status(ApiKey(K1, 0.0, UNVERIFIED), active=False, cooldown=0) == ("⚠ 未能驗證", "warn")
    assert key_row_status(ApiKey(K1, 0.0, UNVERIFIED), active=True, cooldown=0) == ("✓ 使用中", "active")
    assert key_row_status(ApiKey(K1, 0.0, WARMING), active=False, cooldown=0) == ("新 Key 生效中…", "busy")
