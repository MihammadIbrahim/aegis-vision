"""AI Configuration (Org Admin page 17, Supervisor page 14; decisions D-024 and D-028).

* Admins and Supervisors see what the platform permits, what the Super Admin blocked for their organization, what
  their campus permits, each camera's switches, the effective modules and what a vision worker last reported
  running, with the stream health.
* Effective per camera: platform AND NOT organization-blocked AND campus AND camera. A camera without its own
  switches runs nothing (D-028: explicit opt-in).
* Campus modules and the Supervisor visibility of each module: Admins only, with their password; never beyond what
  the platform permits; a blocked module can never be switched on (403 AI_MODULE_BLOCKED_BY_PLATFORM).
* Camera modules (one camera, bulk, or with the camera's details): Admins and Supervisors, never beyond the campus.
  Supervisors neither see nor change modules hidden from them (403 AI_MODULE_HIDDEN_FROM_SUPERVISOR); their changes
  keep the hidden modules exactly as they were.
* Every change is audited (AI_SETTINGS_UPDATED / CAMERA_AI_SETTINGS_UPDATED / CAMERA_AI_BULK_UPDATED, category
  AI_SETTINGS).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID

from src.domain.entities.ai_configuration import (
    ALL_ON,
    MODULE_LABELS,
    NONE,
    AiModule,
    CameraAiConfig,
    CameraAiState,
    CampusAiSettings,
    ModuleSet,
)
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import Conflict, NotFound, PermissionDenied
from src.domain.repositories.i_audit_log_repo import IOrganizationAuditLog
from src.domain.repositories.i_unit_of_work import ITransaction
from src.domain.value_objects.enums import UserRole

# A worker report older than this means the camera is not being processed now.
RUNNING_FRESH = timedelta(seconds=60)
BULK_LIMIT = 200

BulkMode = Literal["ADD", "REMOVE", "SET"]


class IAiConfigRepository(Protocol):
    async def platform_modules(self) -> ModuleSet: ...

    async def organization_blocks(self) -> ModuleSet: ...

    async def campus_settings(self, actor: User) -> CampusAiSettings | None: ...

    async def save_campus(self, actor: User, modules: ModuleSet, supervisor_visible: ModuleSet) -> None: ...

    async def cameras(self, actor: User) -> list[CameraAiState]: ...

    async def save_camera(self, actor: User, camera_id: UUID, modules: ModuleSet) -> None: ...

    async def service_config(self, camera_id: UUID) -> CameraAiConfig | None: ...

    async def report_status(
        self, camera_id: UUID, modules: list[str], fps: float | None, error: str | None, worker: str | None
    ) -> str: ...


class IPasswordConfirmation(Protocol):
    async def confirm(self, current_password: str) -> None: ...


@dataclass(frozen=True, slots=True)
class CameraView:
    state: CameraAiState
    configured: ModuleSet  # as the caller may see it (Supervisors: hidden modules shown as off)
    effective: ModuleSet
    running_now: bool


@dataclass(frozen=True, slots=True)
class AiConfigurationView:
    platform: ModuleSet
    campus: ModuleSet
    campus_permitted: ModuleSet  # platform AND NOT blocked AND campus
    campus_customized: bool
    campus_updated_at: datetime | None
    campus_updated_by_name: str | None
    cameras: list[CameraView]
    org_blocked: ModuleSet = NONE
    visible: ModuleSet = ALL_ON  # the modules the caller sees (Supervisors: not the hidden ones)
    available: ModuleSet = NONE  # what the caller may switch on for a camera
    supervisor_visible: ModuleSet | None = None  # Admins only


@dataclass(frozen=True, slots=True)
class _Levels:
    platform: ModuleSet
    blocked: ModuleSet
    campus: ModuleSet
    supervisor_visible: ModuleSet
    stored: CampusAiSettings | None

    @property
    def permitted(self) -> ModuleSet:
        return self.platform.without(self.blocked) & self.campus

    def visible_to(self, actor: User) -> ModuleSet:
        return self.supervisor_visible if actor.role is UserRole.SUPERVISOR else ALL_ON


def _staff(actor: User) -> None:
    if actor.role not in (UserRole.ADMIN, UserRole.SUPERVISOR):
        raise PermissionDenied("Only Admins and Supervisors can open AI Configuration.", code="STAFF_ONLY")


def _names(modules: list[AiModule]) -> str:
    return ", ".join(MODULE_LABELS[m] for m in modules)


def _not_permitted(modules: list[AiModule], level: str) -> Conflict:
    return Conflict(f"Not permitted by the {level}: {_names(modules)}.", code="MODULE_NOT_PERMITTED")


def _blocked(modules: list[AiModule]) -> PermissionDenied:
    return PermissionDenied(
        f"Blocked for your organization by the platform: {_names(modules)}.", code="AI_MODULE_BLOCKED_BY_PLATFORM"
    )


def _hidden(modules: list[AiModule]) -> PermissionDenied:
    return PermissionDenied(
        f"Not available to Supervisors on this campus: {_names(modules)}.", code="AI_MODULE_HIDDEN_FROM_SUPERVISOR"
    )


def parse_modules(raw: Mapping[str, bool]) -> dict[str, bool]:
    """Validated ``{module key: on}`` (unknown keys are refused)."""
    keys = {m.value for m in AiModule}
    unknown = sorted(set(raw) - keys)
    if unknown:
        raise Conflict(f"Unknown AI modules: {', '.join(unknown)}.", code="UNKNOWN_AI_MODULE")
    return {k: bool(v) for k, v in raw.items()}


class AiConfiguration:
    def __init__(
        self,
        repository: IAiConfigRepository,
        audit: IOrganizationAuditLog,
        transaction: ITransaction,
        confirmation: IPasswordConfirmation | None = None,
        clock: type[datetime] = datetime,
    ) -> None:
        self._repo = repository
        self._audit = audit
        self._transaction = transaction
        self._confirmation = confirmation
        self._clock = clock

    async def _campus(self, actor: User) -> tuple[ModuleSet, CampusAiSettings | None]:
        stored = await self._repo.campus_settings(actor)
        return (stored.modules if stored else ModuleSet()), stored

    async def _levels(self, actor: User) -> _Levels:
        platform = await self._repo.platform_modules()
        blocked = await self._repo.organization_blocks()
        campus, stored = await self._campus(actor)
        visible = stored.supervisor_visible if stored else ALL_ON
        return _Levels(platform, blocked, campus, visible, stored)

    async def _confirm(self, current_password: str) -> None:
        assert self._confirmation is not None
        await self._confirmation.confirm(current_password)

    async def view(self, actor: User) -> AiConfigurationView:
        _staff(actor)
        levels = await self._levels(actor)
        permitted = levels.permitted
        visible = levels.visible_to(actor)
        now = datetime.now(UTC)
        cameras = [
            CameraView(
                state=c,
                configured=c.configured & visible,
                effective=permitted & c.configured & visible,
                running_now=c.reported_at is not None and now - c.reported_at <= RUNNING_FRESH,
            )
            for c in await self._repo.cameras(actor)
        ]
        stored = levels.stored
        return AiConfigurationView(
            platform=levels.platform & visible,
            campus=levels.campus & visible,
            campus_permitted=permitted & visible,
            campus_customized=stored is not None,
            campus_updated_at=stored.updated_at if stored else None,
            campus_updated_by_name=stored.updated_by_name if stored else None,
            cameras=cameras,
            org_blocked=levels.blocked & visible,
            visible=visible,
            available=permitted & visible,
            supervisor_visible=levels.supervisor_visible if actor.role is UserRole.ADMIN else None,
        )

    async def enabled_modules(self, actor: User) -> list[str]:
        """The modules the caller's campus permits (for the Organization Settings summary)."""
        levels = await self._levels(actor)
        return [m.value for m in levels.permitted.enabled()]

    async def update_campus(
        self,
        actor: User,
        modules: ModuleSet,
        current_password: str,
        supervisor_visible: ModuleSet | None = None,
    ) -> AiConfigurationView:
        if actor.role is not UserRole.ADMIN:
            raise PermissionDenied("Only an Admin can change the campus AI modules.", code="ADMIN_ONLY")
        levels = await self._levels(actor)
        blocked = [m for m in modules.enabled() if getattr(levels.blocked, m.value)]
        if blocked:
            raise _blocked(blocked)
        beyond = modules.beyond(levels.platform)
        if beyond:
            raise _not_permitted(beyond, "platform")
        visible = levels.supervisor_visible if supervisor_visible is None else supervisor_visible
        await self._confirm(current_password)
        await self._repo.save_campus(actor, modules, visible)
        metadata: dict[str, object] = {"changed": modules.changes_from(levels.campus)}
        visibility = visible.changes_from(levels.supervisor_visible)
        if visibility:
            metadata["supervisor_visible_changed"] = visibility
        await self._audit.record(actor, "AI_SETTINGS_UPDATED", "CAMPUS", actor.campus_id, metadata)
        saved = await self.view(actor)  # read before the commit ends the signed-in transaction
        await self._transaction.commit()
        return saved

    # --- Camera switches ------------------------------------------------------------------------------------

    def _checked(self, actor: User, levels: _Levels, before: ModuleSet, wanted: ModuleSet) -> ModuleSet:
        """``wanted`` validated for the caller; a Supervisor's hidden modules keep their ``before`` value."""
        visible = levels.visible_to(actor)
        turned_on = wanted.enabled()
        hidden = [m for m in turned_on if not getattr(visible, m.value)]
        if hidden:
            raise _hidden(hidden)
        blocked = [m for m in turned_on if getattr(levels.blocked, m.value)]
        if blocked:
            raise _blocked(blocked)
        beyond = wanted.beyond(levels.platform & levels.campus)
        if beyond:
            raise _not_permitted(beyond, "campus or the platform")
        return wanted.keeping(before, visible)

    async def _record_camera(self, actor: User, camera: CameraAiState | None, camera_id: UUID, name: str,
                             before: ModuleSet, after: ModuleSet) -> None:  # fmt: skip
        await self._audit.record(
            actor,
            "CAMERA_AI_SETTINGS_UPDATED",
            "CAMERA",
            camera_id,
            {"camera": camera.camera_name if camera else name, "changed": after.changes_from(before)},
        )

    async def update_camera(
        self, actor: User, camera_id: UUID, modules: ModuleSet, current_password: str
    ) -> AiConfigurationView:
        _staff(actor)
        cameras = {c.camera_id: c for c in await self._repo.cameras(actor)}
        camera = cameras.get(camera_id)
        if camera is None:
            raise NotFound("Camera not found.")
        levels = await self._levels(actor)
        after = self._checked(actor, levels, camera.configured, modules)
        await self._confirm(current_password)
        await self._repo.save_camera(actor, camera_id, after)
        await self._record_camera(actor, camera, camera_id, camera.camera_name, camera.configured, after)
        saved = await self.view(actor)
        await self._transaction.commit()
        return saved

    async def update_cameras_bulk(
        self,
        actor: User,
        camera_ids: list[UUID],
        mode: BulkMode,
        modules: Mapping[str, bool],
        current_password: str,
    ) -> list[CameraView]:
        """One click for many cameras of the caller's campus, all or nothing.

        ADD switches the selected modules on, REMOVE switches them off, SET replaces the switches with exactly the
        selected modules. Another campus's camera is "not found" and nothing changes."""
        _staff(actor)
        ids = list(dict.fromkeys(camera_ids))
        if not 1 <= len(ids) <= BULK_LIMIT:
            raise Conflict(f"Select between 1 and {BULK_LIMIT} cameras.", code="BULK_SELECTION_SIZE")
        selected = ModuleSet.of_keys(k for k, on in parse_modules(modules).items() if on)
        if mode != "SET" and not selected.enabled():
            raise Conflict("Select at least one AI feature.", code="NO_AI_MODULE_SELECTED")
        cameras = {c.camera_id: c for c in await self._repo.cameras(actor)}
        missing = [i for i in ids if i not in cameras]
        if missing:
            raise NotFound("Camera not found.", code="CAMERA_NOT_FOUND")
        levels = await self._levels(actor)
        visible = levels.visible_to(actor)
        hidden = [m for m in selected.enabled() if not getattr(visible, m.value)]
        if hidden:
            raise _hidden(hidden)
        plans: list[tuple[CameraAiState, ModuleSet]] = []
        for camera_id in ids:
            camera = cameras[camera_id]
            before = camera.configured
            if mode == "ADD":
                wanted = before.with_changes({m.value: True for m in selected.enabled()}) & visible
            elif mode == "REMOVE":
                wanted = before.without(selected) & visible
            else:
                wanted = selected
            # Only what this request switches on is checked against the levels above; switches already on stay.
            check = wanted if mode == "SET" else wanted.without(before)
            self._checked(actor, levels, before, check)
            plans.append((camera, wanted.keeping(before, visible)))
        await self._confirm(current_password)
        for camera, after in plans:
            await self._repo.save_camera(actor, camera.camera_id, after)
        await self._audit.record(
            actor,
            "CAMERA_AI_BULK_UPDATED",
            "CAMPUS",
            actor.campus_id,
            {
                "mode": mode,
                "modules": [m.value for m in selected.enabled()],
                "camera_count": len(plans),
                "cameras": [{"id": str(c.camera_id), "name": c.camera_name} for c, _ in plans][:BULK_LIMIT],
                "changed": sum(1 for c, after in plans if after != c.configured),
            },
        )
        updated = {c.camera_id for c, _ in plans}
        result = [c for c in (await self.view(actor)).cameras if c.state.camera_id in updated]
        await self._transaction.commit()
        return result

    # --- With the camera's details (Add / Edit Camera) ------------------------------------------------------

    async def resolve_for_camera(
        self, actor: User, requested: Mapping[str, bool] | None, camera_id: UUID | None
    ) -> ModuleSet | None:
        """The switches to save with a new or edited camera, validated before anything is written.

        A new camera without ``ai_modules`` gets every module off; an edit without ``ai_modules`` keeps the
        camera's switches (``None``). Missing keys keep their value; a Supervisor's hidden modules stay."""
        if actor.role not in (UserRole.ADMIN, UserRole.SUPERVISOR):
            return None
        if requested is None and camera_id is not None:
            return None
        before = NONE
        if camera_id is not None:
            current = {c.camera_id: c for c in await self._repo.cameras(actor)}.get(camera_id)
            before = current.configured if current else NONE
        if requested is None:
            return NONE
        levels = await self._levels(actor)
        wanted = before.with_changes(parse_modules(requested))
        self._checked(actor, levels, before, wanted.without(before))  # what this request switches on
        return wanted.keeping(before, levels.visible_to(actor))

    async def save_for_camera(
        self, actor: User, camera_id: UUID, camera_name: str, modules: ModuleSet, created: bool
    ) -> None:
        """Writes the switches resolved by :meth:`resolve_for_camera` in the camera's transaction (no commit)."""
        before = NONE
        if not created:
            current = {c.camera_id: c for c in await self._repo.cameras(actor)}.get(camera_id)
            before = current.configured if current else NONE
        await self._repo.save_camera(actor, camera_id, modules)
        if created or modules != before:
            await self._audit.record(
                actor,
                "CAMERA_AI_SETTINGS_UPDATED",
                "CAMERA",
                camera_id,
                {"camera": camera_name, "changed": modules.changes_from(before), "with_camera_details": True},
            )

    async def camera_modules(self, actor: User) -> dict[UUID, tuple[list[str], list[str]]]:
        """Per camera of the caller's campus: (selected, effective) module keys the caller may see. Empty for
        anyone but Admins and Supervisors."""
        if actor.role not in (UserRole.ADMIN, UserRole.SUPERVISOR):
            return {}
        view = await self.view(actor)
        return {
            c.state.camera_id: ([m.value for m in c.configured.enabled()], [m.value for m in c.effective.enabled()])
            for c in view.cameras
        }

    # --- Vision workers -------------------------------------------------------------------------------------

    async def service_config(self, camera_id: UUID) -> CameraAiConfig:
        config = await self._repo.service_config(camera_id)
        if config is None:
            raise NotFound("Camera not found.", code="CAMERA_NOT_FOUND")
        return config

    async def report_status(
        self, camera_id: UUID, modules: list[str], fps: float | None, error: str | None, worker: str | None
    ) -> None:
        if await self._repo.report_status(camera_id, modules, fps, error, worker) != "RECORDED":
            raise NotFound("Camera not found.", code="CAMERA_NOT_FOUND")
        await self._transaction.commit()
