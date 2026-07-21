"""
Domain exceptions.

These carry business meaning only — no HTTP status codes, no framework
imports. The API layer's exception handlers (`api/v1/error_handlers.py`)
are responsible for mapping each of these to the right RFC 7807
response. Keeping that mapping at the API boundary is what lets
`domain/`/`application/` stay framework-free.
"""

from __future__ import annotations

from uuid import UUID


class DomainError(Exception):
    """Base class for all domain-layer errors."""


# --- Auth ------------------------------------------------------------------


class EmailAlreadyRegisteredError(DomainError):
    def __init__(self, email: str) -> None:
        self.email = email
        super().__init__(f"An account with email '{email}' already exists.")


class InvalidCredentialsError(DomainError):
    def __init__(self) -> None:
        super().__init__("Invalid email or password.")


class InactiveUserError(DomainError):
    def __init__(self, user_id: UUID) -> None:
        self.user_id = user_id
        super().__init__(f"User {user_id} is inactive.")


class InvalidRefreshTokenError(DomainError):
    def __init__(self) -> None:
        super().__init__("Refresh token is invalid, expired, or has been revoked.")


# --- Authorization / RBAC ---------------------------------------------------


class InsufficientPermissionError(DomainError):
    def __init__(self, required_roles: tuple[str, ...]) -> None:
        self.required_roles = required_roles
        super().__init__(f"Requires one of roles: {', '.join(required_roles)}")


class NotAnOrganizationMemberError(DomainError):
    def __init__(self, organization_id: UUID) -> None:
        self.organization_id = organization_id
        super().__init__(f"User is not a member of organization {organization_id}.")


class NotAProjectMemberError(DomainError):
    def __init__(self, project_id: UUID) -> None:
        self.project_id = project_id
        super().__init__(f"User is not a member of project {project_id}.")


# --- Not found ---------------------------------------------------------------


class OrganizationNotFoundError(DomainError):
    def __init__(self, organization_id: UUID) -> None:
        self.organization_id = organization_id
        super().__init__(f"Organization {organization_id} not found.")


class ProjectNotFoundError(DomainError):
    def __init__(self, project_id: UUID) -> None:
        self.project_id = project_id
        super().__init__(f"Project {project_id} not found.")


class TargetNotFoundError(DomainError):
    def __init__(self, target_id: UUID) -> None:
        self.target_id = target_id
        super().__init__(f"Target {target_id} not found.")


class AuthorizationRecordNotFoundError(DomainError):
    def __init__(self, project_id: UUID) -> None:
        self.project_id = project_id
        super().__init__(f"No authorization record found for project {project_id}.")


# --- Project lifecycle -------------------------------------------------------


class InvalidProjectStateTransitionError(DomainError):
    def __init__(self, current: str, requested: str) -> None:
        self.current = current
        self.requested = requested
        super().__init__(f"Cannot transition project from '{current}' to '{requested}'.")


class ProjectNotAuthorizedError(DomainError):
    """Raised when a project attempts to become Active without a valid AuthorizationRecord."""

    def __init__(self, project_id: UUID) -> None:
        self.project_id = project_id
        super().__init__(
            f"Project {project_id} cannot become Active without an attached, "
            "currently-valid authorization record (SRS FR-2.3)."
        )


# --- Target validation ---------------------------------------------------------


class InvalidTargetValueError(DomainError):
    def __init__(self, value: str, target_type: str) -> None:
        self.value = value
        self.target_type = target_type
        super().__init__(f"'{value}' is not a valid {target_type}.")


# --- Scope Guard (SRS §16.3) ---------------------------------------------------


class OutOfScopeTargetError(DomainError):
    """
    Raised when one or more targets are not covered by an active
    authorization record. This is the exception the API layer maps to
    the exact `422 out-of-scope-target` problem+json shape from SRS §6.3.
    """

    def __init__(self, target_ids: tuple[UUID, ...]) -> None:
        self.target_ids = target_ids
        joined = ", ".join(str(t) for t in target_ids)
        super().__init__(f"Target(s) outside authorized scope: {joined}")


class NoActiveAuthorizationError(DomainError):
    def __init__(self, project_id: UUID) -> None:
        self.project_id = project_id
        super().__init__(f"Project {project_id} has no currently-active authorization record.")


class ProjectNotActiveError(DomainError):
    def __init__(self, project_id: UUID, current_state: str) -> None:
        self.project_id = project_id
        self.current_state = current_state
        super().__init__(
            f"Project {project_id} is not Active (current state: {current_state}); "
            "scan execution is only permitted for Active projects."
        )


# --- Scans (Milestone 3) -----------------------------------------------------


class ScanNotFoundError(DomainError):
    def __init__(self, scan_id: UUID) -> None:
        self.scan_id = scan_id
        super().__init__(f"Scan {scan_id} not found.")


class ScanNotCancellableError(DomainError):
    def __init__(self, scan_id: UUID, current_status: str) -> None:
        self.scan_id = scan_id
        self.current_status = current_status
        super().__init__(
            f"Scan {scan_id} cannot be cancelled from status '{current_status}' "
            "(only 'queued' or 'running' scans can be cancelled)."
        )


class PluginNotFoundError(DomainError):
    def __init__(self, plugin_name: str) -> None:
        self.plugin_name = plugin_name
        super().__init__(f"No registered plugin named '{plugin_name}'.")


class InvalidPluginConfigError(DomainError):
    def __init__(self, plugin_name: str, reason: str) -> None:
        self.plugin_name = plugin_name
        self.reason = reason
        super().__init__(f"Invalid configuration for plugin '{plugin_name}': {reason}")


# --- Assets & Findings (Milestone 4A) ---------------------------------------


class AssetNotFoundError(DomainError):
    def __init__(self, asset_id: UUID) -> None:
        self.asset_id = asset_id
        super().__init__(f"Asset {asset_id} not found.")


class FindingNotFoundError(DomainError):
    def __init__(self, finding_id: UUID) -> None:
        self.finding_id = finding_id
        super().__init__(f"Finding {finding_id} not found.")
