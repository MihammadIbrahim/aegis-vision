from typing import Protocol


class IPasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...

    def verify(self, password_hash: str, password: str) -> bool:
        """True only for a matching password; never raises for a wrong or malformed hash."""
        ...
