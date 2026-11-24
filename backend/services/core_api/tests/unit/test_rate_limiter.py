import pytest

from src.infrastructure.security.rate_limiter import SlidingWindowRateLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_allows_up_to_the_limit_then_says_how_long_to_wait() -> None:
    clock = Clock()
    limiter = SlidingWindowRateLimiter(3, 60, clock=clock)
    assert [limiter.hit("a") for _ in range(3)] == [None, None, None]
    clock.now += 10
    assert limiter.hit("a") == 50


def test_window_slides_and_refused_requests_are_not_counted() -> None:
    clock = Clock()
    limiter = SlidingWindowRateLimiter(2, 60, clock=clock)
    limiter.hit("a")
    clock.now += 30
    limiter.hit("a")
    for _ in range(5):
        assert limiter.hit("a") is not None
    clock.now += 31  # the first hit has left the window
    assert limiter.hit("a") is None
    assert limiter.hit("a") is not None


def test_keys_are_counted_separately() -> None:
    limiter = SlidingWindowRateLimiter(1, 60, clock=Clock())
    assert limiter.hit("10.0.0.1") is None
    assert limiter.hit("10.0.0.2") is None
    assert limiter.hit("10.0.0.1") is not None


def test_memory_stays_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.infrastructure.security.rate_limiter._MAX_KEYS", 10)
    clock = Clock()
    limiter = SlidingWindowRateLimiter(5, 60, clock=clock)
    for i in range(100):
        limiter.hit(f"client-{i}")
    assert len(limiter._hits) <= 10


def test_invalid_configuration_is_refused() -> None:
    with pytest.raises(ValueError):
        SlidingWindowRateLimiter(0, 60)
