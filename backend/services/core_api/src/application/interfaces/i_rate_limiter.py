from typing import Protocol


class IRateLimiter(Protocol):
    def hit(self, key: str) -> int | None:
        """Count one request for ``key``. Returns None if allowed, otherwise the number of
        seconds to wait (the refused request is not counted)."""
        ...
