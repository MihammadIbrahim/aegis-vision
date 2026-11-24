"""Person Search storage (migration 0028). Staff of a campus read and write their own campus's searches
(Row-Level Security); observations arrive only through ``app_reid_observation_record``."""

import json
import re
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.person_search import (
    Band,
    Candidate,
    Limitation,
    Observation,
    PersonSearch,
    Review,
)
from src.domain.entities.user import User

_UUIDS = ("id", "org", "campus", "camera", "search", "observation", "incident", "evidence", "actor", "candidate")
_PARAM = re.compile(r"(?<!:):([a-z_]+)\b")


def _sql(statement: str, params: dict[str, Any]):  # type: ignore[no-untyped-def]
    used = set(_PARAM.findall(statement))
    return text(statement).bindparams(*(bindparam(n, type_=Uuid) for n in _UUIDS if n in params and n in used))


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in values) + "]"


def _label(*parts: str | None) -> str | None:
    return " › ".join(p for p in parts if p) or None


_OBSERVATION = """o.id, o.camera_id, o.camera_name, o.building_name, o.floor_name, o.area_name, o.track_id,
       o.observed_at, o.quality, o.crop_path IS NOT NULL AS has_crop, o.model_version"""

_CANDIDATE = """c.id, c.search_id, c.rank, c.observation_id, c.camera_id, c.camera_name, c.location_label, c.track_id,
       c.observed_at, c.similarity, c.band, c.crop_path, c.review, c.reviewed_by_name, c.reviewed_at,
       c.review_note, c.incident_id, (SELECT i.number FROM incidents i WHERE i.id = c.incident_id) AS incident_number,
       c.evidence_id"""

_SEARCH = """s.id, s.kind, s.reference_observation_id, s.reference_path, s.period_from, s.period_to, s.camera_ids,
       s.model_version, s.candidate_count, s.searched_observations, s.limitations, s.note, s.incident_id,
       s.created_by_name, s.created_at"""


def _observation(r: Any) -> Observation:
    return Observation(
        r.id, r.camera_id, r.camera_name, _label(r.building_name, r.floor_name, r.area_name), r.track_id,
        r.observed_at, float(r.quality) if r.quality is not None else None, bool(r.has_crop), r.model_version,
    )  # fmt: skip


def _candidate(r: Any) -> Candidate:
    return Candidate(
        id=r.id,
        search_id=r.search_id,
        rank=r.rank,
        observation_id=r.observation_id,
        camera_id=r.camera_id,
        camera_name=r.camera_name,
        location_label=r.location_label,
        track_id=r.track_id,
        observed_at=r.observed_at,
        similarity=float(r.similarity),
        band=Band(r.band),
        has_crop=r.crop_path is not None,
        review=Review(r.review),
        reviewed_by_name=r.reviewed_by_name,
        reviewed_at=r.reviewed_at,
        review_note=r.review_note,
        incident_id=r.incident_id,
        incident_number=r.incident_number,
        evidence_id=r.evidence_id,
        crop_path=r.crop_path,
    )


def _search(r: Any, candidates: list[Candidate] | None = None) -> PersonSearch:
    return PersonSearch(
        id=r.id,
        kind=r.kind,
        reference_observation_id=r.reference_observation_id,
        has_reference=r.reference_path is not None,
        period_from=r.period_from,
        period_to=r.period_to,
        camera_ids=list(r.camera_ids or []),
        model_version=r.model_version,
        candidate_count=r.candidate_count,
        searched_observations=r.searched_observations,
        limitations=[
            Limitation(
                UUID(x["camera_id"]) if x.get("camera_id") else None, x["camera_name"], x["issue"], x.get("detail")
            )
            for x in (r.limitations or [])
        ],  # fmt: skip
        note=r.note,
        incident_id=r.incident_id,
        created_by_name=r.created_by_name,
        created_at=r.created_at,
        candidates=candidates or [],
        reference_path=r.reference_path,
    )


class SqlPersonSearchRepository:
    def __init__(self, session: AsyncSession, actor: User | None = None) -> None:
        self._session = session
        self._actor = actor
        self._scope = {} if actor is None else {"org": actor.organization_id, "campus": actor.campus_id}

    async def _run(self, statement: str, params: dict[str, Any] | None = None) -> Any:
        values = {**self._scope, **(params or {})}
        used = set(_PARAM.findall(statement))
        values = {k: v for k, v in values.items() if k in used}
        return await self._session.execute(_sql(statement, values), values)

    # --- Observations -------------------------------------------------------------------------------------

    async def record_observation(
        self,
        observation_id: UUID,
        camera_id: UUID,
        track_id: int,
        observed_at: datetime,
        embedding: list[float],
        model_version: str,
        quality: float | None,
        crop_path: str | None,
    ) -> tuple[str, UUID | None, UUID | None]:
        row = (
            await self._run(
                "SELECT outcome, organization_id, campus_id FROM app_reid_observation_record(:id, :camera, :track, "
                ":observed_at, :embedding, :model, :quality, :crop)",
                {
                    "id": observation_id,
                    "camera": camera_id,
                    "track": track_id,
                    "observed_at": observed_at,
                    "embedding": vector_literal(embedding),
                    "model": model_version,
                    "quality": quality,
                    "crop": crop_path,
                },
            )
        ).one()
        return row.outcome, row.organization_id, row.campus_id

    async def purge_expired(self, default_seconds: int, limit: int) -> list[tuple[UUID, UUID, str | None]]:
        rows = await self._run(
            "SELECT organization_id, campus_id, crop_path FROM app_reid_observations_purge(:seconds, :limit)",
            {"seconds": default_seconds, "limit": limit},
        )
        return [(r.organization_id, r.campus_id, r.crop_path) for r in rows]

    async def observations(
        self, *, camera_id: UUID | None, since: datetime | None, until: datetime | None, limit: int
    ) -> list[Observation]:
        clauses = ["o.organization_id = :org", "o.campus_id = :campus"]
        params: dict[str, Any] = {"limit": limit}
        if camera_id is not None:
            clauses.append("o.camera_id = :camera")
            params["camera"] = camera_id
        if since is not None:
            clauses.append("o.observed_at >= :since")
            params["since"] = since
        if until is not None:
            clauses.append("o.observed_at <= :until")
            params["until"] = until
        rows = await self._run(
            f"SELECT {_OBSERVATION} FROM reid_observations o WHERE {' AND '.join(clauses)} "
            "ORDER BY o.observed_at DESC LIMIT :limit",
            params,
        )
        return [_observation(r) for r in rows]

    async def observation(self, observation_id: UUID) -> tuple[Observation, str | None] | None:
        row = (
            await self._run(
                f"SELECT {_OBSERVATION}, o.crop_path FROM reid_observations o "
                "WHERE o.id = :observation AND o.organization_id = :org AND o.campus_id = :campus",
                {"observation": observation_id},
            )
        ).first()
        return None if row is None else (_observation(row), row.crop_path)

    async def observation_vector(self, observation_id: UUID) -> str | None:
        row = (
            await self._run(
                "SELECT o.embedding::text FROM reid_observations o "
                "WHERE o.id = :observation AND o.organization_id = :org AND o.campus_id = :campus",
                {"observation": observation_id},
            )
        ).first()
        return None if row is None else str(row[0])

    async def nearest(
        self,
        vector: str,
        *,
        since: datetime,
        until: datetime,
        camera_ids: list[UUID],
        exclude: UUID | None,
        limit: int,
    ) -> tuple[list[tuple[Observation, str | None, float]], int]:
        """The closest observations (cosine similarity) and how many were compared."""
        clauses = ["o.organization_id = :org", "o.campus_id = :campus", "o.observed_at BETWEEN :since AND :until"]
        params: dict[str, Any] = {"since": since, "until": until, "vector": vector, "limit": limit}
        if camera_ids:
            clauses.append("o.camera_id = ANY (CAST(:cameras AS uuid[]))")
            params["cameras"] = [str(c) for c in camera_ids]
        if exclude is not None:
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM reid_observations x WHERE x.id = :observation "
                "AND x.camera_id = o.camera_id AND x.track_id = o.track_id)"
            )
            params["observation"] = exclude
        where = " AND ".join(clauses)
        total = (await self._run(f"SELECT count(*) FROM reid_observations o WHERE {where}", params)).scalar_one()
        rows = await self._run(
            f"""SELECT {_OBSERVATION}, o.crop_path, 1 - (o.embedding <=> CAST(:vector AS vector)) AS similarity
                  FROM reid_observations o WHERE {where}
                 ORDER BY o.embedding <=> CAST(:vector AS vector) LIMIT :limit""",
            params,
        )
        return [(_observation(r), r.crop_path, float(r.similarity)) for r in rows], int(total)

    async def camera_coverage(
        self, since: datetime, until: datetime, camera_ids: list[UUID]
    ) -> list[tuple[UUID, str, str, bool, int]]:
        """Per camera in scope: name, stream state, Re-ID allowed, observations in the period."""
        clauses = ["c.organization_id = :org", "c.campus_id = :campus"]
        params: dict[str, Any] = {"since": since, "until": until}
        if camera_ids:
            clauses.append("c.id = ANY (CAST(:cameras AS uuid[]))")
            params["cameras"] = [str(c) for c in camera_ids]
        rows = await self._run(
            f"""SELECT c.id, c.display_name, COALESCE(st.state, 'UNKNOWN') AS state,
                       COALESCE((SELECT cfg.person_reid FROM app_camera_ai_config(c.id) cfg), false) AS reid,
                       (SELECT count(*) FROM reid_observations o WHERE o.camera_id = c.id
                          AND o.observed_at BETWEEN :since AND :until) AS observed
                  FROM cameras c LEFT JOIN camera_status st ON st.camera_id = c.id
                 WHERE {" AND ".join(clauses)} ORDER BY c.display_name""",
            params,
        )
        return [(r.id, r.display_name, r.state, bool(r.reid), int(r.observed)) for r in rows]

    # --- Searches ----------------------------------------------------------------------------------------------

    async def add_search(
        self,
        search_id: UUID,
        *,
        kind: str,
        reference_observation_id: UUID | None,
        reference_path: str | None,
        period_from: datetime,
        period_to: datetime,
        camera_ids: list[UUID],
        model_version: str | None,
        searched: int,
        limitations: list[Limitation],
        note: str | None,
    ) -> None:
        assert self._actor is not None
        await self._run(
            """INSERT INTO person_searches (id, organization_id, campus_id, kind, reference_observation_id,
                   reference_path, period_from, period_to, camera_ids, model_version, searched_observations,
                   limitations, note, created_by, created_by_name)
               VALUES (:search, :org, :campus, :kind, :observation, :reference, :since, :until,
                   CAST(:cameras AS uuid[]), :model, :searched, CAST(:limitations AS jsonb), :note, :actor, :name)""",
            {
                "search": search_id,
                "kind": kind,
                "observation": reference_observation_id,
                "reference": reference_path,
                "since": period_from,
                "until": period_to,
                "cameras": [str(c) for c in camera_ids],
                "model": model_version,
                "searched": searched,
                "limitations": json.dumps(
                    [
                        {
                            "camera_id": str(x.camera_id) if x.camera_id else None,
                            "camera_name": x.camera_name,
                            "issue": x.issue,
                            "detail": x.detail,
                        }
                        for x in limitations
                    ]  # fmt: skip
                ),
                "note": note,
                "actor": self._actor.id,
                "name": self._actor.full_name,
            },
        )

    async def add_candidate(
        self,
        search_id: UUID,
        rank: int,
        observation: Observation,
        similarity: float,
        band: Band,
        crop_path: str | None,
    ) -> None:
        await self._run(
            """INSERT INTO person_search_candidates (id, search_id, organization_id, campus_id, rank, observation_id,
                   camera_id, camera_name, location_label, track_id, observed_at, similarity, band, crop_path)
               VALUES (:candidate, :search, :org, :campus, :rank, :observation, :camera, :camera_name, :location,
                   :track, :observed_at, :similarity, :band, :crop)""",
            {
                "candidate": uuid4(),
                "search": search_id,
                "rank": rank,
                "observation": observation.id,
                "camera": observation.camera_id,
                "camera_name": observation.camera_name,
                "location": observation.location_label,
                "track": observation.track_id,
                "observed_at": observation.observed_at,
                "similarity": round(similarity, 4),
                "band": band.value,
                "crop": crop_path,
            },
        )

    async def set_candidate_count(self, search_id: UUID, count: int) -> None:
        await self._run(
            "UPDATE person_searches SET candidate_count = :count WHERE id = :search",
            {"search": search_id, "count": count},
        )

    async def searches(self, limit: int, offset: int) -> list[PersonSearch]:
        rows = await self._run(
            f"""SELECT {_SEARCH} FROM person_searches s WHERE s.organization_id = :org AND s.campus_id = :campus
                ORDER BY s.created_at DESC LIMIT :limit OFFSET :offset""",
            {"limit": limit, "offset": offset},
        )
        return [_search(r) for r in rows]

    async def search(self, search_id: UUID) -> PersonSearch | None:
        row = (
            await self._run(
                f"SELECT {_SEARCH} FROM person_searches s WHERE s.id = :search AND s.organization_id = :org "
                "AND s.campus_id = :campus",
                {"search": search_id},
            )
        ).first()
        if row is None:
            return None
        rows = await self._run(
            f"SELECT {_CANDIDATE} FROM person_search_candidates c WHERE c.search_id = :search ORDER BY c.rank",
            {"search": search_id},
        )
        return _search(row, [_candidate(r) for r in rows])

    async def candidate(self, candidate_id: UUID, *, lock: bool = False) -> Candidate | None:
        row = (
            await self._run(
                f"SELECT {_CANDIDATE} FROM person_search_candidates c WHERE c.id = :candidate "
                f"AND c.organization_id = :org AND c.campus_id = :campus{' FOR UPDATE' if lock else ''}",
                {"candidate": candidate_id},
            )
        ).first()
        return None if row is None else _candidate(row)

    async def review(self, candidate_id: UUID, review: Review, note: str | None) -> None:
        assert self._actor is not None
        pending = review is Review.PENDING
        await self._run(
            """UPDATE person_search_candidates SET review = :review, review_note = :note,
                   reviewed_by = :actor, reviewed_by_name = :name,
                   reviewed_at = CASE WHEN :pending THEN NULL ELSE now() END
               WHERE id = :candidate""",
            {
                "candidate": candidate_id,
                "review": review.value,
                "note": note,
                "actor": None if pending else self._actor.id,
                "name": None if pending else self._actor.full_name,
                "pending": pending,
            },
        )

    async def link_incident(self, candidate_id: UUID, incident_id: UUID | None) -> None:
        await self._run(
            "UPDATE person_search_candidates SET incident_id = :incident WHERE id = :candidate",
            {"candidate": candidate_id, "incident": incident_id},
        )

    async def set_evidence(self, candidate_id: UUID, evidence_id: UUID) -> None:
        await self._run(
            "UPDATE person_search_candidates SET evidence_id = :evidence WHERE id = :candidate",
            {"candidate": candidate_id, "evidence": evidence_id},
        )

    async def incident_candidates(self, incident_id: UUID) -> list[Candidate]:
        rows = await self._run(
            f"SELECT {_CANDIDATE} FROM person_search_candidates c WHERE c.incident_id = :incident "
            "ORDER BY c.observed_at",
            {"incident": incident_id},
        )
        return [_candidate(r) for r in rows]
