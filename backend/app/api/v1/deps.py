"""
Dependency-injection wiring (SRS §10.1: FastAPI's `Depends()` graph IS
the DI container — see the Milestone 1 note on why no separate
container library is used).

Three tiers, each depending only on the tier below:
  1. Repository providers  — wrap a request-scoped DB session
  2. Service providers      — application-layer use cases, built from repos
  3. Auth/permission deps   — `get_current_user`, `require_org_role(...)`,
                              `require_project_role(...)`

Routers only ever import from tier 2 and 3 — they never construct a
repository or service directly.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.asset_service import AssetService
from app.application.auth_service import (
    LoginService,
    LogoutAllService,
    LogoutService,
    RefreshTokenService,
    RegisterUserService,
)
from app.application.authorization_service import AuthorizationRecordService
from app.application.evidence_service import EvidenceService
from app.application.finding_service import FindingService
from app.application.graph_service import GraphService
from app.application.organization_service import OrganizationService
from app.application.project_service import ProjectService
from app.application.report_service import ReportService
from app.application.scan_service import ScanService, ScanTaskDispatcher
from app.application.schedule_service import ScheduleService
from app.application.scope_guard_service import ScopeGuardService
from app.application.target_service import TargetService
from app.application.workflow_service import WorkflowService, WorkflowTaskDispatcher
from app.core.config import Settings, get_settings
from app.domain.entities import OrganizationMember, ProjectMember, User
from app.domain.exceptions import InsufficientPermissionError, NotAProjectMemberError
from app.domain.value_objects import (
    ORGANIZATION_ADMIN_ROLES,
    OrganizationRole,
    ProjectRole,
)
from app.infrastructure.celery_app.dispatcher import (
    CeleryScanTaskDispatcher,
    CeleryWorkflowTaskDispatcher,
)
from app.infrastructure.db.repositories.asset_repository import SqlAlchemyAssetRepository
from app.infrastructure.db.repositories.audit_log_repository import SqlAlchemyAuditLogRepository
from app.infrastructure.db.repositories.authorization_repository import (
    SqlAlchemyAuthorizationRecordRepository,
)
from app.infrastructure.db.repositories.evidence_repository import (
    SqlAlchemyEvidenceRepository,
)
from app.infrastructure.db.repositories.finding_repository import SqlAlchemyFindingRepository
from app.infrastructure.db.repositories.graph_repository import SqlAlchemyGraphRepository
from app.infrastructure.db.repositories.identity_repository import (
    SqlAlchemySessionRepository,
    SqlAlchemyUserRepository,
)
from app.infrastructure.db.repositories.organization_repository import (
    SqlAlchemyOrganizationRepository,
)
from app.infrastructure.db.repositories.project_repository import SqlAlchemyProjectRepository
from app.infrastructure.db.repositories.report_repository import (
    SqlAlchemyReportRepository,
    SqlAlchemyReportVersionRepository,
)
from app.infrastructure.db.repositories.scan_repository import SqlAlchemyScanRepository
from app.infrastructure.db.repositories.target_repository import SqlAlchemyTargetRepository
from app.infrastructure.db.repositories.workflow_repository import (
    SqlAlchemyScheduleRepository,
    SqlAlchemyWorkflowExecutionRepository,
    SqlAlchemyWorkflowRepository,
    SqlAlchemyWorkflowStepRepository,
)
from app.infrastructure.db.session import get_db_session
from app.infrastructure.security.jwt import (
    InvalidAccessTokenError,
    create_access_token,
    decode_access_token,
)
from app.infrastructure.security.password_hasher import hash_password, verify_password
from app.infrastructure.security.tokens import generate_refresh_token, hash_refresh_token
from app.plugins.manager import PluginManager
from app.plugins.registry import registry as plugin_registry

# --- Tier 1: repositories ----------------------------------------------------


def get_user_repository(
    session: AsyncSession = Depends(get_db_session),
) -> SqlAlchemyUserRepository:
    return SqlAlchemyUserRepository(session)


def get_session_repository(
    session: AsyncSession = Depends(get_db_session),
) -> SqlAlchemySessionRepository:
    return SqlAlchemySessionRepository(session)


def get_organization_repository(
    session: AsyncSession = Depends(get_db_session),
) -> SqlAlchemyOrganizationRepository:
    return SqlAlchemyOrganizationRepository(session)


def get_project_repository(
    session: AsyncSession = Depends(get_db_session),
) -> SqlAlchemyProjectRepository:
    return SqlAlchemyProjectRepository(session)


def get_target_repository(
    session: AsyncSession = Depends(get_db_session),
) -> SqlAlchemyTargetRepository:
    return SqlAlchemyTargetRepository(session)


def get_authorization_repository(
    session: AsyncSession = Depends(get_db_session),
) -> SqlAlchemyAuthorizationRecordRepository:
    return SqlAlchemyAuthorizationRecordRepository(session)


def get_audit_log_repository(
    session: AsyncSession = Depends(get_db_session),
) -> SqlAlchemyAuditLogRepository:
    return SqlAlchemyAuditLogRepository(session)


def get_scan_repository(
    session: AsyncSession = Depends(get_db_session),
) -> SqlAlchemyScanRepository:
    return SqlAlchemyScanRepository(session)


def get_asset_repository(
    session: AsyncSession = Depends(get_db_session),
) -> SqlAlchemyAssetRepository:
    return SqlAlchemyAssetRepository(session)


def get_finding_repository(
    session: AsyncSession = Depends(get_db_session),
) -> SqlAlchemyFindingRepository:
    return SqlAlchemyFindingRepository(session)


def get_evidence_repository(
    session: AsyncSession = Depends(get_db_session),
) -> SqlAlchemyEvidenceRepository:
    return SqlAlchemyEvidenceRepository(session)


def get_report_repository(
    session: AsyncSession = Depends(get_db_session),
) -> SqlAlchemyReportRepository:
    return SqlAlchemyReportRepository(session)


def get_report_version_repository(
    session: AsyncSession = Depends(get_db_session),
) -> SqlAlchemyReportVersionRepository:
    return SqlAlchemyReportVersionRepository(session)


def get_graph_repository(
    session: AsyncSession = Depends(get_db_session),
) -> SqlAlchemyGraphRepository:
    return SqlAlchemyGraphRepository(session)


def get_workflow_repository(
    session: AsyncSession = Depends(get_db_session),
) -> SqlAlchemyWorkflowRepository:
    return SqlAlchemyWorkflowRepository(session)


def get_workflow_step_repository(
    session: AsyncSession = Depends(get_db_session),
) -> SqlAlchemyWorkflowStepRepository:
    return SqlAlchemyWorkflowStepRepository(session)


def get_workflow_execution_repository(
    session: AsyncSession = Depends(get_db_session),
) -> SqlAlchemyWorkflowExecutionRepository:
    return SqlAlchemyWorkflowExecutionRepository(session)


def get_schedule_repository(
    session: AsyncSession = Depends(get_db_session),
) -> SqlAlchemyScheduleRepository:
    return SqlAlchemyScheduleRepository(session)


# --- Tier 2: application services --------------------------------------------


def get_register_service(
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repository),
) -> RegisterUserService:
    return RegisterUserService(user_repo, hash_password)


def get_login_service(
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repository),
    session_repo: SqlAlchemySessionRepository = Depends(get_session_repository),
    settings: Settings = Depends(get_settings),
) -> LoginService:
    return LoginService(
        user_repository=user_repo,
        session_repository=session_repo,
        verify_password=verify_password,
        create_access_token=lambda uid: create_access_token(uid, settings),
        generate_refresh_token=generate_refresh_token,
        hash_refresh_token=hash_refresh_token,
    )


def get_refresh_service(
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repository),
    session_repo: SqlAlchemySessionRepository = Depends(get_session_repository),
    settings: Settings = Depends(get_settings),
) -> RefreshTokenService:
    return RefreshTokenService(
        user_repository=user_repo,
        session_repository=session_repo,
        create_access_token=lambda uid: create_access_token(uid, settings),
        generate_refresh_token=generate_refresh_token,
        hash_refresh_token=hash_refresh_token,
    )


def get_logout_service(
    session_repo: SqlAlchemySessionRepository = Depends(get_session_repository),
) -> LogoutService:
    return LogoutService(session_repo, hash_refresh_token)


def get_logout_all_service(
    session_repo: SqlAlchemySessionRepository = Depends(get_session_repository),
) -> LogoutAllService:
    return LogoutAllService(session_repo)


def get_organization_service(
    org_repo: SqlAlchemyOrganizationRepository = Depends(get_organization_repository),
) -> OrganizationService:
    return OrganizationService(org_repo)


def get_project_service(
    project_repo: SqlAlchemyProjectRepository = Depends(get_project_repository),
    auth_repo: SqlAlchemyAuthorizationRecordRepository = Depends(get_authorization_repository),
) -> ProjectService:
    return ProjectService(project_repo, auth_repo)


def get_target_service(
    target_repo: SqlAlchemyTargetRepository = Depends(get_target_repository),
) -> TargetService:
    return TargetService(target_repo)


def get_authorization_record_service(
    auth_repo: SqlAlchemyAuthorizationRecordRepository = Depends(get_authorization_repository),
) -> AuthorizationRecordService:
    return AuthorizationRecordService(auth_repo)


def get_scope_guard_service(
    project_repo: SqlAlchemyProjectRepository = Depends(get_project_repository),
    target_repo: SqlAlchemyTargetRepository = Depends(get_target_repository),
    auth_repo: SqlAlchemyAuthorizationRecordRepository = Depends(get_authorization_repository),
) -> ScopeGuardService:
    return ScopeGuardService(project_repo, target_repo, auth_repo)


def get_plugin_manager() -> PluginManager:
    """
    Ensures built-in plugins are registered (idempotent — `builtin.py`
    just re-registers the same names if imported more than once) before
    handing out a `PluginManager` bound to the process-wide registry.
    """
    import app.plugins.builtin  # noqa: F401 - side-effect import

    return PluginManager(plugin_registry)


def get_scan_task_dispatcher() -> ScanTaskDispatcher:
    return CeleryScanTaskDispatcher()


def get_scan_service(
    scan_repo: SqlAlchemyScanRepository = Depends(get_scan_repository),
    scope_guard: ScopeGuardService = Depends(get_scope_guard_service),
    plugin_manager: PluginManager = Depends(get_plugin_manager),
    dispatcher: ScanTaskDispatcher = Depends(get_scan_task_dispatcher),
) -> ScanService:
    return ScanService(scan_repo, scope_guard, plugin_manager, dispatcher)


def get_asset_service(
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    graph_repo: SqlAlchemyGraphRepository = Depends(get_graph_repository),
) -> AssetService:
    return AssetService(asset_repo, GraphService(graph_repo))


def get_finding_service(
    finding_repo: SqlAlchemyFindingRepository = Depends(get_finding_repository),
    asset_repo: SqlAlchemyAssetRepository = Depends(get_asset_repository),
    graph_repo: SqlAlchemyGraphRepository = Depends(get_graph_repository),
) -> FindingService:
    return FindingService(finding_repo, asset_repo, GraphService(graph_repo))


def get_evidence_service(
    evidence_repo: SqlAlchemyEvidenceRepository = Depends(get_evidence_repository),
    finding_repo: SqlAlchemyFindingRepository = Depends(get_finding_repository),
    settings: Settings = Depends(get_settings),
) -> EvidenceService:
    from app.infrastructure.storage.evidence_store import EvidenceStore

    store = EvidenceStore(str(settings.SCAN_ARTIFACTS_DIR))
    return EvidenceService(evidence_repo, store, finding_repo)


def get_report_service(
    report_repo: SqlAlchemyReportRepository = Depends(get_report_repository),
    report_version_repo: SqlAlchemyReportVersionRepository = Depends(
        get_report_version_repository
    ),
    finding_repo: SqlAlchemyFindingRepository = Depends(get_finding_repository),
    settings: Settings = Depends(get_settings),
) -> ReportService:
    return ReportService(
        report_repository=report_repo,
        report_version_repository=report_version_repo,
        finding_repository=finding_repo,
        artifacts_dir=str(settings.SCAN_ARTIFACTS_DIR),
    )


def get_graph_service(
    graph_repo: SqlAlchemyGraphRepository = Depends(get_graph_repository),
) -> GraphService:
    return GraphService(graph_repo)


def get_workflow_task_dispatcher() -> WorkflowTaskDispatcher:
    return CeleryWorkflowTaskDispatcher()


def get_workflow_service(
    workflow_repo: SqlAlchemyWorkflowRepository = Depends(get_workflow_repository),
    step_repo: SqlAlchemyWorkflowStepRepository = Depends(get_workflow_step_repository),
    execution_repo: SqlAlchemyWorkflowExecutionRepository = Depends(
        get_workflow_execution_repository
    ),
    dispatcher: WorkflowTaskDispatcher = Depends(get_workflow_task_dispatcher),
) -> WorkflowService:
    return WorkflowService(workflow_repo, step_repo, execution_repo, dispatcher)


def get_schedule_service(
    schedule_repo: SqlAlchemyScheduleRepository = Depends(get_schedule_repository),
    workflow_repo: SqlAlchemyWorkflowRepository = Depends(get_workflow_repository),
) -> ScheduleService:
    return ScheduleService(schedule_repo, workflow_repo)


# --- Tier 3: authentication + RBAC --------------------------------------------

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    settings: Settings = Depends(get_settings),
    user_repo: SqlAlchemyUserRepository = Depends(get_user_repository),
) -> User:
    """
    Decodes the bearer access token and loads the current user.

    Deliberately raises a plain `HTTPException`, not a `DomainError`:
    "malformed/missing bearer token" is a transport-layer concern, not
    a business rule, so it doesn't belong in `domain/exceptions.py`.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_access_token(credentials.credentials, settings)
    except InvalidAccessTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = await user_repo.get_by_id(UUID(payload.sub))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_org_role(
    *allowed_roles: OrganizationRole,
) -> Callable[..., Awaitable[OrganizationMember]]:
    """
    Dependency factory: returns a dependency that (a) requires the
    caller to be a member of `organization_id` (bound from the route's
    path parameter of the same name) and (b) requires their role to be
    one of `allowed_roles`. Raises `NotAnOrganizationMemberError` /
    `InsufficientPermissionError` (mapped to 403 by the error handler)
    on failure — enforced server-side per SRS §16.2, never trusting a
    frontend role display.
    """

    async def _checker(
        organization_id: UUID,
        current_user: User = Depends(get_current_user),
        org_service: OrganizationService = Depends(get_organization_service),
    ) -> OrganizationMember:
        member = await org_service.require_member(organization_id, current_user.id)
        if allowed_roles and member.role not in allowed_roles:
            raise InsufficientPermissionError(tuple(r.value for r in allowed_roles))
        return member

    return _checker


def require_project_role(
    *allowed_roles: ProjectRole,
) -> Callable[..., Awaitable[ProjectMember]]:
    """Project-scoped equivalent of `require_org_role` (FR-1.5: roles are
    scoped per-project, independently of the user's organization role)."""

    async def _checker(
        project_id: UUID,
        current_user: User = Depends(get_current_user),
        project_service: ProjectService = Depends(get_project_service),
    ) -> ProjectMember:
        member = await project_service.require_member(project_id, current_user.id)
        if allowed_roles and member.role not in allowed_roles:
            raise InsufficientPermissionError(tuple(r.value for r in allowed_roles))
        return member

    return _checker


def require_project_role_for_target(
    *allowed_roles: ProjectRole,
) -> Callable[..., Awaitable[ProjectMember]]:
    """
    Permission dependency for routes keyed by `target_id` alone (no
    `{project_id}` path segment to bind `require_project_role` to
    directly). Resolves the target's owning project first, then applies
    the exact same membership + role check — a target's permissions
    are always its project's permissions, never a separate RBAC layer.
    """

    async def _checker(
        target_id: UUID,
        current_user: User = Depends(get_current_user),
        target_service: TargetService = Depends(get_target_service),
        project_service: ProjectService = Depends(get_project_service),
    ) -> ProjectMember:
        target = await target_service.get(target_id)
        member = await project_service.require_member(target.project_id, current_user.id)
        if allowed_roles and member.role not in allowed_roles:
            raise InsufficientPermissionError(tuple(r.value for r in allowed_roles))
        return member

    return _checker


_SCAN_CAPABLE_PROJECT_ROLES = frozenset(
    {ProjectRole.OWNER, ProjectRole.ADMIN, ProjectRole.LEAD_TESTER, ProjectRole.TESTER}
)


async def _check_scan_launch_permission(
    project_id: UUID,
    current_user: User,
    project_service: ProjectService,
    org_service: OrganizationService,
) -> ProjectMember | OrganizationMember:
    """
    Shared logic behind both `require_scan_launch_permission` (routes
    keyed by `{project_id}`) and `require_scan_permission_for_scan`
    (routes keyed by `{scan_id}` alone, e.g. cancel) — one place decides
    what "authorized to launch/cancel a scan" means (Milestone 3 spec:
    "Only: Project Owner, Organization Admin, Authorized Users").

    "Authorized Users" is read as: any project member whose role does
    testing work (Owner/Admin/Lead Tester/Tester) — Read-Only and
    Client Viewer cannot launch or cancel scans. An organization
    Owner/Admin is also allowed even without explicit project
    membership, given org-level oversight responsibility.
    """
    try:
        project_member = await project_service.require_member(project_id, current_user.id)
    except NotAProjectMemberError:
        project_member = None

    if project_member is not None and project_member.role in _SCAN_CAPABLE_PROJECT_ROLES:
        return project_member

    project = await project_service.get(project_id)
    org_member = await org_service.get_member_or_none(project.organization_id, current_user.id)
    if org_member is not None and org_member.role in ORGANIZATION_ADMIN_ROLES:
        return org_member

    raise InsufficientPermissionError(
        (
            "project:owner",
            "project:admin",
            "project:lead_tester",
            "project:tester",
            "org:owner",
            "org:admin",
        )
    )


def require_scan_launch_permission() -> (
    Callable[..., Awaitable[ProjectMember | OrganizationMember]]
):
    """For routes with a `{project_id}` path segment (launching a scan)."""

    async def _checker(
        project_id: UUID,
        current_user: User = Depends(get_current_user),
        project_service: ProjectService = Depends(get_project_service),
        org_service: OrganizationService = Depends(get_organization_service),
    ) -> ProjectMember | OrganizationMember:
        return await _check_scan_launch_permission(
            project_id, current_user, project_service, org_service
        )

    return _checker


def require_scan_permission_for_scan() -> (
    Callable[..., Awaitable[ProjectMember | OrganizationMember]]
):
    """
    For routes keyed by `{scan_id}` alone (get/cancel a single scan) —
    resolves the scan's owning project first, then applies the exact
    same permission rule as launching one. A scan's permissions are
    always its project's permissions, never a separate RBAC layer —
    the same principle Milestone 2 established for
    `require_project_role_for_target`.
    """

    async def _checker(
        scan_id: UUID,
        current_user: User = Depends(get_current_user),
        scan_service: ScanService = Depends(get_scan_service),
        project_service: ProjectService = Depends(get_project_service),
        org_service: OrganizationService = Depends(get_organization_service),
    ) -> ProjectMember | OrganizationMember:
        scan = await scan_service.get(scan_id)
        return await _check_scan_launch_permission(
            scan.project_id, current_user, project_service, org_service
        )

    return _checker


def require_scan_view_permission() -> Callable[..., Awaitable[ProjectMember]]:
    """For `GET /scans/{scan_id}` — any project member may view a scan,
    unlike launching/cancelling one, which needs `_SCAN_CAPABLE_PROJECT_ROLES`."""

    async def _checker(
        scan_id: UUID,
        current_user: User = Depends(get_current_user),
        scan_service: ScanService = Depends(get_scan_service),
        project_service: ProjectService = Depends(get_project_service),
    ) -> ProjectMember:
        scan = await scan_service.get(scan_id)
        return await project_service.require_member(scan.project_id, current_user.id)

    return _checker


def require_project_role_for_asset(
    *allowed_roles: ProjectRole,
) -> Callable[..., Awaitable[ProjectMember]]:
    """Permission dependency for routes keyed by `asset_id` alone."""

    async def _checker(
        asset_id: UUID,
        current_user: User = Depends(get_current_user),
        asset_service: AssetService = Depends(get_asset_service),
        project_service: ProjectService = Depends(get_project_service),
    ) -> ProjectMember:
        asset = await asset_service.get(asset_id)
        member = await project_service.require_member(asset.project_id, current_user.id)
        if allowed_roles and member.role not in allowed_roles:
            raise InsufficientPermissionError(tuple(r.value for r in allowed_roles))
        return member

    return _checker


def require_finding_view_permission() -> Callable[..., Awaitable[ProjectMember]]:
    """For `GET /findings/{finding_id}` — any project member may view."""

    async def _checker(
        finding_id: UUID,
        current_user: User = Depends(get_current_user),
        finding_service: FindingService = Depends(get_finding_service),
        project_service: ProjectService = Depends(get_project_service),
    ) -> ProjectMember:
        finding = await finding_service.get(finding_id)
        return await project_service.require_member(finding.project_id, current_user.id)

    return _checker


def require_finding_edit_permission() -> (
    Callable[..., Awaitable[ProjectMember | OrganizationMember]]
):
    """For `PATCH /findings/{finding_id}/status` — scan-capable roles or org admin."""

    async def _checker(
        finding_id: UUID,
        current_user: User = Depends(get_current_user),
        finding_service: FindingService = Depends(get_finding_service),
        project_service: ProjectService = Depends(get_project_service),
        org_service: OrganizationService = Depends(get_organization_service),
    ) -> ProjectMember | OrganizationMember:
        finding = await finding_service.get(finding_id)
        return await _check_scan_launch_permission(
            finding.project_id, current_user, project_service, org_service
        )

    return _checker


def get_client_ip(request: Request) -> str | None:
    """Best-effort client IP extraction for audit logging (SRS §16.5)."""
    if request.client is None:
        return None
    return request.client.host


def require_report_view_permission() -> Callable[..., Awaitable[ProjectMember]]:
    """Permission dependency for report routes keyed by `{report_id}`."""

    async def _checker(
        report_id: UUID,
        current_user: User = Depends(get_current_user),
        report_service: ReportService = Depends(get_report_service),
        project_service: ProjectService = Depends(get_project_service),
    ) -> ProjectMember:
        report = await report_service.get(report_id)
        return await project_service.require_member(report.project_id, current_user.id)

    return _checker
