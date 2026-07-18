"""
ORM model registry.

Importing this module has the side effect of registering every model
class on `Base.metadata` (SQLAlchemy's declarative registration). This
is what `alembic/env.py` needs imported before `--autogenerate` can see
the full schema — without it, `target_metadata` would be empty and
every migration would generate a "drop everything" diff.
"""

from __future__ import annotations

from app.infrastructure.db.models.audit_log import AuditLogModel
from app.infrastructure.db.models.authorization import AuthorizationRecordModel
from app.infrastructure.db.models.identity import SessionModel, UserModel
from app.infrastructure.db.models.organization import (
    OrganizationInvitationModel,
    OrganizationMemberModel,
    OrganizationModel,
)
from app.infrastructure.db.models.project import ProjectMemberModel, ProjectModel
from app.infrastructure.db.models.target import TargetModel

__all__ = [
    "AuditLogModel",
    "AuthorizationRecordModel",
    "SessionModel",
    "UserModel",
    "OrganizationInvitationModel",
    "OrganizationMemberModel",
    "OrganizationModel",
    "ProjectMemberModel",
    "ProjectModel",
    "TargetModel",
]
