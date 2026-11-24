"""Person Search (migration 0028, decision D-024) with synthetic 512-number appearance vectors: worker intake
(only cameras that may run Re-ID), search by an observed person, bands, limitations, review rules (an
uncertain result is never verified), case link, evidence from a result, expiry with the campus retention,
photo search without a model (503), campus isolation and refusals."""

# ruff: noqa: F811  (imported fixtures are used as test parameters)

import base64
import io
import math
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from PIL import Image

from tests.conftest import platform_auth_header
from tests.integration.test_alert_operations import SERVICE, ops_client  # noqa: F401  (a fixture)
from tests.integration.test_alerts import ok, site  # noqa: F401  (site is a fixture)
from tests.integration.test_ownership_and_deletion import count, headers, one, ucp  # noqa: F401

API = "/api/v1"
PS = f"{API}/person-search"


def unit(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    return [v / norm for v in vector]


def near(base: list[float], similarity: float, seed: int) -> list[float]:
    """A unit vector with the given cosine similarity to ``base``."""
    rng = random.Random(seed)
    noise = unit([rng.gauss(0, 1) for _ in base])
    dot = sum(a * b for a, b in zip(noise, base, strict=True))
    orthogonal = unit([n - dot * b for n, b in zip(noise, base, strict=True)])
    side = math.sqrt(max(0.0, 1 - similarity * similarity))
    return [similarity * b + side * o for b, o in zip(base, orthogonal, strict=True)]


def jpeg(color: str = "red") -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 64), color).save(buffer, "JPEG")
    return base64.b64encode(buffer.getvalue()).decode()


async def observe(
    client: AsyncClient, camera: UUID, track: int, vector: list[float], at: datetime | None = None, crop: bool = True
):  # type: ignore[no-untyped-def]
    return await client.post(
        f"{API}/internal/reid/observations",
        json={
            "camera_id": str(camera),
            "track_id": track,
            "observed_at": (at or datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
            "embedding": vector,
            "model_version": "osnet-test-1",
            "quality": 0.9,
            "crop_jpeg_base64": jpeg() if crop else None,
        },
        headers=SERVICE,
    )


async def recorded(client: AsyncClient, camera: UUID, track: int, vector: list[float], **kw: Any) -> str:
    response = await observe(client, camera, track, vector, **kw)
    assert response.status_code == 201, response.text
    observation: str = response.json()["observation_id"]
    return observation


OPT_IN = (
    "INSERT INTO camera_ai_settings (camera_id, organization_id, campus_id, human_detection, person_reid, "
    "weapon_detection, fight_detection, updated_by_name) VALUES (%(c)s, %(o)s, %(k)s, true, true, true, true, 'test')"
)


@pytest.fixture(autouse=True)
def camera_a_runs_reid(site: dict[str, Any], owner_execute) -> None:  # type: ignore[no-untyped-def]
    """Cameras run no AI module until one is selected (D-028): the campus camera is opted in here."""
    owner_execute(OPT_IN, {"c": site["camera_a"], "o": site["org"], "k": site["campus_a"]})


def second_camera(owner_query, site: dict[str, Any]) -> UUID:
    camera: UUID = one(
        owner_query,
        "INSERT INTO cameras (id, organization_id, campus_id, display_name) VALUES (gen_random_uuid(), %(o)s, %(c)s, "
        "'Corridor') RETURNING id",
        o=site["org"],
        c=site["campus_a"],
    )
    one(owner_query, OPT_IN + " RETURNING camera_id", c=camera, o=site["org"], k=site["campus_a"])
    return camera


async def test_search_by_an_observed_person_with_bands_review_case_link_and_evidence(
    ops_client: AsyncClient, site: dict[str, Any], owner_query, storage_root: Path
) -> None:
    client, sa, a = ops_client, site["sa"], site["a"]
    cam1, cam2 = site["camera_a"], second_camera(owner_query, site)
    base = unit([random.Random(1).gauss(0, 1) for _ in range(512)])
    reference = await recorded(client, cam1, 1, base)
    await recorded(client, cam1, 1, near(base, 0.99, 2))  # the same track: never a result
    likely = await recorded(client, cam2, 5, near(base, 0.93, 3))
    uncertain = await recorded(client, cam2, 6, near(base, 0.62, 4))
    await recorded(client, cam2, 7, near(base, 0.10, 5))  # too weak to show

    listed = await ok(client, sa, "GET", "/person-search/observations")
    assert len(listed) == 5 and listed[0]["camera_name"] in ("Gate", "Corridor")
    crop = await client.get(f"{PS}/observations/{reference}/crop", headers=headers(sa))
    assert crop.status_code == 200 and crop.headers["content-type"] == "image/jpeg"

    search = await ok(client, sa, "POST", "/person-search/observation", {"observation_id": reference})
    assert [(c["observation_id"], c["band"]) for c in search["candidates"]] == [
        (likely, "LIKELY_MATCH"),
        (uncertain, "UNCERTAIN"),
    ]
    assert search["searched_observations"] == 3 and search["has_reference"] is True
    assert all(c["review"] == "PENDING" for c in search["candidates"])
    good, weak = search["candidates"]

    refused = await client.post(f"{PS}/results/{weak['id']}/review", json={"review": "VERIFIED_MATCH"},
                                headers=headers(sa))  # fmt: skip
    assert refused.status_code == 409 and refused.json()["code"] == "UNCERTAIN_CANNOT_BE_VERIFIED"
    unverified = await client.post(f"{PS}/results/{good['id']}/incident", json={"incident_id": str(uuid4())},
                                   headers=headers(sa))  # fmt: skip
    assert unverified.status_code == 409 and unverified.json()["code"] == "RESULT_NOT_VERIFIED"
    reviewed = await ok(client, sa, "POST", f"/person-search/results/{good['id']}/review",
                        {"review": "VERIFIED_MATCH", "note": "Same jacket"})  # fmt: skip
    assert reviewed["review"] == "VERIFIED_MATCH" and reviewed["reviewed_by_name"] == sa["name"]
    await ok(client, sa, "POST", f"/person-search/results/{weak['id']}/review", {"review": "NOT_A_MATCH"})

    case = await ok(client, a, "POST", "/incidents", {"title": "Person of interest"})
    linked = await ok(client, sa, "POST", f"/person-search/results/{good['id']}/incident",
                      {"incident_id": case["id"]})  # fmt: skip
    assert linked["incident_reference"] == case["reference"]
    detail = await ok(client, a, "GET", f"/incidents/{case['id']}")
    assert [x["result_id"] for x in detail["person_search"]] == [good["id"]]
    assert "PERSON_SEARCH_LINKED" in [e["kind"] for e in detail["history"]]

    saved = await ok(client, sa, "POST", f"/person-search/results/{good['id']}/evidence",
                     {"title": "Corridor sighting", "tags": ["person-search"]})  # fmt: skip
    evidence = await ok(client, sa, "GET", f"/evidence/{saved['evidence_id']}")
    assert evidence["source"] == "PERSON_SEARCH" and evidence["incident_id"] == case["id"]
    assert evidence["tags"] == ["person-search"] and evidence["camera_name"] == "Corridor"
    again = await client.post(f"{PS}/results/{good['id']}/evidence", json={"title": "x"}, headers=headers(sa))
    assert again.status_code == 409

    result_crop = await client.get(f"{PS}/results/{good['id']}/crop", headers=headers(sa))
    assert result_crop.status_code == 200
    clip = await client.get(f"{PS}/results/{good['id']}/clip", headers=headers(sa))
    assert clip.status_code == 422 and clip.json()["code"] == "NO_RECORDING"  # stated, not hidden
    history = await ok(client, sa, "GET", "/person-search")
    assert [s["id"] for s in history] == [search["id"]]
    actions = {r[0] for r in owner_query("SELECT action FROM organization_audit_logs WHERE category = "
                                         "'PERSON_SEARCH'")}  # fmt: skip
    assert {"PERSON_SEARCH_RUN", "PERSON_SEARCH_REVIEWED", "PERSON_SEARCH_LINKED"} <= actions


async def test_limitations_disabled_cameras_and_expiry(
    ops_client: AsyncClient, site: dict[str, Any], owner_query, owner_execute, storage_root: Path
) -> None:
    client, sa = ops_client, site["sa"]
    quiet = second_camera(owner_query, site)
    owner_execute("INSERT INTO camera_status (camera_id, organization_id, campus_id, state, source) "
                  "VALUES (%(c)s, %(o)s, %(k)s, 'OFFLINE', 'INGESTION')",
                  {"c": quiet, "o": site["org"], "k": site["campus_a"]})  # fmt: skip
    base = unit([random.Random(9).gauss(0, 1) for _ in range(512)])
    reference = await recorded(client, site["camera_a"], 1, base)
    search = await ok(client, sa, "POST", "/person-search/observation", {"observation_id": reference})
    issues = {(x["camera_name"], x["issue"]) for x in search["limitations"]}
    assert ("Corridor", "CAMERA_OFFLINE") in issues and ("Corridor", "NO_OBSERVATIONS") in issues
    assert search["complete"] is False and search["candidates"] == []

    owner_execute(
        "UPDATE camera_ai_settings SET person_reid = false, updated_by_name = 'test' WHERE camera_id = %(c)s",
        {"c": quiet},
    )
    disabled = await observe(client, quiet, 3, base)
    assert disabled.status_code == 409 and disabled.json()["code"] == "REID_DISABLED"
    wrong_size = await client.post(f"{API}/internal/reid/observations", headers=SERVICE,
                                   json={"camera_id": str(quiet), "track_id": 1,
                                         "observed_at": datetime.now(UTC).isoformat(), "embedding": [0.1] * 10,
                                         "model_version": "x"})  # fmt: skip
    assert wrong_size.status_code == 422
    assert (await observe_without_token(client, site["camera_a"], base)).status_code == 401

    # Expiry: one minute of retention removes old observations and their crops; results keep their copies.
    owner_execute(
        "INSERT INTO campus_retention_settings (organization_id, campus_id, retention_value, retention_unit, "
        "updated_by) VALUES (%(o)s, %(c)s, 1, 'MINUTES', %(u)s)",
        {"o": site["org"], "c": site["campus_a"], "u": site["a"]["id"]},
    )
    old = await recorded(client, site["camera_a"], 4, base, at=datetime.now(UTC) - timedelta(minutes=10))
    crop_file = storage_root / "live" / str(site["org"]) / str(site["campus_a"]) / str(site["camera_a"]) / "reid"
    assert (crop_file / f"{old}.jpg").is_file()
    purged = await client.post(f"{API}/internal/reid/purge-expired", headers=SERVICE)
    assert purged.status_code == 200 and purged.json()["deleted"] >= 1
    assert count(owner_query, "SELECT 1 FROM reid_observations WHERE id = %(o)s", o=old) == 0
    assert not (crop_file / f"{old}.jpg").exists()


async def observe_without_token(client: AsyncClient, camera: UUID, vector: list[float]):  # type: ignore[no-untyped-def]
    return await client.post(
        f"{API}/internal/reid/observations",
        json={"camera_id": str(camera), "track_id": 1, "observed_at": datetime.now(UTC).isoformat(),
              "embedding": vector, "model_version": "x"},
    )  # fmt: skip


async def test_photo_search_needs_the_model_and_access_is_limited(
    ops_client: AsyncClient, site: dict[str, Any], super_admin
) -> None:
    client, sa = ops_client, site["sa"]
    status = await ok(client, sa, "GET", "/person-search/model")
    assert status["photo_search_available"] is False and "REID_MODEL_MANIFEST" in status["reason"]
    photo = base64.b64decode(jpeg("blue"))
    response = await client.post(f"{PS}/photo", files={"photo": ("p.jpg", photo, "image/jpeg")}, headers=headers(sa))
    assert response.status_code == 503 and response.json()["code"] == "REID_MODEL_UNAVAILABLE"
    not_photo = await client.post(f"{PS}/photo", files={"photo": ("p.txt", b"hello", "text/plain")},
                                  headers=headers(sa))  # fmt: skip
    assert not_photo.status_code == 422

    base = unit([random.Random(3).gauss(0, 1) for _ in range(512)])
    reference = await recorded(client, site["camera_a"], 1, base)
    search = await ok(client, sa, "POST", "/person-search/observation", {"observation_id": reference})
    assert (await client.get(f"{PS}/{search['id']}", headers=headers(site["b"]))).status_code == 404
    assert (await client.get(f"{PS}/observations/{reference}/crop", headers=headers(site["b"]))).status_code == 404
    assert (await client.get(PS, headers=headers(site["ga"]))).status_code == 403
    assert (await client.get(PS, headers=platform_auth_header(super_admin))).status_code == 403
    too_long = await client.post(f"{PS}/observation", headers=headers(sa), json={
        "observation_id": reference, "since": "2026-01-01T00:00:00Z", "until": "2026-03-01T00:00:00Z"})  # fmt: skip
    assert too_long.status_code == 422 and too_long.json()["code"] == "PERIOD_TOO_LONG"
