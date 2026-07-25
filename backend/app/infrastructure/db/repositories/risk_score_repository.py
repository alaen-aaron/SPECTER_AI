"""SQLAlchemy implementation of `RiskScoreRepository` (SRS FR-7.3)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from app.domain.entities import RiskScore
from app.domain.value_objects import AIOutputReviewStatus, RiskScoreSource
from app.infrastructure.db.models.ai_engine import RiskScoreModel


def _to_entity(row: RiskScoreModel) -> RiskScore:
    return RiskScore(
        id=row.id,
        finding_id=row.finding_id,
        base_score=float(row.base_score),
        exposure_modifier=float(row.exposure_modifier),
        ai_rationale=row.ai_rationale,
        review_status=AIOutputReviewStatus(row.review_status),
        source=RiskScoreSource(row.source),
        computed_at=row.computed_at,
    )


class SqlAlchemyRiskScoreRepository:
    def __init__(self, session: SqlAsyncSession) -> None:
        self._session = session

    async def create(self, score: RiskScore) -> None:
        model = RiskScoreModel(
            id=score.id,
            finding_id=score.finding_id,
            base_score=score.base_score,
            exposure_modifier=score.exposure_modifier,
            ai_rationale=score.ai_rationale,
            review_status=score.review_status.value,
            source=score.source.value,
        )
        self._session.add(model)
        await self._session.flush()

    async def get(self, score_id: UUID) -> RiskScore | None:
        row = await self._session.get(RiskScoreModel, score_id)
        return _to_entity(row) if row else None

    async def get_by_finding(self, finding_id: UUID) -> RiskScore | None:
        stmt = select(RiskScoreModel).where(RiskScoreModel.finding_id == finding_id)
        result = await self._session.execute(stmt)
        row = result.scalars().first()
        return _to_entity(row) if row else None

    async def list_for_project(self, project_id: UUID) -> list[RiskScore]:
        from app.infrastructure.db.models.finding import FindingModel

        stmt = (
            select(RiskScoreModel)
            .join(FindingModel, FindingModel.id == RiskScoreModel.finding_id)
            .where(FindingModel.project_id == project_id)
            .order_by(RiskScoreModel.computed_at.desc())
        )
        result = await self._session.execute(stmt)
        return [_to_entity(row) for row in result.scalars().all()]

    async def update(self, score: RiskScore) -> None:
        row = await self._session.get(RiskScoreModel, score.id)
        if row is None:
            return
        row.base_score = score.base_score
        row.exposure_modifier = score.exposure_modifier
        row.ai_rationale = score.ai_rationale
        row.review_status = score.review_status.value
        row.source = score.source.value
        await self._session.flush()
