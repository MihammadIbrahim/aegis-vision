from uuid import uuid4

import pytest

from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import PermissionDenied
from src.domain.services.permission_policy import (
    ensure_admin,
    ensure_building_manager,
    ensure_camera_manager,
    ensure_camera_viewer,
    ensure_first_admin,
)
from src.domain.value_objects.enums import AccountStatus, UserRole


def make_user(role: UserRole, *, first_admin: bool = False) -> User:
    return User(
        id=uuid4(),
        organization_id=uuid4(),
        campus_id=uuid4(),
        role=role,
        status=AccountStatus.ACTIVE,
        full_name="Test User",
        email="test@example.test",
        display_id="ADM-00000000001",
        is_first_admin=first_admin,
    )


def test_only_admins_pass_admin_check() -> None:
    ensure_admin(make_user(UserRole.ADMIN))
    for role in (UserRole.SUPERVISOR, UserRole.GUARD):
        with pytest.raises(PermissionDenied):
            ensure_admin(make_user(role))


def test_only_first_admin_passes_first_admin_check() -> None:
    ensure_first_admin(make_user(UserRole.ADMIN, first_admin=True))
    with pytest.raises(PermissionDenied):
        ensure_first_admin(make_user(UserRole.ADMIN))
    with pytest.raises(PermissionDenied):
        ensure_first_admin(make_user(UserRole.SUPERVISOR))


def test_guards_cannot_view_cameras() -> None:
    ensure_camera_viewer(make_user(UserRole.ADMIN))
    ensure_camera_viewer(make_user(UserRole.SUPERVISOR))
    with pytest.raises(PermissionDenied):
        ensure_camera_viewer(make_user(UserRole.GUARD))


def test_only_admins_and_supervisors_manage_cameras() -> None:
    ensure_camera_manager(make_user(UserRole.ADMIN))
    ensure_camera_manager(make_user(UserRole.SUPERVISOR))
    with pytest.raises(PermissionDenied):
        ensure_camera_manager(make_user(UserRole.GUARD))


def test_only_admins_manage_buildings() -> None:
    ensure_building_manager(make_user(UserRole.ADMIN))
    for role in (UserRole.SUPERVISOR, UserRole.GUARD):
        with pytest.raises(PermissionDenied):
            ensure_building_manager(make_user(role))
