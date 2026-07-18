"""Unit tests for `TargetService`, including target value validation (FR-3.1)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.application.target_service import TargetService
from app.domain.exceptions import InvalidTargetValueError, TargetNotFoundError
from app.domain.value_objects import TargetType
from tests.fakes import FakeTargetRepository


@pytest.fixture
def service() -> TargetService:
    return TargetService(FakeTargetRepository())


@pytest.mark.asyncio
async def test_create_valid_ip_target(service):
    project_id = uuid4()
    target = await service.create(project_id, "192.168.1.10", TargetType.IP)
    assert target.value == "192.168.1.10"
    assert target.in_scope is True


@pytest.mark.asyncio
async def test_create_rejects_invalid_ip(service):
    with pytest.raises(InvalidTargetValueError):
        await service.create(uuid4(), "not-an-ip", TargetType.IP)


@pytest.mark.asyncio
async def test_create_rejects_invalid_cidr(service):
    with pytest.raises(InvalidTargetValueError):
        await service.create(uuid4(), "10.0.0.0/999", TargetType.CIDR)


@pytest.mark.asyncio
async def test_create_valid_domain_target(service):
    target = await service.create(uuid4(), "lab.example.com", TargetType.DOMAIN)
    assert target.target_type is TargetType.DOMAIN


@pytest.mark.asyncio
async def test_create_rejects_url_without_scheme(service):
    with pytest.raises(InvalidTargetValueError):
        await service.create(uuid4(), "example.com/path", TargetType.URL)


@pytest.mark.asyncio
async def test_list_for_project_excludes_other_projects(service):
    project_a = uuid4()
    project_b = uuid4()
    await service.create(project_a, "10.0.0.1", TargetType.IP)
    await service.create(project_b, "10.0.0.2", TargetType.IP)

    targets_a = await service.list_for_project(project_a)
    assert len(targets_a) == 1
    assert targets_a[0].value == "10.0.0.1"


@pytest.mark.asyncio
async def test_soft_delete_removes_target_from_listing(service):
    project_id = uuid4()
    target = await service.create(project_id, "10.0.0.1", TargetType.IP)
    await service.soft_delete(target.id)

    with pytest.raises(TargetNotFoundError):
        await service.get(target.id)
    assert await service.list_for_project(project_id) == []


@pytest.mark.asyncio
async def test_update_revalidates_new_value(service):
    target = await service.create(uuid4(), "10.0.0.1", TargetType.IP)
    with pytest.raises(InvalidTargetValueError):
        await service.update(target.id, value="not-an-ip")
