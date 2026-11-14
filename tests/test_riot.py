import time

from bp_auditor.riot import RateLimiter


def test_limiter_reports_wait_when_window_is_full():
    lim = RateLimiter(limits=((2, 0.3),))
    lim.acquire(); lim.acquire()
    assert lim.wait_remaining() == 0
    t = time.monotonic(); lim.acquire()  # 第 3 次要等視窗滑過
    assert time.monotonic() - t >= 0.25


def test_block_for_delays_next_request_and_is_visible():
    lim = RateLimiter(limits=((100, 1.0),))
    lim.block_for(0.3)
    assert 0.2 < lim.wait_remaining() <= 0.3 + 1e-6  # 同一個時鐘刻度內會有浮點誤差
    t = time.monotonic(); lim.acquire()
    assert time.monotonic() - t >= 0.25
    assert lim.wait_remaining() == 0
