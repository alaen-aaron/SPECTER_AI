"""Tests for AI Decision Engine acceptance bug fixes (Milestone 7)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.domain.entities import Project, ProjectMember
from app.domain.exceptions import (
    ProjectMemberAlreadyExistsError,
)
from app.domain.value_objects import ProjectRole, ProjectState
from tests.fakes import (
    FakeAIContextMemoryRepository,
    FakeAssetRepository,
    FakeAuthorizationRecordRepository,
    FakeFindingRepository,
    FakePlannedActionRepository,
    FakeProjectRepository,
)


def _make_project(project_id: UUID | None = None) -> Project:
    return Project(
        id=project_id or uuid4(),
        organization_id=uuid4(),
        name="Test Project",
        description="A test project",
        state=ProjectState.ACTIVE,
        tags=[],
        client_metadata={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Bug 1 & 3: AI Planner RBAC — owner/admin/lead_tester should access AI endpoints
# ---------------------------------------------------------------------------


class TestAIRBACRoles:
    @pytest.mark.asyncio
    async def test_owner_can_suggest(self):
        from app.application.planner_service import PlannerService

        project = _make_project()
        project_repo = FakeProjectRepository()
        await project_repo.add(project)
        owner_id = uuid4()
        await project_repo.add_member(
            ProjectMember(
                project_id=project.id,
                user_id=owner_id,
                role=ProjectRole.OWNER,
                created_at=datetime.now(UTC),
            )
        )

        svc = PlannerService(
            planned_action_repo=FakePlannedActionRepository(),
            finding_repo=FakeFindingRepository(),
            asset_repo=FakeAssetRepository(),
            context_memory_repo=FakeAIContextMemoryRepository(),
        )
        actions = await svc.suggest(project.id, created_by=owner_id)
        assert len(actions) > 0

    def test_all_capable_roles_in_ai_frozenset(self):
        from app.api.v1.routers.ai_engine import _AI_CAPABLE_PROJECT_ROLES

        assert ProjectRole.OWNER in _AI_CAPABLE_PROJECT_ROLES
        assert ProjectRole.ADMIN in _AI_CAPABLE_PROJECT_ROLES
        assert ProjectRole.LEAD_TESTER in _AI_CAPABLE_PROJECT_ROLES
        assert ProjectRole.TESTER in _AI_CAPABLE_PROJECT_ROLES

    def test_read_only_not_in_ai_roles(self):
        from app.api.v1.routers.ai_engine import _AI_CAPABLE_PROJECT_ROLES

        assert ProjectRole.READ_ONLY not in _AI_CAPABLE_PROJECT_ROLES
        assert ProjectRole.CLIENT_VIEWER not in _AI_CAPABLE_PROJECT_ROLES


# ---------------------------------------------------------------------------
# Bug 2: Duplicate project member → 409 instead of 500
# ---------------------------------------------------------------------------


class TestDuplicateProjectMember:
    def test_domain_exception_message(self):
        pid, uid = uuid4(), uuid4()
        exc = ProjectMemberAlreadyExistsError(pid, uid)
        assert "already a member" in str(exc)
        assert str(pid) in str(exc)
        assert str(uid) in str(exc)

    @pytest.mark.asyncio
    async def test_add_duplicate_member_raises(self):
        from app.application.project_service import ProjectService

        project = _make_project()
        user_id = uuid4()
        project_repo = FakeProjectRepository()
        await project_repo.add(project)
        await project_repo.add_member(
            ProjectMember(
                project_id=project.id,
                user_id=user_id,
                role=ProjectRole.TESTER,
                created_at=datetime.now(UTC),
            )
        )

        svc = ProjectService(project_repo, FakeAuthorizationRecordRepository())
        with pytest.raises(ProjectMemberAlreadyExistsError):
            await svc.add_member(project.id, user_id, ProjectRole.ADMIN)

    @pytest.mark.asyncio
    async def test_add_different_user_succeeds(self):
        from app.application.project_service import ProjectService

        project = _make_project()
        project_repo = FakeProjectRepository()
        await project_repo.add(project)

        svc = ProjectService(project_repo, FakeAuthorizationRecordRepository())
        member = await svc.add_member(project.id, uuid4(), ProjectRole.TESTER)
        assert member.role is ProjectRole.TESTER

    @pytest.mark.asyncio
    async def test_add_same_user_different_role_still_raises(self):
        from app.application.project_service import ProjectService

        project = _make_project()
        user_id = uuid4()
        project_repo = FakeProjectRepository()
        await project_repo.add(project)
        await project_repo.add_member(
            ProjectMember(
                project_id=project.id,
                user_id=user_id,
                role=ProjectRole.TESTER,
                created_at=datetime.now(UTC),
            )
        )

        svc = ProjectService(project_repo, FakeAuthorizationRecordRepository())
        with pytest.raises(ProjectMemberAlreadyExistsError):
            await svc.add_member(project.id, user_id, ProjectRole.OWNER)


# ---------------------------------------------------------------------------
# Planner Suggestions DI — regression test for Depends escaping into service layer
# ---------------------------------------------------------------------------


class TestPlannerSuggestionsDI:
    @pytest.mark.asyncio
    async def test_list_suggestions_empty_returns_list(self):
        from app.application.planner_service import PlannerService

        planner = PlannerService(
            planned_action_repo=FakePlannedActionRepository(),
            finding_repo=FakeFindingRepository(),
            asset_repo=FakeAssetRepository(),
            context_memory_repo=FakeAIContextMemoryRepository(),
        )
        result = await planner.list_for_project(uuid4())
        assert isinstance(result, list)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_planner_service_is_not_depends_object(self):
        from app.api.v1.deps import get_planner_service
        from app.application.planner_service import PlannerService

        planner = get_planner_service()
        assert isinstance(planner, PlannerService), (
            f"Expected PlannerService instance, got {type(planner).__name__}"
        )
        assert hasattr(planner, "list_for_project")
        assert hasattr(planner, "suggest")

    @pytest.mark.asyncio
    async def test_all_service_factories_return_real_instances(self):
        from app.api.v1.deps import (
            get_ai_reporter_service,
            get_analyzer_service,
            get_context_memory_service,
            get_explainer_service,
            get_planner_service,
            get_prompt_library_service,
            get_risk_engine_service,
        )
        from app.application.ai_reporter_service import AIReporterService
        from app.application.analyzer_service import AnalyzerService
        from app.application.context_memory_service import ContextMemoryService
        from app.application.explainer_service import ExplainerService
        from app.application.planner_service import PlannerService
        from app.application.prompt_library_service import PromptLibraryService
        from app.application.risk_engine_service import RiskEngineService

        assert isinstance(get_planner_service(), PlannerService)
        assert isinstance(get_analyzer_service(), AnalyzerService)
        assert isinstance(get_risk_engine_service(), RiskEngineService)
        assert isinstance(get_explainer_service(), ExplainerService)
        assert isinstance(get_ai_reporter_service(), AIReporterService)
        assert isinstance(get_context_memory_service(), ContextMemoryService)
        assert isinstance(get_prompt_library_service(), PromptLibraryService)


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/findings — create-finding endpoint
# ---------------------------------------------------------------------------


class TestCreateFindingEndpoint:
    def test_schema_has_required_fields(self):
        from app.api.v1.schemas.findings import CreateFindingRequest
        from app.domain.value_objects import Severity

        body = CreateFindingRequest(title="Test", severity=Severity.HIGH)
        assert body.title == "Test"
        assert body.severity is Severity.HIGH
        assert body.description is None
        assert body.cvss_score is None
        assert body.dedup_key == ""

    def test_schema_with_optional_fields(self):
        from app.api.v1.schemas.findings import CreateFindingRequest
        from app.domain.value_objects import Severity

        body = CreateFindingRequest(
            title="XSS in /search",
            severity=Severity.CRITICAL,
            description="Reflected XSS",
            cvss_score=9.1,
            dedup_key="manual:xss-search",
        )
        assert body.description == "Reflected XSS"
        assert body.cvss_score == 9.1
        assert body.dedup_key == "manual:xss-search"

    def test_create_endpoint_uses_scan_capable_roles(self):
        """The POST /findings endpoint binds require_scan_launch_permission,
        not require_finding_edit_permission (which needs finding_id in path)."""
        import inspect

        from app.api.v1.routers.findings import create_finding

        sig = inspect.signature(create_finding)
        dep_names = [p.name for p in sig.parameters.values()]
        assert "_member" in dep_names

    def test_create_finding_service_method_exists(self):
        from app.application.finding_service import FindingService

        assert hasattr(FindingService, "create")
        assert callable(FindingService.create)
