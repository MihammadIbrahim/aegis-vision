"""Who an Admin or Supervisor adds to an alert when escalating it (requirements 15.4)."""

from dataclasses import dataclass, field

from src.domain.entities.alert import EscalationScope
from src.domain.entities.location import LocationSelection


@dataclass(frozen=True, slots=True)
class EscalationRequest:
    scope: EscalationScope
    location: LocationSelection | None = None
    guard_ids: list[str] = field(default_factory=list)
    note: str | None = None
