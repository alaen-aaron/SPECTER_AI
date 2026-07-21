"""
Scan use-case service (Milestone 3).

`create` deliberately does ALL of its validation — project exists,
project is Active, authorization record is currently valid, every
target belongs to the project and is within the authorized allow-list
— through a single call to `ScopeGuardService.validate_targets`,
rather than re-implementing any of those checks here. That's the same
service Milestone 2 already built and tested; duplicating its logic
here would create two places that could drift out of sync on exactly
the safety-critical logic the SRS is built around (§16.3).

Plugin config validation happens here too, BEFORE the scan is ever
persisted or queued — a malformed plugin request should never even
create a `queued` Scan row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.application.scope_guard_service import ScopeGuardService
from app.domain.entities import Scan
from app.domain.exceptions import ScanNotCancellableError, ScanNotFoundError
from app.domain.repositories import ScanRepository
from app.domain.value_objects import ScanStatus
from app.plugins.manager import PluginManager


class ScanTaskDispatcher(Protocol):
    """
    Boundary between `application/` and Celery. `ScanService` depends
    only on this Protocol, never on `celery_app` directly — the
    concrete implementation (which does call Celery) lives in
    `infrastructure/celery_app/dispatcher.py`. This is the same
    Dependency Inversion pattern used for password hashing/JWT in
    Milestone 2's `auth_service.py`.
    """

    def dispatch(self, scan_id: UUID) -> None: ...


@dataclass(frozen=True, slots=True)
class NullScanTaskDispatcher:
    """
    A dispatcher that does nothing — used by tests that only care about
    whether `ScanService.create` persisted the right `Scan` row and
    called Scope Guard correctly, without needing a real (or fake)
    Celery broker in the loop.
    """

    def dispatch(self, scan_id: UUID) -> None:
        return None


class ScanService:
    def __init__(
        self,
        scan_repository: ScanRepository,
        scope_guard: ScopeGuardService,
        plugin_manager: PluginManager,
        task_dispatcher: ScanTaskDispatcher,
    ) -> None:
        self._scans = scan_repository
        self._scope_guard = scope_guard
        self._plugin_manager = plugin_manager
        self._dispatcher = task_dispatcher

    async def create(
        self,
        project_id: UUID,
        plugin_name: str,
        plugin_config: dict[str, Any],
        target_ids: list[UUID],
        initiated_by: UUID,
    ) -> Scan:
        """
        Raises (all from the domain layer, mapped to HTTP by the API's
        error handlers): `ProjectNotFoundError`, `ProjectNotActiveError`,
        `NoActiveAuthorizationError`, `TargetNotFoundError`,
        `OutOfScopeTargetError` (all via Scope Guard), or
        `PluginNotFoundError`/`InvalidPluginConfigError` (plugin config).
        """
        # 1. Scope Guard — project active, authorization valid, every
        #    target in-project and in-scope. No bypass, no shortcut.
        await self._scope_guard.validate_targets(project_id, target_ids)

        # 2. Plugin config validated BEFORE persistence — a bad request
        #    never creates a queued scan row at all.
        self._plugin_manager.validate(plugin_name, plugin_config)

        scan = Scan(
            id=uuid4(),
            project_id=project_id,
            initiated_by=initiated_by,
            plugin=plugin_name,
            status=ScanStatus.QUEUED,
            target_ids=target_ids,
            plugin_config=plugin_config,
            created_at=datetime.now(UTC),
        )
        await self._scans.create(scan)

        # 3. Only after the scan is durably persisted do we hand it to
        #    the background worker — never the other way around.
        self._dispatcher.dispatch(scan.id)

        return scan

    async def get(self, scan_id: UUID) -> Scan:
        scan = await self._scans.get(scan_id)
        if scan is None:
            raise ScanNotFoundError(scan_id)
        return scan

    async def list_for_project(
        self, project_id: UUID, limit: int = 20, cursor: datetime | None = None
    ) -> list[Scan]:
        return await self._scans.list(project_id, limit=limit, cursor=cursor)

    async def cancel(self, scan_id: UUID) -> Scan:
        """
        Marks a scan cancelled if it's still `queued` or `running`.

        Scope note: this is a cooperative/soft cancellation for
        Milestone 3 — it updates the Scan row's status but does not
        forcibly terminate an already-running plugin subprocess. A hard
        kill requires tracking the Celery task id per scan and calling
        `celery_app.control.revoke(task_id, terminate=True)`, which
        needs a live broker+worker to meaningfully test; that wiring is
        flagged as a near-term follow-up, not silently skipped.
        """
        scan = await self.get(scan_id)
        if not scan.is_cancellable:
            raise ScanNotCancellableError(scan_id, scan.status.value)
        await self._scans.update_status(scan_id, ScanStatus.CANCELLED)
        scan.status = ScanStatus.CANCELLED
        return scan
