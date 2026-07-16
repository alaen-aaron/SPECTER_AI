"""
Application layer.

Bounded context: use-case services that orchestrate one or more domain
repositories/entities inside a single transaction (e.g. LaunchScanService,
GenerateReportService — added in later milestones per the frozen SRS).

Non-goals: no direct SQL, no HTTP concerns, no framework request/response
objects. This layer depends only on `domain/` interfaces, never on
concrete `infrastructure/` implementations (Dependency Inversion).
"""
