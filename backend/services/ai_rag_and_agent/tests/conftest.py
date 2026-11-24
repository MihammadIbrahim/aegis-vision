from datetime import UTC, datetime

import pytest

from assistant_engine.types import CameraRef, LocationRef, Vocabulary

NOW = datetime(2026, 11, 19, 10, 30, tzinfo=UTC)  # a Thursday


@pytest.fixture
def vocab() -> Vocabulary:
    return Vocabulary(
        now=NOW,
        time_zone="Asia/Karachi",
        role="ADMIN",
        locations=(
            LocationRef("BUILDING", "b-admin", "Admin Block"),
            LocationRef("BUILDING", "b-lib", "Library"),
            LocationRef("BUILDING", "b-fence", "North Fence"),
            LocationRef("FLOOR", "f-ground", "Admin Block › Ground"),
            LocationRef("AREA", "a-lobby", "Admin Block › Ground › Lobby"),
            LocationRef("AREA", "a-gate", "North Fence › Main Gate"),
        ),
        cameras=(CameraRef("c-gate", "Gate Camera"), CameraRef("c-lobby", "Lobby Cam")),
        report_types=("Suspicious Person", "Property Damage"),
        evidence_tags=("weapon", "gate-breach"),
    )
