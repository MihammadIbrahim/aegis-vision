"""A CCTV camera of one campus (requirements Section 13; decision D-012).

- **Camera ID:** the system-generated ``id``.
- **Location:** at most one building, floor or area of the same campus (migration 0013).
  "Indoor" or "outdoor" follows from the building's kind, so it is not stored twice.
- **Stream URL:** optional, so a camera can be registered before the equipment is connected. It
  must never contain a user name or password; no secure credential storage exists yet.
"""

import ipaddress
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlsplit
from uuid import UUID

from src.domain.exceptions.domain_errors import ValidationFailed
from src.domain.value_objects.location_path import LocationPath


class ConnectionType(StrEnum):
    RTSP = "RTSP"
    ONVIF = "ONVIF"


_SCHEMES = {ConnectionType.RTSP: ("rtsp", "rtsps"), ConnectionType.ONVIF: ("http", "https")}
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)
MAX_STREAM_URL = 2048
MAX_DISPLAY_NAME = 120


def clean_display_name(value: str) -> str:
    name = value.strip()
    if not name or len(name) > MAX_DISPLAY_NAME or any(ord(c) < 32 for c in name):
        raise ValidationFailed(f"The display name must be 1 to {MAX_DISPLAY_NAME} characters.", code="INVALID_NAME")
    return name


def clean_stream_url(connection_type: ConnectionType, value: str | None) -> str | None:
    """Checks scheme (by connection type), host or IP address, port, and that no credentials are
    embedded. Returns the trimmed URL, or None when no URL was given."""
    if value is None or not value.strip():
        return None
    url = value.strip()

    def invalid(message: str, code: str = "INVALID_STREAM_URL") -> ValidationFailed:
        return ValidationFailed(message, code=code)

    if len(url) > MAX_STREAM_URL or any(c.isspace() for c in url):
        raise invalid("The stream URL is too long or contains spaces.")
    parts = urlsplit(url)
    allowed = _SCHEMES[connection_type]
    if parts.scheme.lower() not in allowed:
        raise invalid(f"A {connection_type.value} camera needs a {' or '.join(s + '://' for s in allowed)} address.")
    if "@" in parts.netloc:
        raise invalid(
            "Do not put a user name or password in the stream URL. Camera credentials cannot be stored yet.",
            code="STREAM_CREDENTIALS_NOT_ALLOWED",
        )
    host = parts.hostname or ""
    try:
        parts.port  # noqa: B018 - raises ValueError for a bad port
    except ValueError as error:
        raise invalid("The port in the stream URL is not valid.") from error
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not _HOSTNAME.match(host):
            raise invalid("The stream URL needs a valid IP address or host name.") from None
    return url


@dataclass(frozen=True, slots=True)
class Camera:
    id: UUID
    organization_id: UUID
    campus_id: UUID
    display_name: str
    connection_type: ConnectionType = ConnectionType.RTSP
    stream_url: str | None = None
    location: LocationPath | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def environment(self) -> str | None:
        """INDOOR or OUTDOOR, from the linked building or perimeter; None without a location."""
        if self.location is None:
            return None
        return "OUTDOOR" if self.location.building_kind == "OUTDOOR_PERIMETER" else "INDOOR"
