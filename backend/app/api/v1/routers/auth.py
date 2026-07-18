"""
Authentication endpoints (SRS §2.1, §16.1).

`POST /auth/register` is a Milestone 2 addition not listed in the
frozen SRS's §6.2 endpoint table — the SRS documents login/refresh/
logout but never specifies how the first user account is created.
Without it there would be no way to obtain a user at all, so it's
added here as the obvious, minimal complement, clearly separated from
the SRS-specified endpoints in this docstring so it's easy to revisit
if a different bootstrap mechanism (invite-only, SSO-first) is
preferred later.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, status

from app.api.v1.deps import (
    get_audit_log_repository,
    get_client_ip,
    get_current_user,
    get_login_service,
    get_logout_all_service,
    get_logout_service,
    get_refresh_service,
    get_register_service,
)
from app.api.v1.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.application.auth_service import (
    LoginService,
    LogoutAllService,
    LogoutService,
    RefreshTokenService,
    RegisterUserService,
)
from app.domain.entities import AuditLogEntry, User
from app.infrastructure.db.repositories.audit_log_repository import SqlAlchemyAuditLogRepository

router = APIRouter(prefix="/auth", tags=["auth"])


async def _write_audit_entry(
    audit_repo: SqlAlchemyAuditLogRepository,
    action: str,
    actor_id: UUID | None,
    ip_address: str | None,
) -> None:
    """SRS FR-1.6: all authentication events are written to the Audit Log."""
    await audit_repo.add(
        AuditLogEntry(
            id=uuid4(),
            organization_id=None,
            actor_id=actor_id,
            action=action,
            target_type="user",
            target_id=actor_id,
            ip_address=ip_address,
            created_at=datetime.now(UTC),
        )
    )


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user account (Milestone 2 addition; see module docstring)",
)
async def register(
    body: RegisterRequest,
    service: RegisterUserService = Depends(get_register_service),
    audit_repo: SqlAlchemyAuditLogRepository = Depends(get_audit_log_repository),
    client_ip: str | None = Depends(get_client_ip),
) -> User:
    user = await service.execute(body.email, body.password, body.full_name)
    await _write_audit_entry(audit_repo, "auth.register", user.id, client_ip)
    return user


@router.post("/login", response_model=TokenResponse, summary="Exchange credentials for tokens")
async def login(
    body: LoginRequest,
    request: Request,
    service: LoginService = Depends(get_login_service),
    audit_repo: SqlAlchemyAuditLogRepository = Depends(get_audit_log_repository),
    client_ip: str | None = Depends(get_client_ip),
) -> TokenResponse:
    result = await service.execute(
        body.email,
        body.password,
        user_agent=request.headers.get("user-agent"),
        ip_address=client_ip,
    )
    await _write_audit_entry(audit_repo, "auth.login", result.user.id, client_ip)
    return TokenResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        user=UserResponse.model_validate(result.user),
    )


@router.post(
    "/refresh", response_model=TokenResponse, summary="Rotate an access/refresh token pair"
)
async def refresh(
    body: RefreshRequest,
    service: RefreshTokenService = Depends(get_refresh_service),
    audit_repo: SqlAlchemyAuditLogRepository = Depends(get_audit_log_repository),
    client_ip: str | None = Depends(get_client_ip),
) -> TokenResponse:
    result = await service.execute(body.refresh_token)
    await _write_audit_entry(audit_repo, "auth.refresh", result.user.id, client_ip)
    return TokenResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        user=UserResponse.model_validate(result.user),
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Revoke a single session",
)
async def logout(
    body: LogoutRequest,
    audit_repo: SqlAlchemyAuditLogRepository = Depends(get_audit_log_repository),
    client_ip: str | None = Depends(get_client_ip),
    service: LogoutService = Depends(get_logout_service),
) -> None:
    await service.execute(body.refresh_token)
    await _write_audit_entry(audit_repo, "auth.logout", None, client_ip)


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Revoke every session for the current user (log out all devices)",
)
async def logout_all(
    current_user: User = Depends(get_current_user),
    service: LogoutAllService = Depends(get_logout_all_service),
    audit_repo: SqlAlchemyAuditLogRepository = Depends(get_audit_log_repository),
    client_ip: str | None = Depends(get_client_ip),
) -> None:
    await service.execute(current_user.id)
    await _write_audit_entry(audit_repo, "auth.logout_all", current_user.id, client_ip)


@router.get("/me", response_model=UserResponse, summary="Get the current authenticated user")
async def get_me(current_user: User = Depends(get_current_user)) -> User:
    return current_user
