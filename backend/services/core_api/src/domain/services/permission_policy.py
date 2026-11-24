"""Role rules. Campus/organization isolation is enforced separately by the
database (Row-Level Security) and by scoping every query to the caller."""

from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import PermissionDenied
from src.domain.value_objects.enums import UserRole


def ensure_admin(actor: User) -> None:
    if actor.role is not UserRole.ADMIN:
        raise PermissionDenied("Only Admins can perform this action.")


def ensure_first_admin(actor: User) -> None:
    ensure_admin(actor)
    if not actor.is_first_admin:
        raise PermissionDenied("Only the organization's First Admin can review campus registration requests.")


def ensure_camera_viewer(actor: User) -> None:
    if actor.role not in (UserRole.ADMIN, UserRole.SUPERVISOR):
        raise PermissionDenied("Only Admins and Supervisors can view cameras.")


def ensure_location_staff(actor: User) -> None:
    """Admins and Supervisors work with their own campus's floors, areas and location links."""
    if actor.role not in (UserRole.ADMIN, UserRole.SUPERVISOR):
        raise PermissionDenied("Only Admins and Supervisors can manage locations.")


def ensure_building_manager(actor: User) -> None:
    """Buildings and outdoor perimeters are created, changed and removed by Admins only."""
    if actor.role is not UserRole.ADMIN:
        raise PermissionDenied(
            "Only an Admin can add, change or remove buildings and outdoor perimeters.", code="ADMIN_ONLY"
        )


def ensure_camera_manager(actor: User) -> None:
    """Admins and Supervisors fully manage their own campus's cameras (decision D-012)."""
    if actor.role not in (UserRole.ADMIN, UserRole.SUPERVISOR):
        raise PermissionDenied("Only Admins and Supervisors can manage cameras.")


def ensure_personnel_manager(actor: User) -> None:
    """Personnel records, categories, custom fields, imports and restricted-area access are
    created and changed by the campus's Organization Admins only (decision D-016)."""
    if actor.role is not UserRole.ADMIN:
        raise PermissionDenied(
            "Only an Organization Admin can manage personnel and access permissions.", code="PERSONNEL_ADMIN_ONLY"
        )


def ensure_personnel_viewer(actor: User) -> None:
    """Admins and Supervisors search the campus personnel directory (Supervisors: basic details
    only). Guards have no directory access (decision D-016)."""
    if actor.role not in (UserRole.ADMIN, UserRole.SUPERVISOR):
        raise PermissionDenied("Only Admins and Supervisors can view personnel.", code="PERSONNEL_STAFF_ONLY")


def ensure_staff_manager(actor: User) -> None:
    """Admins and Supervisors view and update Guards of their own campus (decision D-017)."""
    if actor.role not in (UserRole.ADMIN, UserRole.SUPERVISOR):
        raise PermissionDenied("Only Admins and Supervisors can manage Guards.", code="STAFF_ONLY")


def ensure_staff_admin(actor: User) -> None:
    """Only Admins create Guards and Supervisors, see Supervisors, and deactivate or reactivate
    accounts (decision D-017)."""
    if actor.role is not UserRole.ADMIN:
        raise PermissionDenied("Only an Organization Admin can do this.", code="ADMIN_ONLY")
