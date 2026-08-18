"""
Repository-layer integration tests for assets (real Postgres — see
`tests/integration/conftest.py` for the skip-if-unreachable fixture).

These cover the two things fakes cannot verify about
`SqlAlchemyAssetRepository`:
  1. the `metadata` JSONB column actually round-trips through the
     model whose ORM attribute is `metadata_`;
  2. `upsert()`'s `ON CONFLICT DO UPDATE` runs against the real column
     name — the regression that emitted `SET metadata_ = ...` and blew
     up with `UndefinedColumnError` in the scan pipeline.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.entities import Asset, Organization, Project, Scan, User
from app.domain.value_objects import AssetType, ProjectState, ScanStatus
from app.infrastructure.db.repositories.asset_repository import (
    SqlAlchemyAssetRepository,
)
from app.infrastructure.db.repositories.identity_repository import (
    SqlAlchemyUserRepository,
)
from app.infrastructure.db.repositories.organization_repository import (
    SqlAlchemyOrganizationRepository,
)
from app.infrastructure.db.repositories.project_repository import (
    SqlAlchemyProjectRepository,
)
from app.infrastructure.db.repositories.scan_repository import SqlAlchemyScanRepository
from tests.integration.conftest import requires_postgres

pytestmark = requires_postgres


def _make_asset(
    project_id,
    *,
    value: str = "10.0.0.5",
    asset_type: AssetType = AssetType.HOST,
    metadata: dict | None = None,
    source_scan_id=None,
) -> Asset:
    now = datetime.now(UTC)
    return Asset(
        id=uuid4(),
        project_id=project_id,
        asset_type=asset_type,
        value=value,
        first_seen=now,
        last_seen=now,
        in_scope=True,
        source_scan_id=source_scan_id,
        metadata=metadata or {},
        created_at=now,
    )


async def _make_project(db_session) -> tuple[Organization, Project]:
    org_repo = SqlAlchemyOrganizationRepository(db_session)
    project_repo = SqlAlchemyProjectRepository(db_session)
    org = Organization(id=uuid4(), name="Asset Org", created_at=datetime.now(UTC))
    await org_repo.add(org)
    now = datetime.now(UTC)
    project = Project(
        id=uuid4(),
        organization_id=org.id,
        name="Asset Project",
        description=None,
        state=ProjectState.DRAFT,
        tags=[],
        client_metadata={},
        created_at=now,
        updated_at=now,
    )
    await project_repo.add(project)
    return org, project


async def _make_scan(db_session, project_id) -> Scan:
    user_repo = SqlAlchemyUserRepository(db_session)
    scan_repo = SqlAlchemyScanRepository(db_session)
    now = datetime.now(UTC)
    initiator = User(
        id=uuid4(),
        email=f"initiator-{uuid4().hex[:8]}@example.com",
        password_hash="hash",
        full_name="Initiator",
        is_active=True,
        created_at=now,
    )
    await user_repo.add(initiator)
    scan = Scan(
        id=uuid4(),
        project_id=project_id,
        initiated_by=initiator.id,
        plugin="nmap",
        status=ScanStatus.COMPLETED,
        target_ids=[uuid4()],
        plugin_config={},
        created_at=now,
        started_at=now,
        completed_at=now,
    )
    await scan_repo.create(scan)
    return scan


@pytest.mark.asyncio
async def test_asset_create_round_trips_metadata_jsonb(db_session):
    """Inserting an asset persists the `metadata` JSONB column despite the
    ORM attribute being named `metadata_`."""
    _, project = await _make_project(db_session)
    repo = SqlAlchemyAssetRepository(db_session)
    asset = _make_asset(project.id, metadata={"reachable": True, "ttl": 63})

    await repo.add(asset)

    fetched = await repo.get_by_id(asset.id)
    assert fetched is not None
    assert fetched.value == asset.value
    assert fetched.asset_type is AssetType.HOST
    assert fetched.metadata == {"reachable": True, "ttl": 63}


@pytest.mark.asyncio
async def test_asset_upsert_inserts_new_asset(db_session):
    _, project = await _make_project(db_session)
    repo = SqlAlchemyAssetRepository(db_session)
    asset = _make_asset(project.id, metadata={"host_up": True})

    created = await repo.upsert(asset)

    assert created.id == asset.id
    assert created.metadata == {"host_up": True}
    assert await repo.get_by_id(asset.id) is not None


@pytest.mark.asyncio
async def test_asset_upsert_on_conflict_updates_metadata_and_last_seen(db_session):
    """
    The core regression: a second upsert on the same dedup key
    (project_id, asset_type, value) must run the `ON CONFLICT` branch
    and update the `metadata` column — previously this emitted
    `SET metadata_ = ...` and raised UndefinedColumnError.
    """
    _, project = await _make_project(db_session)
    repo = SqlAlchemyAssetRepository(db_session)
    scan_1 = await _make_scan(db_session, project.id)
    scan_2 = await _make_scan(db_session, project.id)

    first = _make_asset(
        project.id, metadata={"host_up": True}, source_scan_id=scan_1.id
    )
    created = await repo.upsert(first)

    second = _make_asset(
        project.id,
        value=created.value,
        asset_type=created.asset_type,
        metadata={"host_up": True, "os": "Linux"},
        source_scan_id=scan_2.id,
    )
    second.id = created.id
    second.first_seen = created.first_seen
    second.last_seen = created.last_seen + timedelta(seconds=1)

    updated = await repo.upsert(second)

    assert updated.id == created.id
    assert updated.metadata == {"host_up": True, "os": "Linux"}
    assert updated.source_scan_id == scan_2.id
    assert updated.last_seen > created.last_seen
    assert updated.first_seen == created.first_seen


@pytest.mark.asyncio
async def test_asset_upsert_keeps_single_row_per_dedup_key(db_session):
    """The `uq_asset_dedup` constraint means N upserts yield one row."""
    _, project = await _make_project(db_session)
    repo = SqlAlchemyAssetRepository(db_session)

    first = await repo.upsert(_make_asset(project.id, metadata={"v": 1}))
    await repo.upsert(
        _make_asset(
            project.id,
            value=first.value,
            asset_type=first.asset_type,
            metadata={"v": 2},
        )
    )

    fetched = await repo.get_by_dedup(project.id, first.asset_type, first.value)
    assert fetched is not None
    assert fetched.id == first.id
    assert fetched.metadata == {"v": 2}

    all_assets = await repo.list_for_project(project.id)
    assert len(all_assets) == 1


@pytest.mark.asyncio
async def test_asset_upsert_with_source_scan_reference(db_session):
    """source_scan_id links the asset to the scan that discovered it."""
    _, project = await _make_project(db_session)
    repo = SqlAlchemyAssetRepository(db_session)
    scan = await _make_scan(db_session, project.id)

    asset = _make_asset(project.id, source_scan_id=scan.id)
    created = await repo.upsert(asset)

    assert created.source_scan_id == scan.id
