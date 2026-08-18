"""Reproduce the scan_asset_upsert_failed error to find the exact root cause."""
import sys, io, asyncio, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import os
os.environ["DATABASE_URL"] = "postgresql+asyncpg://specter:specter@localhost:5432/specter"

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.infrastructure.db.repositories.asset_repository import SqlAlchemyAssetRepository
from app.infrastructure.db.repositories.graph_repository import SqlAlchemyGraphRepository
from app.application.asset_service import AssetService
from app.application.graph_service import GraphService
from app.infrastructure.db.models.tool_result import ToolResultModel
from app.domain.entities import ToolResult

SCAN_ID = "26640fbf-0445-4d05-a7f2-7bf5288abb45"
PROJECT_ID = "a4e621ef-72c3-4f03-9922-ef843a3d19f3"

async def main():
    engine = create_async_engine("postgresql+asyncpg://specter:specter@localhost:5432/specter")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        from sqlalchemy import select
        res = await session.execute(select(ToolResultModel).where(ToolResultModel.scan_id == SCAN_ID))
        rows = res.scalars().all()
        print("tool results:", len(rows))
        for row in rows:
            print("plugin:", row.plugin, "target:", row.target)
            print("payload:", json.dumps(row.normalized_payload, default=str)[:2000])

        asset_repo = SqlAlchemyAssetRepository(session)
        graph_repo = SqlAlchemyGraphRepository(session)
        svc = AssetService(asset_repo, GraphService(graph_repo))

        for row in rows:
            tr = ToolResult(
                id=row.id, scan_id=row.scan_id, plugin=row.plugin, target=row.target,
                normalized_payload=row.normalized_payload or {}, raw_output_path=row.raw_output_path,
                created_at=row.created_at,
            )
            try:
                assets = await svc.upsert_from_tool_result(PROJECT_ID, tr)
                print("UPSERT OK assets:", [a.value for a in assets])
            except Exception as e:
                import traceback
                print("UPSERT FAILED:", type(e).__name__, e)
                traceback.print_exc()
    await engine.dispose()

asyncio.run(main())