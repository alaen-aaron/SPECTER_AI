"""
Regression tests for `SqlAlchemyAssetRepository.upsert` statement
generation (no database required — this compiles the SQL).

Background: `AssetModel` maps the ORM attribute `metadata_` to the
database column literally named `metadata`. SQLAlchemy's
`on_conflict_do_update(set_=...)` treats string keys LITERALLY, so the
old `set_={"metadata_": ...}` emitted `SET metadata_ = ...`, which
Postgres rejected with

    UndefinedColumnError: column "metadata_" of relation "assets"
    does not exist

These tests pin the generated SQL to the real column name so the
regression cannot silently return.
"""

from __future__ import annotations

import warnings
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.domain.entities import Asset
from app.domain.value_objects import AssetType
from app.infrastructure.db.repositories.asset_repository import (
    _build_upsert_statement,
)


def _make_asset(metadata: dict) -> Asset:
    now = datetime.now(UTC)
    return Asset(
        id=uuid4(),
        project_id=uuid4(),
        asset_type=AssetType.HOST,
        value="10.0.0.5",
        first_seen=now,
        last_seen=now,
        in_scope=True,
        source_scan_id=uuid4(),
        metadata=metadata,
        created_at=now,
    )


def _compile_upsert(asset: Asset) -> str:
    stmt = _build_upsert_statement(asset, datetime.now(UTC))
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_upsert_on_conflict_updates_metadata_column_not_metadata_():
    """The SET clause must reference the real column `metadata`."""
    sql = _compile_upsert(_make_asset({"reachable": True}))

    assert "SET metadata_ =" not in sql
    assert "metadata =" in sql
    assert "DO UPDATE SET" in sql


def test_upsert_insert_column_list_uses_metadata():
    """The INSERT column list must reference `metadata`, not `metadata_`."""
    sql = _compile_upsert(_make_asset({}))

    assert "metadata_," not in sql
    assert "INSERT INTO assets" in sql
    assert "metadata" in sql


def test_upsert_conflict_target_is_dedup_columns():
    sql = _compile_upsert(_make_asset({}))

    assert "ON CONFLICT (project_id, asset_type, value) DO UPDATE SET" in sql


def test_upsert_compiles_without_sqlalchemy_warning():
    """
    The previous `values(metadata_=...)` string key tripped an
    SAWarning about a column key that did not match the table. Using
    the mapped attribute must compile cleanly.
    """
    asset = _make_asset({"reachable": True})
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _compile_upsert(asset)

    relevant = [
        str(w.message)
        for w in caught
        if "column" in str(w.message) and "metadata" in str(w.message)
    ]
    assert relevant == []
