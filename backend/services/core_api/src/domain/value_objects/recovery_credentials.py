"""Emergency Super Admin credentials, read from the server environment at startup.

They are never stored in the database, never logged, and never returned. Comparison is
constant-time: both values are hashed to equal-length digests and compared with
``hmac.compare_digest``, and the username and password checks always both run.
"""

import hashlib
import hmac
from dataclasses import dataclass, field


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(hashlib.sha256(a.encode()).digest(), hashlib.sha256(b.encode()).digest())


@dataclass(frozen=True, slots=True)
class RecoveryCredentials:
    username: str
    password: str = field(repr=False)  # kept out of reprs, tracebacks and logs

    def username_matches(self, username: str) -> bool:
        return _same(username.lower(), self.username.lower())

    def matches(self, username: str, password: str) -> bool:
        username_ok = self.username_matches(username)
        password_ok = _same(password, self.password)
        return username_ok & password_ok  # no short-circuit: both comparisons always run
