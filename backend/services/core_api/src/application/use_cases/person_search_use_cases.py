"""Person Search (requirements Section 16; Org Admin page 7, Supervisor page 7; decision D-024).

Admins and Supervisors of a campus search by a reference photo (needs the Re-ID model on this server) or by
a person a camera observed; review each result; link verified results to a case; save a result's crop as
evidence. Every upload, search, review and link is audited. Results never confirm an identity; only a
LIKELY_MATCH can be marked a verified match; the limits of each search are recorded with it.
Vision workers send observations (service token); they expire with the campus's recording retention.
"""

from __future__ import annotations

import base64
import binascii
import io
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from src.application.interfaces.i_campus_media_storage import ICampusMediaStorage
from src.domain.entities.evidence import clean_notes, clean_tags, clean_title
from src.domain.entities.person_search import (
    Band,
    Candidate,
    Limitation,
    Observation,
    PersonSearch,
    Review,
    band_for,
    check_review,
)
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import (
    Conflict,
    InvalidStateTransition,
    NotFound,
    PermissionDenied,
    ServiceUnavailable,
    ValidationFailed,
)
from src.domain.repositories.i_audit_log_repo import IOrganizationAuditLog
from src.domain.repositories.i_unit_of_work import ITransaction
from src.domain.value_objects.enums import UserRole
from src.infrastructure.ml.reid_embedder import DIMENSION, ReIdModelUnavailable

MAX_PHOTO_BYTES = 8 * 1024 * 1024
MAX_CROP_BYTES = 512 * 1024
MAX_PERIOD = timedelta(days=31)
DEFAULT_PERIOD = timedelta(hours=24)
_FOLDER = "person-search"


class IPhotoEmbedder(Protocol):
    version: str

    def embed(self, image_bytes: bytes) -> list[float]: ...


class IIncidentLinks(Protocol):
    async def attach_person_search(self, actor: User, incident_id: UUID, candidate: Candidate) -> str: ...

    async def attach_evidence(self, actor: User, incident_id: UUID, evidence_id: UUID) -> None: ...


class IEvidenceFromCrop(Protocol):
    async def save_person_search_crop(
        self, actor: User, candidate: Candidate, crop: bytes, title: str, notes: str, tags: tuple[str, ...]
    ) -> UUID: ...


@dataclass(frozen=True, slots=True)
class SearchScope:
    since: datetime | None = None
    until: datetime | None = None
    camera_ids: tuple[UUID, ...] = ()
    note: str | None = None


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in values) + "]"


def _staff(actor: User) -> None:
    if actor.role not in (UserRole.ADMIN, UserRole.SUPERVISOR):
        raise PermissionDenied("Only Admins and Supervisors can use Person Search.", code="STAFF_ONLY")


def _period(scope: SearchScope, now: datetime) -> tuple[datetime, datetime]:
    until = scope.until or now
    since = scope.since or until - DEFAULT_PERIOD
    if since > until:
        raise ValidationFailed("The start of the period must be before its end.", code="INVALID_PERIOD")
    if until - since > MAX_PERIOD:
        raise ValidationFailed("Search at most 31 days at a time.", code="PERIOD_TOO_LONG")
    return since, until


def _image(data: bytes, limit: int) -> bytes:
    """Only JPEG or PNG pictures within the size limit; returned as given."""
    if not data:
        raise ValidationFailed("The photo is empty.", code="EMPTY_FILE")
    if len(data) > limit:
        raise ValidationFailed("The photo is too large.", code="PHOTO_TOO_LARGE")
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ("JPEG", "PNG"):
                raise ValidationFailed("Upload a JPEG or PNG photo.", code="INVALID_PHOTO")
            image.verify()
    except ValidationFailed:
        raise
    except Exception:
        raise ValidationFailed("The file is not a readable photo.", code="INVALID_PHOTO") from None
    return data


class PersonSearchService:
    def __init__(
        self,
        repository: Any,
        storage: ICampusMediaStorage,
        audit: IOrganizationAuditLog,
        transaction: ITransaction,
        *,
        likely: float,
        uncertain: float,
        max_results: int,
        embedder: Callable[[], IPhotoEmbedder] | None = None,
        incidents: IIncidentLinks | None = None,
        evidence: IEvidenceFromCrop | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repo = repository
        self._storage = storage
        self._audit = audit
        self._transaction = transaction
        self._likely = likely
        self._uncertain = uncertain
        self._max = max_results
        self._embedder = embedder
        self._incidents = incidents
        self._evidence = evidence
        self._clock = clock

    # --- Vision workers ---------------------------------------------------------------------------------

    async def record_observation(
        self,
        camera_id: UUID,
        track_id: int,
        observed_at: datetime,
        embedding: list[float],
        model_version: str,
        quality: float | None,
        crop_base64: str | None,
    ) -> UUID:
        if len(embedding) != DIMENSION:
            raise ValidationFailed(f"The embedding must have {DIMENSION} numbers.", code="INVALID_EMBEDDING")
        crop: bytes | None = None
        if crop_base64:
            try:
                raw = base64.b64decode(crop_base64, validate=True)
            except (binascii.Error, ValueError):
                raise ValidationFailed("The crop is not valid base64.", code="INVALID_PHOTO") from None
            crop = _image(raw, MAX_CROP_BYTES)
        observation_id = uuid4()
        relative = f"{camera_id}/reid/{observation_id}.jpg" if crop else None
        outcome, org, campus = await self._repo.record_observation(
            observation_id, camera_id, track_id, observed_at, embedding, model_version, quality, relative
        )
        if outcome == "NOT_FOUND":
            raise NotFound("Camera not found.", code="CAMERA_NOT_FOUND")
        if outcome == "REID_DISABLED":
            raise Conflict("Re-ID is switched off for this camera.", code="REID_DISABLED")
        if crop is not None and relative is not None:
            self._storage.write_tier_file("live", org, campus, relative, crop)
        await self._transaction.commit()
        return observation_id

    async def purge_expired(self, default_seconds: int, limit: int = 1000) -> int:
        removed = await self._repo.purge_expired(default_seconds, limit)
        await self._transaction.commit()
        for org, campus, crop in removed:
            if crop:
                try:
                    self._storage.remove_tier_tree("live", org, campus, crop)
                except (OSError, ValueError):
                    pass  # a crop already gone is fine
        return len(removed)

    # --- Reading --------------------------------------------------------------------------------------

    async def observations(
        self, actor: User, *, camera_id: UUID | None, since: datetime | None, until: datetime | None, limit: int
    ) -> list[Observation]:
        _staff(actor)
        return await self._repo.observations(camera_id=camera_id, since=since, until=until, limit=limit)

    def _read(self, actor: User, tier: str, relative: str | None) -> bytes:
        if not relative:
            raise NotFound("No picture is kept for this.", code="PHOTO_NOT_FOUND")
        try:
            return self._storage.tier_path(tier, actor.organization_id, actor.campus_id, relative).read_bytes()
        except (OSError, ValueError):
            raise NotFound("The picture is no longer stored.", code="PHOTO_NOT_FOUND") from None

    async def observation_crop(self, actor: User, observation_id: UUID) -> bytes:
        _staff(actor)
        found = await self._repo.observation(observation_id)
        if found is None:
            raise NotFound("Observation not found.", code="OBSERVATION_NOT_FOUND")
        return self._read(actor, "live", found[1])

    async def searches(self, actor: User, limit: int, offset: int) -> list[PersonSearch]:
        _staff(actor)
        return await self._repo.searches(limit, offset)

    async def search(self, actor: User, search_id: UUID) -> PersonSearch:
        _staff(actor)
        found = await self._repo.search(search_id)
        if found is None:
            raise NotFound("Search not found.", code="SEARCH_NOT_FOUND")
        return found

    async def reference(self, actor: User, search_id: UUID) -> bytes:
        found = await self.search(actor, search_id)
        return self._read(actor, "evidence", found.reference_path)

    async def candidate(self, actor: User, candidate_id: UUID, *, lock: bool = False) -> Candidate:
        _staff(actor)
        found = await self._repo.candidate(candidate_id, lock=lock)
        if found is None:
            raise NotFound("Result not found.", code="RESULT_NOT_FOUND")
        return found

    async def candidate_crop(self, actor: User, candidate_id: UUID) -> bytes:
        found = await self.candidate(actor, candidate_id)
        return self._read(actor, "evidence", found.crop_path)

    # --- Searching --------------------------------------------------------------------------------------

    async def _limitations(self, since: datetime, until: datetime, cameras: list[UUID]) -> list[Limitation]:
        found: list[Limitation] = []
        for camera_id, name, state, reid, observed in await self._repo.camera_coverage(since, until, cameras):
            if state == "OFFLINE":
                found.append(Limitation(camera_id, name, "CAMERA_OFFLINE", "The camera is offline now."))
            if not reid:
                found.append(Limitation(camera_id, name, "REID_OFF", "Re-ID is not running on this camera."))
            elif observed == 0:
                found.append(
                    Limitation(camera_id, name, "NO_OBSERVATIONS", "No person was observed here in this period.")
                )
        return found

    async def _run(
        self,
        actor: User,
        *,
        kind: str,
        vector: str,
        scope: SearchScope,
        reference_observation: UUID | None,
        reference_bytes: bytes | None,
        model_version: str | None,
    ) -> PersonSearch:
        since, until = _period(scope, self._clock())
        cameras = list(dict.fromkeys(scope.camera_ids))
        search_id = uuid4()
        nearest, compared = await self._repo.nearest(
            vector, since=since, until=until, camera_ids=cameras, exclude=reference_observation, limit=self._max
        )
        reference_path = None
        if reference_bytes is not None:
            reference_path = f"{_FOLDER}/{search_id}/reference.jpg"
            self._storage.write_tier_file(
                "evidence", actor.organization_id, actor.campus_id, reference_path, reference_bytes
            )
        await self._repo.add_search(
            search_id,
            kind=kind,
            reference_observation_id=reference_observation,
            reference_path=reference_path,
            period_from=since,
            period_to=until,
            camera_ids=cameras,
            model_version=model_version,
            searched=compared,
            limitations=await self._limitations(since, until, cameras),
            note=(scope.note or "").strip()[:1000] or None,
        )
        rank = 0
        for observation, crop, similarity in nearest:
            band = band_for(similarity, self._likely, self._uncertain)
            if band is None:
                continue
            rank += 1
            copied = None
            if crop:
                try:
                    data = self._storage.tier_path("live", actor.organization_id, actor.campus_id, crop).read_bytes()
                    copied = f"{_FOLDER}/{search_id}/{rank:03d}.jpg"
                    self._storage.write_tier_file("evidence", actor.organization_id, actor.campus_id, copied, data)
                except (OSError, ValueError):
                    copied = None
            await self._repo.add_candidate(search_id, rank, observation, similarity, band, copied)
        await self._repo.set_candidate_count(search_id, rank)
        await self._audit.record(
            actor,
            "PERSON_SEARCH_RUN",
            "PERSON_SEARCH",
            search_id,
            {"kind": kind, "results": rank, "compared": compared, "cameras": len(cameras) or "all"},
        )
        result = await self._repo.search(search_id)
        assert result is not None
        await self._transaction.commit()
        return result

    async def search_by_photo(self, actor: User, photo: bytes, scope: SearchScope) -> PersonSearch:
        _staff(actor)
        data = _image(photo, MAX_PHOTO_BYTES)
        if self._embedder is None:
            raise ServiceUnavailable("Photo search needs the Re-ID model.", code="REID_MODEL_UNAVAILABLE")
        try:
            embedder = self._embedder()
            vector = embedder.embed(data)
        except ReIdModelUnavailable as error:
            raise ServiceUnavailable(
                f"Photo search needs the trained Re-ID model on this server: {error}", code="REID_MODEL_UNAVAILABLE"
            ) from None
        await self._audit.record(actor, "PERSON_SEARCH_PHOTO_UPLOADED", "PERSON_SEARCH", None, {"bytes": len(data)})
        return await self._run(
            actor,
            kind="PHOTO",
            vector=_vector_literal(vector),
            scope=scope,
            reference_observation=None,
            reference_bytes=data,
            model_version=embedder.version,
        )

    async def search_by_observation(self, actor: User, observation_id: UUID, scope: SearchScope) -> PersonSearch:
        _staff(actor)
        found = await self._repo.observation(observation_id)
        vector = await self._repo.observation_vector(observation_id)
        if found is None or vector is None:
            raise NotFound("Observation not found (it may have expired).", code="OBSERVATION_NOT_FOUND")
        observation, crop = found
        reference = None
        if crop:
            try:
                reference = self._read(actor, "live", crop)
            except NotFound:
                reference = None
        return await self._run(
            actor,
            kind="OBSERVATION",
            vector=vector,
            scope=scope,
            reference_observation=observation.id,
            reference_bytes=reference,
            model_version=observation.model_version,
        )

    # --- Review, case link, evidence ----------------------------------------------------------------------

    async def review(self, actor: User, candidate_id: UUID, review: Review, note: str | None) -> Candidate:
        found = await self.candidate(actor, candidate_id, lock=True)
        check_review(found.band, review)
        text = (note or "").strip()
        if len(text) > 1000:
            raise ValidationFailed("The note can be at most 1000 characters.", code="INVALID_NOTE")
        if found.incident_id is not None and review is not Review.VERIFIED_MATCH:
            raise Conflict("Unlink the result from its case first.", code="RESULT_LINKED")
        await self._repo.review(candidate_id, review, text or None)
        await self._audit.record(
            actor, "PERSON_SEARCH_REVIEWED", "PERSON_SEARCH", found.search_id,
            {"result": str(candidate_id), "review": review.value, "band": found.band.value},
        )  # fmt: skip
        updated = await self.candidate(actor, candidate_id)
        await self._transaction.commit()
        return updated

    async def link_incident(self, actor: User, candidate_id: UUID, incident_id: UUID) -> Candidate:
        found = await self.candidate(actor, candidate_id, lock=True)
        if found.review is not Review.VERIFIED_MATCH:
            raise Conflict("Only a verified result can be linked to a case.", code="RESULT_NOT_VERIFIED")
        if found.incident_id == incident_id:
            return found
        if found.incident_id is not None:
            raise Conflict("This result is already linked to another case.", code="RESULT_ALREADY_LINKED")
        assert self._incidents is not None
        reference = await self._incidents.attach_person_search(actor, incident_id, found)
        await self._repo.link_incident(candidate_id, incident_id)
        await self._audit.record(
            actor, "PERSON_SEARCH_LINKED", "PERSON_SEARCH", found.search_id,
            {"result": str(candidate_id), "incident": reference},
        )  # fmt: skip
        updated = await self.candidate(actor, candidate_id)
        await self._transaction.commit()
        return updated

    async def save_evidence(
        self, actor: User, candidate_id: UUID, title: str, notes: str | None, tags: list[str] | None
    ) -> UUID:
        found = await self.candidate(actor, candidate_id, lock=True)
        if found.evidence_id is not None:
            raise Conflict("This result was already saved as evidence.", code="ALREADY_SAVED")
        crop = self._read(actor, "evidence", found.crop_path)
        assert self._evidence is not None
        evidence_id = await self._evidence.save_person_search_crop(
            actor, found, crop, clean_title(title), clean_notes(notes), clean_tags(tags)
        )
        await self._repo.set_evidence(candidate_id, evidence_id)
        if found.incident_id is not None and self._incidents is not None:
            try:  # the evidence joins the result's case, with a history entry
                await self._incidents.attach_evidence(actor, found.incident_id, evidence_id)
            except InvalidStateTransition:
                pass  # a closed case: the evidence is kept without the link
        await self._transaction.commit()
        return evidence_id


def band_label(band: Band) -> str:
    return "Likely match" if band is Band.LIKELY_MATCH else "Uncertain"
