"""
Domain layer.

Bounded context: core business entities and repository interfaces for
SPECTER_AI (Projects, Targets, Assets, Findings, etc. — added in later
milestones per the frozen SRS).

Non-goals: this package must never import from `api/`, `application/`,
or `infrastructure/`. Dependency direction always points inward, per
SRS §10.1 (Clean Architecture). It has zero framework dependencies
(no FastAPI, no SQLAlchemy) so domain logic is trivially unit-testable
in isolation.
"""
