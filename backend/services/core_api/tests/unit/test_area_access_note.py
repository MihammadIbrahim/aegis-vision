"""The area access note is an Admin note: the use case blanks it for Supervisors (D-016 item 16),
before any response model is chosen."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.application.use_cases.area_access_use_cases import ViewRestrictedAccess
from src.domain.entities.area_access import RestrictedArea
from src.domain.exceptions.domain_errors import PermissionDenied
from src.domain.value_objects.enums import UserRole

AREA = RestrictedArea(uuid4(), "Server Room", uuid4(), "IT Block", None, None, True, "IT staff only", None, 2)


class Areas:
    async def areas(self, *, restricted_only: bool) -> list[RestrictedArea]:
        return [AREA]


def view() -> ViewRestrictedAccess:
    return ViewRestrictedAccess(Areas(), None)  # type: ignore[arg-type]


async def test_admins_keep_the_note() -> None:
    found = await view().areas(SimpleNamespace(role=UserRole.ADMIN), include_unrestricted=False)  # type: ignore[arg-type]
    assert found[0].access_note == "IT staff only"


async def test_supervisors_get_the_area_without_the_note() -> None:
    found = await view().areas(SimpleNamespace(role=UserRole.SUPERVISOR), include_unrestricted=False)  # type: ignore[arg-type]
    assert found[0].access_note is None
    assert (found[0].area_name, found[0].active_grants, found[0].is_restricted) == ("Server Room", 2, True)


async def test_guards_get_nothing() -> None:
    with pytest.raises(PermissionDenied):
        await view().areas(SimpleNamespace(role=UserRole.GUARD), include_unrestricted=False)  # type: ignore[arg-type]
