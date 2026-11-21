import time

from bp_auditor.riot import DEFAULT_RETRY_AFTER, RateLimiter, RiotClient, retry_after_seconds


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


def test_RetryAfter不是秒數時用預設的等待_不會當掉():
    assert retry_after_seconds("30") == 30.0
    assert retry_after_seconds("1.5") == 1.5
    for bad in ("Wed, 21 Oct 2026 07:28:00 GMT", "", "abc", "-5", "nan", "inf"):
        assert retry_after_seconds(bad) == DEFAULT_RETRY_AFTER


def test_429的RetryAfter是HTTP日期時_等預設秒數再重試():
    now, slept = [1000.0], []

    def sleep(s):
        slept.append(s); now[0] += s

    lim = RateLimiter(limits=((100, 1.0),), clock=lambda: now[0], sleep=sleep)
    replies = [(429, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, b"{}"), (200, {}, b"{}")]
    client = RiotClient("RGAPI-x", lim, fetch=lambda url, key, timeout: replies.pop(0))
    client.check_key()
    assert not replies
    assert DEFAULT_RETRY_AFTER <= sum(slept) < DEFAULT_RETRY_AFTER + 1
