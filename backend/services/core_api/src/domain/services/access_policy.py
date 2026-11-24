"""Whether a person may be in a restricted area at a given time (decision D-016).

This is the rule a future identity-verification module (face recognition, or another approved
method) will call. It never invents an identity:

* No reliably identified person → ``UNKNOWN_IDENTITY``. An event about an unknown or uncertain
  person stays unidentified and goes to human review; it is never reported as a named person
  entering without permission. Being absent from the personnel list is not evidence either.
* A known person → ``AUTHORIZED`` or the specific reason they are not.

``may_report_unauthorized_entry`` says whether a decision, by itself, supports an
"unauthorized entry by <person>" alert; it is True only for a verified identity.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from src.domain.entities.area_access import AccessGrant, GrantStatus


class AccessDecision(StrEnum):
    AUTHORIZED = "AUTHORIZED"
    UNKNOWN_IDENTITY = "UNKNOWN_IDENTITY"  # nobody reliably identified
    AREA_NOT_RESTRICTED = "AREA_NOT_RESTRICTED"
    PERSONNEL_INACTIVE = "PERSONNEL_INACTIVE"
    NO_PERMISSION = "NO_PERMISSION"
    NOT_YET_VALID = "NOT_YET_VALID"
    EXPIRED = "EXPIRED"
    OUTSIDE_SCHEDULE = "OUTSIDE_SCHEDULE"


@dataclass(frozen=True, slots=True)
class AccessResult:
    decision: AccessDecision
    grant_id: UUID | None = None

    @property
    def authorized(self) -> bool:
        return self.decision is AccessDecision.AUTHORIZED


_UNAUTHORIZED = {
    AccessDecision.PERSONNEL_INACTIVE,
    AccessDecision.NO_PERMISSION,
    AccessDecision.NOT_YET_VALID,
    AccessDecision.EXPIRED,
    AccessDecision.OUTSIDE_SCHEDULE,
}


def may_report_unauthorized_entry(result: AccessResult, *, identity_verified: bool) -> bool:
    """True only when a *verified* person is not authorized. An unverified or unknown identity
    never supports a claim about a named person."""
    return identity_verified and result.decision in _UNAUTHORIZED


def evaluate_access(
    *,
    personnel_status: str | None,
    area_restricted: bool,
    grants: Sequence[AccessGrant],
    at: datetime,
) -> AccessResult:
    """``personnel_status`` is None when no person was identified. ``grants``: this person's
    grants for this area (any status)."""
    if at.tzinfo is None:
        raise ValueError("the time must be timezone-aware")
    if personnel_status is None:
        return AccessResult(AccessDecision.UNKNOWN_IDENTITY)
    if not area_restricted:
        return AccessResult(AccessDecision.AREA_NOT_RESTRICTED)
    if personnel_status != "ACTIVE":
        return AccessResult(AccessDecision.PERSONNEL_INACTIVE)
    active = [g for g in grants if g.status is GrantStatus.ACTIVE]
    if not active:
        return AccessResult(AccessDecision.NO_PERMISSION)
    # The most permissive active grant decides (normally there is only one).
    best = AccessResult(AccessDecision.NO_PERMISSION)
    order = [
        AccessDecision.NO_PERMISSION,
        AccessDecision.EXPIRED,
        AccessDecision.NOT_YET_VALID,
        AccessDecision.OUTSIDE_SCHEDULE,
        AccessDecision.AUTHORIZED,
    ]
    for grant in active:
        if at < grant.valid_from:
            result = AccessResult(AccessDecision.NOT_YET_VALID, grant.id)
        elif grant.valid_until is not None and at >= grant.valid_until:
            result = AccessResult(AccessDecision.EXPIRED, grant.id)
        elif grant.schedule is not None and not grant.schedule.allows(at):
            result = AccessResult(AccessDecision.OUTSIDE_SCHEDULE, grant.id)
        else:
            return AccessResult(AccessDecision.AUTHORIZED, grant.id)
        if order.index(result.decision) > order.index(best.decision):
            best = result
    return best
