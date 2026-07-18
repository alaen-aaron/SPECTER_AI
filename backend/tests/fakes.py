"""
In-memory fake repositories.

Each class here satisfies its corresponding `domain.repositories`
Protocol *structurally* (no inheritance needed — that's the point of
using `Protocol` in the domain layer). These let every `application/`
service be unit-tested in full isolation, with no Postgres, no Docker,
and no network — a deliberate consequence of Dependency Inversion
(SRS §10.1, §21).

These are test doubles, not a "second implementation to maintain" —
they intentionally skip things like SQL-level cascade behavior, which
is instead covered by the smaller set of real-database integration
tests in `tests/integration/` (skipped automatically if no DB is
reachable).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.domain.entities import (
    AuditLogEntry,
    AuthorizationRecord,
    Organization,
    OrganizationInvitation,
    OrganizationMember,
    Project,
    ProjectMember,
    Session,
    Target,
    User,
)
from app.domain.value_objects import OrganizationRole, ProjectRole


class FakeUserRepository:
    def __init__(self) -> None:
        self._by_id: dict[UUID, User] = {}

    async def get_by_id(self, user_id: UUID) -> User | None:
        return self._by_id.get(user_id)

    async def get_by_email(self, email: str) -> User | None:
        normalized = email.lower()
        for user in self._by_id.values():
            if user.email.lower() == normalized:
                return user
        return None

    async def add(self, user: User) -> None:
        self._by_id[user.id] = user


class FakeSessionRepository:
    def __init__(self) -> None:
        self._by_id: dict[UUID, Session] = {}

    async def add(self, session: Session) -> None:
        self._by_id[session.id] = session

    async def get_by_id(self, session_id: UUID) -> Session | None:
        return self._by_id.get(session_id)

    async def get_by_token_hash(self, token_hash: str) -> Session | None:
        for session in self._by_id.values():
            if session.refresh_token_hash == token_hash:
                return session
        return None

    async def revoke(self, session_id: UUID) -> None:
        session = self._by_id.get(session_id)
        if session is not None:
            session.revoked_at = datetime.now(UTC)

    async def revoke_all_for_user(self, user_id: UUID) -> None:
        for session in self._by_id.values():
            if session.user_id == user_id and session.revoked_at is None:
                session.revoked_at = datetime.now(UTC)


class FakeOrganizationRepository:
    def __init__(self) -> None:
        self._orgs: dict[UUID, Organization] = {}
        self._members: dict[tuple[UUID, UUID], OrganizationMember] = {}
        self._invitations: dict[UUID, OrganizationInvitation] = {}

    async def get_by_id(self, organization_id: UUID) -> Organization | None:
        org = self._orgs.get(organization_id)
        if org is None or org.is_deleted:
            return None
        return org

    async def list_for_user(self, user_id: UUID) -> list[Organization]:
        org_ids = {oid for (oid, uid) in self._members if uid == user_id}
        return [self._orgs[oid] for oid in org_ids if not self._orgs[oid].is_deleted]

    async def add(self, organization: Organization) -> None:
        self._orgs[organization.id] = organization

    async def update(self, organization: Organization) -> None:
        self._orgs[organization.id] = organization

    async def soft_delete(self, organization_id: UUID) -> None:
        org = self._orgs.get(organization_id)
        if org is not None:
            org.deleted_at = datetime.now(UTC)

    async def add_member(self, member: OrganizationMember) -> None:
        self._members[(member.organization_id, member.user_id)] = member

    async def get_member(self, organization_id: UUID, user_id: UUID) -> OrganizationMember | None:
        return self._members.get((organization_id, user_id))

    async def list_members(self, organization_id: UUID) -> list[OrganizationMember]:
        return [m for (oid, _), m in self._members.items() if oid == organization_id]

    async def update_member_role(
        self, organization_id: UUID, user_id: UUID, role: OrganizationRole
    ) -> None:
        member = self._members.get((organization_id, user_id))
        if member is not None:
            member.role = role

    async def add_invitation(self, invitation: OrganizationInvitation) -> None:
        self._invitations[invitation.id] = invitation

    async def list_invitations(self, organization_id: UUID) -> list[OrganizationInvitation]:
        return [i for i in self._invitations.values() if i.organization_id == organization_id]


class FakeProjectRepository:
    def __init__(self) -> None:
        self._projects: dict[UUID, Project] = {}
        self._members: dict[tuple[UUID, UUID], ProjectMember] = {}

    async def get_by_id(self, project_id: UUID) -> Project | None:
        project = self._projects.get(project_id)
        if project is None or project.is_deleted:
            return None
        return project

    async def list_for_organization(self, organization_id: UUID) -> list[Project]:
        return [
            p
            for p in self._projects.values()
            if p.organization_id == organization_id and not p.is_deleted
        ]

    async def add(self, project: Project) -> None:
        self._projects[project.id] = project

    async def update(self, project: Project) -> None:
        self._projects[project.id] = project

    async def soft_delete(self, project_id: UUID) -> None:
        project = self._projects.get(project_id)
        if project is not None:
            project.deleted_at = datetime.now(UTC)

    async def add_member(self, member: ProjectMember) -> None:
        self._members[(member.project_id, member.user_id)] = member

    async def get_member(self, project_id: UUID, user_id: UUID) -> ProjectMember | None:
        return self._members.get((project_id, user_id))

    async def list_members(self, project_id: UUID) -> list[ProjectMember]:
        return [m for (pid, _), m in self._members.items() if pid == project_id]

    async def update_member_role(self, project_id: UUID, user_id: UUID, role: ProjectRole) -> None:
        member = self._members.get((project_id, user_id))
        if member is not None:
            member.role = role


class FakeTargetRepository:
    def __init__(self) -> None:
        self._targets: dict[UUID, Target] = {}

    async def get_by_id(self, target_id: UUID) -> Target | None:
        target = self._targets.get(target_id)
        if target is None or target.is_deleted:
            return None
        return target

    async def list_for_project(self, project_id: UUID) -> list[Target]:
        return [
            t for t in self._targets.values() if t.project_id == project_id and not t.is_deleted
        ]

    async def add(self, target: Target) -> None:
        self._targets[target.id] = target

    async def update(self, target: Target) -> None:
        self._targets[target.id] = target

    async def soft_delete(self, target_id: UUID) -> None:
        target = self._targets.get(target_id)
        if target is not None:
            target.deleted_at = datetime.now(UTC)


class FakeAuthorizationRecordRepository:
    def __init__(self) -> None:
        self._records: dict[UUID, AuthorizationRecord] = {}

    async def get_by_id(self, record_id: UUID) -> AuthorizationRecord | None:
        return self._records.get(record_id)

    async def get_active_for_project(
        self, project_id: UUID, on_date: datetime
    ) -> AuthorizationRecord | None:
        candidates = [
            r
            for r in self._records.values()
            if r.project_id == project_id and r.authorized_from <= on_date.date() <= r.authorized_to
        ]
        return candidates[0] if candidates else None

    async def list_for_project(self, project_id: UUID) -> list[AuthorizationRecord]:
        return [r for r in self._records.values() if r.project_id == project_id]

    async def add(self, record: AuthorizationRecord) -> None:
        self._records[record.id] = record


class FakeAuditLogRepository:
    def __init__(self) -> None:
        self._entries: list[AuditLogEntry] = []

    async def add(self, entry: AuditLogEntry) -> None:
        self._entries.append(entry)

    async def list_for_organization(self, organization_id: UUID) -> list[AuditLogEntry]:
        return [e for e in self._entries if e.organization_id == organization_id]
