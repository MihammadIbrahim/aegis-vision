from typing import Protocol
from uuid import UUID


class ITransaction(Protocol):
    """The current database transaction, for use cases that must decide what is kept.

    Example: a wrong OTP must be counted (commit) even though the request fails, while a
    failed registration completion must leave nothing behind (rollback).
    """

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class IActorBinding(Protocol):
    """Binds the signed-in user to the database transaction again (Row-Level Security). The binding
    is transaction-local, so a use case that rolls back and then writes again must call this."""

    async def rebind(self, user_id: UUID) -> None: ...
