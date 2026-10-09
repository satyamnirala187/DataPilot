"""Tests for the in-memory per-client rate limiter (backend/app/rate_limiter.py). Deterministic: fake clock."""

import threading

from app import rate_limiter as rate_limiter_module
from app.rate_limiter import RateLimiter


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def limiter(max_requests=3, window_seconds=60, clock=None):
    return RateLimiter(max_requests, window_seconds, clock=clock or FakeClock())


def test_allows_up_to_the_limit_then_blocks():
    rl = limiter()
    assert [rl.check("a") for _ in range(3)] == [None, None, None]
    assert rl.check("a") == 60


def test_retry_after_counts_down_to_when_the_oldest_request_expires():
    clock = FakeClock()
    rl = limiter(clock=clock)
    for _ in range(3):
        rl.check("a")
        clock.now += 10  # requests at 1000, 1010, 1020
    assert rl.check("a") == 30  # now 1030; the first expires at 1060


def test_window_slides_and_requests_are_allowed_again():
    clock = FakeClock()
    rl = limiter(clock=clock)
    for _ in range(3):
        rl.check("a")
    clock.now += 60
    assert rl.check("a") is None


def test_blocked_requests_do_not_extend_the_wait():
    clock = FakeClock()
    rl = limiter(max_requests=1, clock=clock)
    rl.check("a")
    for _ in range(5):
        clock.now += 10
        rl.check("a")  # blocked, not recorded
    clock.now = 1060
    assert rl.check("a") is None


def test_each_client_has_its_own_limit():
    rl = limiter(max_requests=1)
    assert rl.check("a") is None
    assert rl.check("a") is not None
    assert rl.check("b") is None


def test_reset_clears_all_counts():
    rl = limiter(max_requests=1)
    rl.check("a")
    rl.reset()
    assert rl.check("a") is None


def test_idle_clients_are_forgotten_when_many_are_tracked(monkeypatch):
    monkeypatch.setattr(rate_limiter_module, "MAX_TRACKED_CLIENTS", 3)
    clock = FakeClock()
    rl = limiter(clock=clock)
    for client in ("a", "b", "c"):
        rl.check(client)
    clock.now += 61
    rl.check("d")  # a, b and c are idle now
    rl.check("e")
    assert set(rl._hits) <= {"d", "e"}


def test_concurrent_requests_never_exceed_the_limit():
    rl = RateLimiter(max_requests=10, window_seconds=60)
    results = []

    def hit():
        results.append(rl.check("same-client"))

    threads = [threading.Thread(target=hit) for _ in range(50)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count(None) == 10
