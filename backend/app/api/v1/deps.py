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

from app.application.auth_service import (
    LoginService,
    LogoutAllService,
    LogoutService,
    RefreshTokenService,
    RegisterUserService,
)
from app.application.authorization_service import AuthorizationRecordService
from app.application.organization_service import OrganizationService
from app.application.project_service import ProjectService
from app.application.scope_guard_service import ScopeGuardService
from app.application.target_service import TargetService
from app.core.config import Settings, get_settings
from app.domain.entities import OrganizationMember, ProjectMember, User
from app.domain.exceptions import InsufficientPermissionError
from app.domain.value_objects import OrganizationRole, ProjectRole
from app.infrastructure.db.repositories.audit_log_repository import SqlAlchemyAuditLogRepository
from app.infrastructure.db.repositories.authorization_repository import (
    SqlAlchemyAuthorizationRecordRepository,
)
from app.infrastructure.db.repositories.identity_repository import (
    SqlAlchemySessionRepository,
    SqlAlchemyUserRepository,
)
from app.infrastructure.db.repositories.organization_repository import (
    SqlAlchemyOrganizationRepository,
)
from app.infrastructure.db.repositories.project_repository import SqlAlchemyProjectRepository
from app.infrastructure.db.repositories.target_repository import SqlAlchemyTargetRepository
from app.infrastructure.db.session import get_db_session
from app.infrastructure.security.jwt import (
    InvalidAccessTokenError,
    create_access_token,
    decode_access_token,
)
from app.infrastructure.security.password_hasher import hash_password, verify_password
from app.infrastructure.security.tokens import generate_refresh_token, hash_refresh_token

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


def get_client_ip(request: Request) -> str | None:
    """Best-effort client IP extraction for audit logging (SRS §16.5)."""
    if request.client is None:
        return None
    return request.client.host
