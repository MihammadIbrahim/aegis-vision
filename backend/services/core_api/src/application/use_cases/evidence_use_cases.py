"""The investigation evidence vault (requirements Section 17; decision D-023).

Admins and Supervisors of a campus save clips and snapshots from an alert or from playback, view
them, and edit their title, notes and investigation reference. Deleting evidence is an Admin's
action, confirmed with their password. Every save, view (download), update and deletion is audited.

Evidence files are cut from routine recording segments with ffmpeg into the ``evidence`` tier,
where no retention applies. A snapshot saved from an alert reuses the detector's snapshot when the
alert has one on disk, and is otherwise extracted from the recording at the detection time.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from src.application.interfaces.i_campus_media_storage import ICampusMediaStorage
from src.application.use_cases.password_confirmation import PasswordConfirmation
from src.application.use_cases.recording_use_cases import recording_file
from src.domain.entities.evidence import (
    EvidenceItem,
    EvidenceKind,
    EvidenceSource,
    clean_investigation_ref,
    clean_notes,
    clean_tags,
    clean_title,
)
from src.domain.entities.recording import RecordingSegment
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import (
    NotFound,
    OperationFailed,
    PermissionDenied,
    ServiceUnavailable,
    ValidationFailed,
)
from src.domain.repositories.i_audit_log_repo import IOrganizationAuditLog
from src.domain.repositories.i_evidence_repo import EvidenceFilter, IEvidenceRepository
from src.domain.repositories.i_recording_repo import IRecordingRepository, SegmentFilter
from src.domain.repositories.i_unit_of_work import ITransaction
from src.domain.services.permission_policy import ensure_camera_viewer
from src.domain.value_objects.enums import UserRole
from src.infrastructure.media.clips import (
    ClipPiece,
    ClipToolFailed,
    ClipToolUnavailable,
    FfmpegClipTool,
    MediaFile,
)

logger = logging.getLogger(__name__)

EVIDENCE_SAVED = "EVIDENCE_SAVED"
EVIDENCE_VIEWED = "EVIDENCE_VIEWED"
EVIDENCE_UPDATED = "EVIDENCE_UPDATED"
EVIDENCE_DELETED = "EVIDENCE_DELETED"

_FILE_NAMES = {EvidenceKind.CLIP: "clip.mp4", EvidenceKind.SNAPSHOT: "snapshot.jpg"}
_MEDIA_TYPES = {EvidenceKind.CLIP: "video/mp4", EvidenceKind.SNAPSHOT: "image/jpeg"}


def _missing() -> NotFound:
    return NotFound("Evidence not found.")


@dataclass(frozen=True, slots=True)
class EvidenceDetails:
    title: str
    notes: str | None = None
    investigation_ref: str | None = None
    tags: list[str] | None = None  # None on an update: unchanged
    incident_id: UUID | None = None  # link the saved item to an open case (decision D-024)


@dataclass(frozen=True, slots=True)
class ClipWindow:
    pre_seconds: int
    post_seconds: int
    max_seconds: int


class IEvidenceLinker(Protocol):
    """Links saved evidence to an open case in the same transaction (Incidents, decision D-024)."""

    async def attach_evidence(self, actor: User, incident_id: UUID, evidence_id: UUID) -> None: ...


class EvidenceVault:
    def __init__(
        self,
        evidence: IEvidenceRepository,
        recordings: IRecordingRepository,
        storage: ICampusMediaStorage,
        clips: FfmpegClipTool,
        audit: IOrganizationAuditLog,
        transaction: ITransaction,
        *,
        window: ClipWindow,
        confirmation: PasswordConfirmation | None = None,
        linker: IEvidenceLinker | None = None,
    ) -> None:
        self._linker = linker
        self._evidence = evidence
        self._recordings = recordings
        self._storage = storage
        self._clips = clips
        self._audit = audit
        self._transaction = transaction
        self._window = window
        self._confirmation = confirmation

    # --- Reading ----------------------------------------------------------------------------------

    async def list_items(self, actor: User, where: EvidenceFilter) -> list[EvidenceItem]:
        ensure_camera_viewer(actor)
        return await self._evidence.list_items(actor.organization_id, actor.campus_id, where)

    async def get(self, actor: User, evidence_id: UUID) -> EvidenceItem:
        ensure_camera_viewer(actor)
        item = await self._evidence.get(evidence_id, actor.organization_id, actor.campus_id)
        if item is None:
            raise _missing()
        return item

    async def media(self, actor: User, evidence_id: UUID) -> tuple[EvidenceItem, Path]:
        item = await self.get(actor, evidence_id)
        path = self._storage.tier_path("evidence", item.organization_id, item.campus_id, item.relative_path)
        if not path.is_file():
            raise NotFound("The evidence file is missing on disk.", code="EVIDENCE_FILE_MISSING")
        await self._audit.record(actor, EVIDENCE_VIEWED, "EVIDENCE", item.id, {"kind": item.kind.value})
        await self._transaction.commit()
        return item, path

    # --- Saving -----------------------------------------------------------------------------------

    async def save_from_alert(
        self, actor: User, alert_id: UUID, kind: EvidenceKind, details: EvidenceDetails
    ) -> EvidenceItem:
        ensure_camera_viewer(actor)
        source = await self._evidence.alert_source(alert_id, actor.organization_id, actor.campus_id)
        if source is None:
            raise NotFound("Alert not found.")
        covers_from = source.detected_at - timedelta(seconds=self._window.pre_seconds)
        covers_to = source.last_detected_at + timedelta(seconds=self._window.post_seconds)
        evidence_id = uuid4()
        output = self._storage.tier_path(
            "evidence", actor.organization_id, actor.campus_id, f"{evidence_id}/{_FILE_NAMES[kind]}"
        )
        if kind is EvidenceKind.SNAPSHOT:
            snapshot = self._alert_snapshot(actor, source.snapshot_ref)
            if snapshot is not None:
                media = self._clips.copy_file(snapshot, output)
                covers_from = covers_to = source.detected_at
            else:
                media, covers_from, covers_to = await self._snapshot_from_recordings(
                    actor, source.camera_id, source.detected_at, output
                )
        else:
            media, covers_from, covers_to = await self._clip_from_recordings(
                actor, source.camera_id, covers_from, covers_to, output
            )
        item = self._item(
            actor, evidence_id, kind, details, EvidenceSource.ALERT, source.alert_id, source.camera_id,
            source.camera_name,
            covers_from, covers_to, media,
        )  # fmt: skip
        return await self._store(actor, item, {"alert_id": str(source.alert_id), "alert_type": source.alert_type})

    async def save_from_playback(
        self,
        actor: User,
        segment_id: UUID,
        kind: EvidenceKind,
        start_offset: float,
        end_offset: float | None,
        details: EvidenceDetails,
    ) -> EvidenceItem:
        ensure_camera_viewer(actor)
        segment = await self._recordings.get_segment(segment_id, actor.organization_id, actor.campus_id)
        if segment is None:
            raise NotFound("Recording not found.")
        if start_offset < 0 or start_offset > segment.duration_seconds:
            raise ValidationFailed("The start offset is outside this recording.", code="INVALID_OFFSET")
        at = segment.started_at + timedelta(seconds=start_offset)
        evidence_id = uuid4()
        output = self._storage.tier_path(
            "evidence", actor.organization_id, actor.campus_id, f"{evidence_id}/{_FILE_NAMES[kind]}"
        )
        if kind is EvidenceKind.SNAPSHOT:
            media, covers_from, covers_to = await self._snapshot_from_recordings(actor, segment.camera_id, at, output)
        else:
            if end_offset is None or end_offset <= start_offset:
                raise ValidationFailed("A clip needs an end after its start.", code="INVALID_OFFSET")
            if end_offset - start_offset > self._window.max_seconds:
                raise ValidationFailed(
                    f"A clip can be at most {self._window.max_seconds} seconds long.", code="CLIP_TOO_LONG"
                )
            until = segment.started_at + timedelta(seconds=end_offset)
            media, covers_from, covers_to = await self._clip_from_recordings(
                actor, segment.camera_id, at, until, output
            )
        item = self._item(
            actor, evidence_id, kind, details, EvidenceSource.PLAYBACK, None, segment.camera_id, segment.camera_name,
            covers_from, covers_to, media,
        )  # fmt: skip
        return await self._store(actor, item, {"segment_id": str(segment.id)})

    async def alert_preview(self, actor: User, alert_id: UUID, kind: EvidenceKind, output: Path) -> None:
        """The alert's snapshot or event clip written to ``output`` only for viewing (Alerts page item 10):
        nothing is saved to the vault. Clips are audited as footage playback."""
        ensure_camera_viewer(actor)
        source = await self._evidence.alert_source(alert_id, actor.organization_id, actor.campus_id)
        if source is None:
            raise NotFound("Alert not found.")
        if kind is EvidenceKind.SNAPSHOT:
            snapshot = self._alert_snapshot(actor, source.snapshot_ref)
            if snapshot is not None:
                self._clips.copy_file(snapshot, output)
            else:
                await self._snapshot_from_recordings(actor, source.camera_id, source.detected_at, output)
            return
        covers_from = source.detected_at - timedelta(seconds=self._window.pre_seconds)
        covers_to = source.last_detected_at + timedelta(seconds=self._window.post_seconds)
        await self._clip_from_recordings(actor, source.camera_id, covers_from, covers_to, output)
        window: dict[str, object] = {"from": covers_from.isoformat(), "to": covers_to.isoformat()}
        await self._audit.record(actor, "ALERT_CLIP_VIEWED", "ALERT", alert_id, window)
        await self._transaction.commit()

    async def moment_clip(
        self, actor: User, camera_id: UUID, since: datetime, until: datetime, output: Path, action: str
    ) -> None:
        """A short clip of a camera around a moment, for viewing only (Person Search results)."""
        ensure_camera_viewer(actor)
        await self._clip_from_recordings(actor, camera_id, since, until, output)
        window: dict[str, object] = {"camera_id": str(camera_id), "from": since.isoformat(), "to": until.isoformat()}
        await self._audit.record(actor, action, "CAMERA", camera_id, window)
        await self._transaction.commit()

    async def save_person_search_crop(
        self, actor: User, candidate: Any, crop: bytes, title: str, notes: str, tags: tuple[str, ...]
    ) -> UUID:
        """A Person Search result's crop as evidence (page 9 item 4; decision D-024). Not committed here:
        the caller records the link on the result in the same transaction."""
        ensure_camera_viewer(actor)
        evidence_id = uuid4()
        relative = f"{evidence_id}/{_FILE_NAMES[EvidenceKind.SNAPSHOT]}"
        path = self._storage.write_tier_file("evidence", actor.organization_id, actor.campus_id, relative, crop)
        now = datetime.now(UTC)
        item = EvidenceItem(
            id=evidence_id,
            organization_id=actor.organization_id,
            campus_id=actor.campus_id,
            kind=EvidenceKind.SNAPSHOT,
            title=title,
            notes=notes,
            investigation_ref=None,
            source=EvidenceSource.PERSON_SEARCH,
            alert_id=None,
            camera_id=candidate.camera_id,
            camera_name=candidate.camera_name,
            covers_from=candidate.observed_at,
            covers_to=candidate.observed_at,
            relative_path=relative,
            media_type=_MEDIA_TYPES[EvidenceKind.SNAPSHOT],
            size_bytes=len(crop),
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            saved_by=actor.id,
            saved_by_name=actor.full_name,
            saved_at=now,
            updated_at=now,
            tags=tags,
        )
        try:
            await self._evidence.add(item)
            await self._audit.record(
                actor, EVIDENCE_SAVED, "EVIDENCE", evidence_id,
                {"kind": "SNAPSHOT", "source": "PERSON_SEARCH", "result_id": str(candidate.id)},
            )  # fmt: skip
        except Exception:
            self._storage.remove_tier_tree("evidence", actor.organization_id, actor.campus_id, str(evidence_id))
            raise
        return evidence_id

    # --- Changing ---------------------------------------------------------------------------------

    async def update(self, actor: User, evidence_id: UUID, details: EvidenceDetails) -> EvidenceItem:
        before = await self.get(actor, evidence_id)
        title, notes, ref = (
            clean_title(details.title),
            clean_notes(details.notes),
            clean_investigation_ref(details.investigation_ref),
        )
        tags = before.tags if details.tags is None else clean_tags(details.tags)
        changed = [
            name
            for name, old, new in (
                ("title", before.title, title),
                ("notes", before.notes, notes),
                ("investigation_ref", before.investigation_ref, ref),
                ("tags", before.tags, tags),
            )
            if old != new
        ]
        if changed:
            if not await self._evidence.update(
                evidence_id,
                actor.organization_id,
                actor.campus_id,
                title=title,
                notes=notes,
                investigation_ref=ref,
                tags=tags,
            ):
                raise _missing()
            await self._audit.record(actor, EVIDENCE_UPDATED, "EVIDENCE", evidence_id, {"fields": changed})
        after = await self.get(actor, evidence_id)
        await self._transaction.commit()
        return after

    async def delete(self, actor: User, evidence_id: UUID, current_password: str) -> None:
        """Admins only, with their password (decision D-023). The record and the audit entry are
        committed first; the files are then erased from the evidence tier."""
        if actor.role is not UserRole.ADMIN:
            raise PermissionDenied("Only an Organization Admin can delete evidence.", code="ADMIN_ONLY")
        assert self._confirmation is not None
        item = await self.get(actor, evidence_id)
        await self._confirmation.confirm(current_password)
        if not await self._evidence.delete(evidence_id, actor.organization_id, actor.campus_id):
            raise _missing()
        await self._audit.record(
            actor,
            EVIDENCE_DELETED,
            "EVIDENCE",
            item.id,
            {
                "kind": item.kind.value,
                "title": item.title,
                "alert_id": None if item.alert_id is None else str(item.alert_id),
            },
        )
        await self._transaction.commit()
        try:
            self._storage.remove_tier_tree("evidence", item.organization_id, item.campus_id, str(item.id))
        except Exception as error:  # noqa: BLE001 - reported, never hidden
            logger.error("evidence %s deleted from the index but its files could not be erased: %s", item.id, error)
            raise OperationFailed(
                "The evidence record was deleted, but its file could not be erased. Contact the administrator.",
                code="EVIDENCE_FILE_CLEANUP_FAILED",
            ) from error

    # --- Helpers ----------------------------------------------------------------------------------

    def _alert_snapshot(self, actor: User, snapshot_ref: str | None) -> Path | None:
        if not snapshot_ref:
            return None
        try:
            path = self._storage.tier_path("live", actor.organization_id, actor.campus_id, snapshot_ref)
        except ValueError:
            return None
        return path if path.is_file() else None

    async def _segments_between(
        self, actor: User, camera_id: UUID, since: datetime, until: datetime
    ) -> list[RecordingSegment]:
        found = await self._recordings.list_segments(
            actor.organization_id, actor.campus_id, SegmentFilter((camera_id,), since, until, limit=500)
        )
        return sorted(found, key=lambda s: s.started_at)

    async def _clip_from_recordings(
        self, actor: User, camera_id: UUID, since: datetime, until: datetime, output: Path
    ) -> tuple[MediaFile, datetime, datetime]:
        segments = await self._segments_between(actor, camera_id, since, until)
        if not segments:
            raise ValidationFailed("No recording covers this period (it may have expired).", code="NO_RECORDING")
        pieces = [ClipPiece(recording_file(self._storage, s), s.duration_seconds) for s in segments]
        if not all(p.path.is_file() for p in pieces):
            raise NotFound("A recording file for this period is no longer on disk.", code="RECORDING_FILE_MISSING")
        covers_from = max(since, segments[0].started_at)
        covers_to = min(until, segments[-1].ended_at)
        start_offset = (covers_from - segments[0].started_at).total_seconds()
        duration = (covers_to - covers_from).total_seconds()
        if duration <= 0:
            raise ValidationFailed("No recording covers this period (it may have expired).", code="NO_RECORDING")
        try:
            media = await self._clips.cut_clip(pieces, start_offset, duration, output)
        except ClipToolUnavailable:
            raise ServiceUnavailable(
                "Clips cannot be cut on this server (ffmpeg is missing).", code="CLIP_TOOL_UNAVAILABLE"
            ) from None
        except ClipToolFailed as error:
            logger.error("evidence clip failed: %s", error)
            raise OperationFailed("The clip could not be produced from the recording.", code="CLIP_FAILED") from None
        return media, covers_from, covers_to

    async def _snapshot_from_recordings(
        self, actor: User, camera_id: UUID, at: datetime, output: Path
    ) -> tuple[MediaFile, datetime, datetime]:
        segments = await self._segments_between(actor, camera_id, at, at + timedelta(milliseconds=1))
        if not segments:
            raise ValidationFailed("No recording covers this moment (it may have expired).", code="NO_RECORDING")
        segment = segments[0]
        path = recording_file(self._storage, segment)
        if not path.is_file():
            raise NotFound("The recording file for this moment is no longer on disk.", code="RECORDING_FILE_MISSING")
        try:
            media = await self._clips.snapshot(path, (at - segment.started_at).total_seconds(), output)
        except ClipToolUnavailable:
            raise ServiceUnavailable(
                "Snapshots cannot be cut on this server (ffmpeg is missing).", code="CLIP_TOOL_UNAVAILABLE"
            ) from None
        except ClipToolFailed as error:
            logger.error("evidence snapshot failed: %s", error)
            raise OperationFailed(
                "The snapshot could not be produced from the recording.", code="CLIP_FAILED"
            ) from None
        return media, at, at

    def _item(
        self,
        actor: User,
        evidence_id: UUID,
        kind: EvidenceKind,
        details: EvidenceDetails,
        source: EvidenceSource,
        alert_id: UUID | None,
        camera_id: UUID,
        camera_name: str,
        covers_from: datetime,
        covers_to: datetime,
        media: MediaFile,
    ) -> EvidenceItem:
        now = datetime.now(tz=covers_from.tzinfo)
        return EvidenceItem(
            id=evidence_id,
            organization_id=actor.organization_id,
            campus_id=actor.campus_id,
            kind=kind,
            title=clean_title(details.title),
            notes=clean_notes(details.notes),
            investigation_ref=clean_investigation_ref(details.investigation_ref),
            source=source,
            alert_id=alert_id,
            camera_id=camera_id,
            camera_name=camera_name,
            covers_from=covers_from,
            covers_to=covers_to,
            relative_path=f"{evidence_id}/{_FILE_NAMES[kind]}",
            media_type=_MEDIA_TYPES[kind],
            size_bytes=media.size_bytes,
            sha256=media.sha256,
            saved_by=actor.id,
            saved_by_name=actor.full_name,
            saved_at=now,
            updated_at=now,
            tags=clean_tags(details.tags),
            incident_id=details.incident_id,
        )

    async def _store(self, actor: User, item: EvidenceItem, metadata: dict[str, object]) -> EvidenceItem:
        try:
            await self._evidence.add(item)
            await self._audit.record(
                actor,
                EVIDENCE_SAVED,
                "EVIDENCE",
                item.id,
                {"kind": item.kind.value, "source": item.source.value, **metadata},
            )
            if item.incident_id is not None:
                if self._linker is None:
                    raise ValidationFailed(
                        "Evidence cannot be linked to a case here.", code="INCIDENT_LINK_UNAVAILABLE"
                    )
                await self._linker.attach_evidence(actor, item.incident_id, item.id)
            saved = await self._evidence.get(item.id, actor.organization_id, actor.campus_id)
            await self._transaction.commit()
        except Exception:
            # Never leave an orphan file in the vault when the index row was not written.
            try:
                self._storage.remove_tier_tree("evidence", item.organization_id, item.campus_id, str(item.id))
            except Exception:  # noqa: BLE001
                logger.exception("could not remove the unsaved evidence files of %s", item.id)
            raise
        assert saved is not None
        return saved
