"""Test data: two organizations; the first has two campuses.

Org 1 "Riverside University"
  Campus A "Main Campus":  Ayesha Khan (First Admin), Sana Iqbal (Supervisor), camera "A - Main Gate"
  Campus B "City Campus":  Bilal Ahmed (Admin), Omar Farooq (Admin, LEFT), Usman Ali (Guard), camera "B - Library"
  Requests: "North Campus" (email verified), "South Campus" (email NOT verified)
Org 2 "Harbor Logistics"
  Campus C "Warehouse 1":  Zara Sheikh (First Admin), Hina Malik (Admin), camera "C - Dock"
  Request: "Warehouse 2" (email verified)
"""

from dataclasses import dataclass, field
from uuid import UUID, uuid4

import psycopg


def _id() -> UUID:
    return uuid4()


@dataclass(frozen=True)
class Seed:
    org1: UUID = field(default_factory=_id)
    org2: UUID = field(default_factory=_id)
    campus_a: UUID = field(default_factory=_id)
    campus_b: UUID = field(default_factory=_id)
    campus_c: UUID = field(default_factory=_id)

    first_admin: UUID = field(default_factory=_id)  # org1, campus A
    supervisor_a: UUID = field(default_factory=_id)
    admin_b: UUID = field(default_factory=_id)
    left_admin_b: UUID = field(default_factory=_id)
    guard_b: UUID = field(default_factory=_id)
    org2_first_admin: UUID = field(default_factory=_id)
    org2_admin: UUID = field(default_factory=_id)

    camera_a: UUID = field(default_factory=_id)
    camera_b: UUID = field(default_factory=_id)
    camera_c: UUID = field(default_factory=_id)

    request_verified: UUID = field(default_factory=_id)
    request_unverified: UUID = field(default_factory=_id)
    request_org2: UUID = field(default_factory=_id)


def insert_seed(conn: psycopg.Connection, s: Seed) -> None:
    cur = conn.cursor()
    conn.execute(
        "INSERT INTO organizations (id, name) VALUES (%s, 'Riverside University'), (%s, 'Harbor Logistics')",
        (s.org1, s.org2),
    )
    cur.executemany(
        "INSERT INTO campuses (id, organization_id, name, status) VALUES (%s, %s, %s, 'ACTIVE')",
        [(s.campus_a, s.org1, "Main Campus"), (s.campus_b, s.org1, "City Campus"), (s.campus_c, s.org2, "Warehouse 1")],
    )
    users = [
        (
            s.first_admin,
            s.org1,
            s.campus_a,
            "ADMIN",
            "ACTIVE",
            "Ayesha Khan",
            "ayesha@riverside.test",
            "ADM-00000000001",
            True,
        ),
        (
            s.supervisor_a,
            s.org1,
            s.campus_a,
            "SUPERVISOR",
            "ACTIVE",
            "Sana Iqbal",
            "sana@riverside.test",
            "SUP-00000000001",
            False,
        ),
        (
            s.admin_b,
            s.org1,
            s.campus_b,
            "ADMIN",
            "ACTIVE",
            "Bilal Ahmed",
            "bilal@riverside.test",
            "ADM-00000000002",
            False,
        ),
        (
            s.left_admin_b,
            s.org1,
            s.campus_b,
            "ADMIN",
            "LEFT",
            "Omar Farooq",
            "omar@riverside.test",
            "ADM-00000000003",
            False,
        ),
        (
            s.guard_b,
            s.org1,
            s.campus_b,
            "GUARD",
            "ACTIVE",
            "Usman Ali",
            "usman@riverside.test",
            "GRD-00000000001",
            False,
        ),
        (
            s.org2_first_admin,
            s.org2,
            s.campus_c,
            "ADMIN",
            "ACTIVE",
            "Zara Sheikh",
            "zara@harbor.test",
            "ADM-00000000004",
            True,
        ),
        (
            s.org2_admin,
            s.org2,
            s.campus_c,
            "ADMIN",
            "ACTIVE",
            "Hina Malik",
            "hina@harbor.test",
            "ADM-00000000005",
            False,
        ),
    ]
    cur.executemany(
        """INSERT INTO users
               (id, organization_id, campus_id, role, status, full_name, email, display_id, is_first_admin)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        users,
    )
    cur.executemany(
        "INSERT INTO cameras (id, organization_id, campus_id, display_name) VALUES (%s, %s, %s, %s)",
        [
            (s.camera_a, s.org1, s.campus_a, "A - Main Gate"),
            (s.camera_b, s.org1, s.campus_b, "B - Library"),
            (s.camera_c, s.org2, s.campus_c, "C - Dock"),
        ],
    )
    cur.executemany(
        """INSERT INTO campus_registration_requests
               (id, organization_id, proposed_campus_name, applicant_name, applicant_email, email_verified, status)
           VALUES (%s, %s, %s, %s, %s, %s, 'PENDING')""",
        [
            (s.request_verified, s.org1, "North Campus", "Hamza Raza", "hamza@riverside.test", True),
            (s.request_unverified, s.org1, "South Campus", "Nida Aslam", "nida@riverside.test", False),
            (s.request_org2, s.org2, "Warehouse 2", "Kamran Butt", "kamran@harbor.test", True),
        ],
    )
