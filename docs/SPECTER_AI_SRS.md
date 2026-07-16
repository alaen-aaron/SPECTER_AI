# SPECTER_AI
## Software Requirements Specification (SRS)
**Autonomous Offensive Security Platform**

Version 1.1 — Draft for Phase 1 Planning
Classification: Internal / Pre-Implementation
Revision note: v1.1 adds the Knowledge Graph as a first-class subsystem (§2.14, §15A), sitting downstream of the Correlation Engine and feeding the Attack Graph, AI Engine, and Report Engine.

> Scope note: SPECTER_AI is designed exclusively for authorized security testing — home labs, HTB/VulnHub/CTF environments, and internal assessments with documented written permission. Every design decision below assumes an operator has already obtained authorization; the platform enforces scope boundaries technically wherever possible (see §16, Security Model).

---

## Table of Contents

1. Executive Overview
2. Functional Requirements
3. Non-Functional Requirements
4. Architecture
5. Database Design
6. API Design
7. Plugin Architecture
8. AI Architecture
9. Frontend Architecture
10. Backend Architecture
11. Folder Structure
12. Deployment Architecture
13. Event Flow
14. Sequence Diagrams
15. Data Flow Diagrams
15A. Knowledge Graph Subsystem
16. Security Model
17. User Stories
18. Roadmap
19. Development Phases
20. Testing Strategy
21. Coding Standards
22. Future Expansion

---

## 1. Executive Overview

### 1.1 Product Vision
SPECTER_AI is an orchestration and intelligence layer that sits above existing open-source offensive security tooling (Nmap, Nuclei, ffuf, BloodHound, etc.). It does not reimplement scanning logic — it schedules tools, normalizes their output into a common data model, correlates findings across tools and time, applies an AI reasoning layer for prioritization and explanation, and produces analyst-grade and executive-grade reporting.

The differentiator versus a "scanner with a dashboard" is the **Decision Layer**: a pipeline that takes raw, noisy, multi-tool output and turns it into a ranked, explained, evidence-backed attack narrative — the way a senior pentester would triage a day's worth of scan output.

### 1.2 Problem Statement
Independent tool output is siloed, inconsistent in format, and time-consuming to correlate. A tester running Nmap, Nuclei, Subfinder, and BloodHound against one target ends up with five different report formats and no unified view of blast radius. Junior testers in particular struggle to prioritize what to attack next. SPECTER_AI addresses this gap.

### 1.3 What SPECTER_AI Is Not
- Not a replacement for the underlying tools — it is a control plane.
- Not an autonomous "hack anything" agent — the AI never executes commands directly.
- Not intended, designed, marketed, or licensed for use against systems without explicit written authorization.

### 1.4 Primary Users
| Persona | Need |
|---|---|
| Solo bug bounty / HTB player | Fast recon-to-report pipeline in a home lab |
| Internal red team | Multi-target project tracking, historical trend, team collaboration |
| Security consultancy | Client-ready PDF reports, evidence chain, RBAC per engagement |
| SOC/Blue team (secondary) | Read-only visibility into what an authorized red team found, for purple-team exercises |

### 1.5 Success Criteria
- A tester can stand up the platform via `docker compose up` and run a full recon→scan→report cycle against a lab target in under 30 minutes.
- Findings from at least 15 distinct tools normalize into one schema without data loss.
- AI-generated executive summaries are reviewed and edited by a human before being marked "final" (human-in-the-loop is mandatory, not optional).

---

## 2. Functional Requirements

### 2.1 Authentication & RBAC
- FR-1.1: Support local username/password auth with Argon2id hashing.
- FR-1.2: Support JWT access tokens (short-lived, ~15 min) + refresh tokens (rotating, stored hashed).
- FR-1.3: Support optional OIDC/SSO integration (Phase 3+).
- FR-1.4: RBAC roles: `Owner`, `Admin`, `Lead Tester`, `Tester`, `Read-Only`, `Client Viewer`.
- FR-1.5: Permissions are scoped per-Organization and per-Project (not global), so a user can be Admin on one engagement and Read-Only on another.
- FR-1.6: All authentication events are written to the Audit Log (§16).

### 2.2 Organizations & Projects
- FR-2.1: Multi-tenant: an Organization contains Projects; a Project contains Targets.
- FR-2.2: A Project has a lifecycle state: `Draft → Authorized → Active → Reporting → Closed → Archived`.
- FR-2.3: A Project cannot move to `Active` until an authorization record (signed scope document reference, start/end date, allowed target list) is attached. This is a hard gate in the workflow engine, not just a UI nudge.
- FR-2.4: Projects support tagging, custom fields, and client metadata.

### 2.3 Targets & Asset Inventory
- FR-3.1: Targets can be IP, CIDR range, domain, or URL.
- FR-3.2: The Asset Inventory is a living, deduplicated table of discovered hosts, subdomains, ports, services, and technologies, built incrementally from every scan.
- FR-3.3: Each asset tracks first-seen, last-seen, and source-scan lineage.
- FR-3.4: Assets outside the authorized target list are flagged `out-of-scope` and excluded from active scanning by the Scope Guard (see §16.3) — they are visible for awareness but the plugin execution layer refuses to run tools against them.

### 2.4 Recon Engine
- FR-4.1: Chains passive recon (Subfinder, Assetfinder, dnsx, httpx) into a normalized subdomain/asset list.
- FR-4.2: Supports scheduled re-recon for continuous monitoring engagements (Phase 4).
- FR-4.3: Deduplicates and diffs recon runs to highlight new assets since last run.

### 2.5 Plugin System
- FR-5.1: Every security tool integration is an isolated plugin implementing a fixed interface (§7).
- FR-5.2: Plugins declare required inputs, output schema, and resource requirements in a manifest (`plugin.yaml`).
- FR-5.3: Plugins run in isolated containers (one container per invocation, ephemeral).
- FR-5.4: Plugin registry supports enable/disable per Organization and per Project.
- FR-5.5: Third-party/community plugins go through a manifest validation step before being loadable (Phase 4+).

### 2.6 Scan Orchestrator & Workflow Engine
- FR-6.1: Users can build multi-stage workflows (DAGs) chaining plugins, e.g., `Subfinder → httpx → Nuclei`.
- FR-6.2: Workflows support conditional branches (e.g., "only run Nikto if httpx reports an HTTP server").
- FR-6.3: The Scheduler supports on-demand, cron-based, and event-triggered execution (event triggers land in Phase 3).
- FR-6.4: Celery-backed task queue with per-project concurrency limits to avoid overwhelming lab/target infrastructure.
- FR-6.5: Every scan execution is rate-limit and scope-checked before dispatch (§16.3).

### 2.7 AI Decision Engine
- FR-7.1: Planner suggests the next logical recon/scan step based on current asset state (suggestion only, requires human approval to execute).
- FR-7.2: Analyzer correlates findings across tools/time to reduce duplicate/noisy findings into unified Findings.
- FR-7.3: Risk Engine scores findings using a deterministic base (CVSS-derived + exposure context) with an AI-generated rationale layer on top — the score itself is never solely an LLM hallucination, it's computed, then explained.
- FR-7.4: Explainer generates human-readable descriptions of a finding, in plain English, with a "why this matters" and "how to fix" section.
- FR-7.5: Reporter drafts Executive Summary and per-finding narrative text for human review/edit before report finalization.
- FR-7.6: All AI output is visually watermarked in the UI as "AI-drafted — pending human review" until a tester explicitly approves it.

### 2.8 Attack Graph
- FR-8.1: Graph view (React Flow) showing hosts, services, credentials, and lateral movement paths (fed primarily by BloodHound plugin output for AD environments, and by correlated recon data for network/web engagements).
- FR-8.2: Nodes are clickable, linking to underlying evidence and findings.
- FR-8.3: Graph highlights AI-suggested shortest path to a defined "crown jewel" target, purely as a visualization/planning aid.

### 2.9 Evidence Collection
- FR-9.1: Every finding can attach evidence: raw tool output, screenshots, terminal session logs (via xterm.js recording), request/response pairs.
- FR-9.2: Evidence is stored content-addressed (hash-named) in object storage with a DB pointer, immutable once written.
- FR-9.3: Chain-of-custody metadata (who, when, from which scan) is mandatory on all evidence.

### 2.10 Dashboards
- FR-10.1: Executive Dashboard: risk posture over time, top findings by business impact, no raw tool jargon.
- FR-10.2: Technical Dashboard: live scan status, queue depth, per-plugin findings feed.
- FR-10.3: Historical Trends: finding count/severity over time, mean-time-to-remediate (for internal assessment repeats).

### 2.11 Reporting
- FR-11.1: Generate PDF/DOCX pentest reports (§11 in Reporting section below) from a template system.
- FR-11.2: Reports are versioned; a report is immutable once marked "Final" (amendments create a new version).
- FR-11.3: Support redaction of sensitive fields for client-distributed copies vs internal full copies.

### 2.12 Collaboration & Notifications
- FR-12.1: Comment threads on findings, @mentions.
- FR-12.2: Notification channels: in-app, email, Slack/Teams webhook (Phase 3).
- FR-12.3: Real-time updates via WebSocket for scan status and new findings.

### 2.13 Audit & API
- FR-13.1: Full audit log of auth events, RBAC changes, scan launches, report exports, evidence access.
- FR-13.2: REST API mirrors all UI capability; API keys scoped like user roles (§6).

### 2.14 Knowledge Graph
- FR-14.1: Every Asset, Finding, Credential, Technology, and Evidence record is also represented as a node in a project-scoped knowledge graph, with typed, directed edges expressing real relationships (`hosts`, `runs`, `exposes`, `authenticates_as`, `vulnerable_to`, `derived_from`, `communicates_with`).
- FR-14.2: The graph is built incrementally and automatically as normalized findings/assets are persisted — it is a projection of the relational data, not a separately-maintained dataset, so it can never drift out of sync with the source of truth.
- FR-14.3: Supports path queries: "shortest path from node A to node B", "all nodes reachable from a given foothold within N hops", "all findings that touch a given credential."
- FR-14.4: Powers Impact Analysis: given a hypothetical compromise of one asset, show every downstream asset/finding reachable from it (feeds directly into the Attack Graph UI, §2.8).
- FR-14.5: Powers richer reporting: the Report Engine can pull "blast radius" narratives directly from graph traversals instead of an analyst manually tracing connections across findings.
- FR-14.6: Graph queries are read-only from the AI Engine's perspective — the AI can query and reason over the graph (e.g., for Planner suggestions) but graph mutations only ever happen through the same validated normalization pipeline as everything else (§7.4), never as a direct AI write.

---

## 3. Non-Functional Requirements

| Category | Requirement |
|---|---|
| Performance | Plugin dispatch overhead < 500ms; dashboard API p95 < 300ms for up to 50k assets/project |
| Scalability | Horizontal scaling of Celery workers; stateless API layer behind a load balancer |
| Availability | Single-node Docker Compose for self-host; documented HA topology for enterprise (Phase 5) |
| Security | Least-privilege plugin containers, secrets never logged, encryption at rest for evidence store |
| Auditability | Every state-changing action logged with actor, timestamp, before/after state |
| Maintainability | Modular monorepo, enforced interfaces, >80% test coverage on domain/service layers |
| Portability | Fully Dockerized; no host-level tool installs required |
| Compliance | Data retention configurable per Organization; GDPR-style data export/delete for user accounts |
| Localization | English at launch; i18n-ready string tables in frontend (Phase 4+) |
| Observability | Structured JSON logs, OpenTelemetry traces, Prometheus metrics endpoint |

---

## 4. Architecture

### 4.1 Style
Modular, service-oriented monolith at launch (single deployable backend, strictly layered internally), designed so any bounded context (Plugin execution, AI engine, Reporting) can be extracted into its own service later without a rewrite — because the internal module boundaries mirror future service boundaries from day one.

### 4.2 High-Level Component Diagram (textual)

```
                        ┌───────────────────────────┐
                        │        Frontend (React)   │
                        │  Dashboards / Attack Graph │
                        └─────────────┬─────────────┘
                                      │ HTTPS / WSS
                        ┌─────────────▼─────────────┐
                        │        API Gateway         │
                        │   FastAPI (REST + WS)      │
                        │  Auth, RBAC, Rate Limiting  │
                        └──────┬───────────┬─────────┘
             ┌──────────────────┘           └─────────────────┐
   ┌─────────▼─────────┐                          ┌───────────▼──────────┐
   │  Core Domain Svc   │                          │   AI Decision Engine  │
   │ Projects/Targets/   │                          │ Planner/Analyzer/     │
   │ Assets/Findings     │◄────────normalized───────┤ Reporter/Explainer    │
   │ Reporting/Evidence  │        findings           └───────────┬──────────┘
   └─────────┬──────────┘                                        │ prompts/context
             │ enqueue                                            │
   ┌─────────▼──────────┐                              ┌──────────▼─────────┐
   │  Celery Task Queue  │                              │  LLM Provider Layer │
   │   (Redis broker)    │                              │ Ollama / OpenAI-API │
   └─────────┬──────────┘                              └────────────────────┘
             │ dispatch
   ┌─────────▼──────────┐
   │   Plugin Manager     │──spawns──►  Ephemeral tool containers (Nmap, Nuclei, ...)
   │  (isolation, scope    │            each: run() → parse() → normalize() → validate()
   │   guard, timeouts)    │
   └─────────┬──────────┘
             │ normalized ToolResult
   ┌─────────▼──────────┐
   │   PostgreSQL          │
   │   Object Storage (S3/ │
   │   MinIO) for evidence │
   └─────────┬─────────────┘
             │ projected (async, on write)
   ┌─────────▼──────────┐
   │  Knowledge Graph      │◄──── queried by ────┐
   │  (nodes: assets/       │                      │
   │   findings/creds/tech/ │              Attack Graph UI,
   │   evidence; typed       │              AI Engine (read-only),
   │   directed edges)       │              Report Engine
   └───────────────────────┘
```

The pipeline sequence for a piece of platform "knowledge" is therefore:

```
Core Platform → Plugin Manager → AI Engine → Correlation Engine
   → Report Engine → Workflow Engine → Policy Engine (Scope Guard)
   → Knowledge Graph
```

Every subsystem in that chain either writes data the graph projects from, or reads the graph to do its own job better (the AI Engine and Report Engine are graph *consumers*; only the Correlation Engine's output — validated Findings/Assets — is a graph *source*, alongside Plugin Manager output directly).

### 4.3 Key Architectural Decisions
- **The AI never touches the plugin execution path directly.** It emits a `PlannedAction` object; a human must approve; only then does the Workflow Engine enqueue it. This is a hard architectural boundary, not a prompt-level guideline, enforced by the API layer refusing any execution request that lacks an `approved_by` field.
- **Plugins are process-isolated** (own container, no shared filesystem with API, network-namespaced to only reach approved targets) so a compromised or buggy plugin can't pivot into platform infrastructure.
- **Normalization is mandatory before persistence** — nothing tool-specific ever leaks into the core schema; the core `Finding`/`ToolResult` tables are tool-agnostic.
- **Object storage is separate from the relational DB** for anything binary (screenshots, pcap, raw tool logs) — DB stores only pointers + hashes.
- **The Knowledge Graph is a projection, not a second source of truth.** It is derived entirely from validated Assets/Findings/Evidence already in Postgres; nothing writes to the graph that didn't first pass through the same normalization/validation pipeline as everything else. This means the graph can be rebuilt from scratch at any time from relational data alone — a deliberate recoverability property, not an afterthought.

---

## 5. Database Design

### 5.1 Entity List
Users, Organizations, OrganizationMembers, Projects, ProjectMembers, Targets, Assets, Scans, Workflows, WorkflowSteps, Plugins, Tasks, ToolResults, Findings, Evidence, Reports, ReportVersions, Notifications, ApiKeys, Sessions, AuditLogs, RiskScores, AuthorizationRecords.

### 5.2 Core Schema (annotated DDL excerpt)

```sql
-- Organizations & Users
CREATE TABLE organizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email CITEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name VARCHAR(255),
    is_active BOOLEAN NOT NULL DEFAULT true,
    mfa_secret TEXT,                     -- nullable, encrypted at rest
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE organization_members (
    organization_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(50) NOT NULL,           -- Owner/Admin/Member
    PRIMARY KEY (organization_id, user_id)
);

-- Projects & Authorization gating
CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    state VARCHAR(30) NOT NULL DEFAULT 'draft',  -- draft/authorized/active/reporting/closed/archived
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT valid_state CHECK (state IN ('draft','authorized','active','reporting','closed','archived'))
);

CREATE TABLE authorization_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    document_reference TEXT NOT NULL,   -- pointer to signed scope doc in evidence store
    authorized_from DATE NOT NULL,
    authorized_to DATE NOT NULL,
    allowed_targets JSONB NOT NULL,      -- list of CIDRs/domains explicitly in scope
    approved_by UUID NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE project_members (
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(50) NOT NULL,  -- Owner/Admin/Lead Tester/Tester/Read-Only/Client Viewer
    PRIMARY KEY (project_id, user_id)
);

-- Targets & Assets
CREATE TABLE targets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    value VARCHAR(255) NOT NULL,        -- IP / CIDR / domain / URL
    target_type VARCHAR(20) NOT NULL,   -- ip/cidr/domain/url
    in_scope BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_targets_project ON targets(project_id);

CREATE TABLE assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    asset_type VARCHAR(30) NOT NULL,    -- host/subdomain/service/technology/credential
    value VARCHAR(500) NOT NULL,
    parent_asset_id UUID REFERENCES assets(id),
    first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    in_scope BOOLEAN NOT NULL DEFAULT true,
    source_scan_id UUID,                -- FK added after scans table below
    metadata JSONB DEFAULT '{}'
);
CREATE INDEX idx_assets_project_type ON assets(project_id, asset_type);
CREATE UNIQUE INDEX uq_asset_dedup ON assets(project_id, asset_type, value);

-- Plugins, Scans, Tasks
CREATE TABLE plugins (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) UNIQUE NOT NULL,   -- e.g. 'nmap', 'nuclei'
    version VARCHAR(50) NOT NULL,
    category VARCHAR(50) NOT NULL,       -- recon/scanning/fuzzing/creds/etc
    manifest JSONB NOT NULL,             -- inputs, outputs, resource limits
    enabled BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE scans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    workflow_id UUID,                    -- nullable if ad-hoc single-plugin run
    status VARCHAR(30) NOT NULL DEFAULT 'queued', -- queued/running/completed/failed/cancelled
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    initiated_by UUID NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_id UUID NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    plugin_id UUID NOT NULL REFERENCES plugins(id),
    target_id UUID NOT NULL REFERENCES targets(id),
    status VARCHAR(30) NOT NULL DEFAULT 'queued',
    celery_task_id VARCHAR(255),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    error_message TEXT
);
CREATE INDEX idx_tasks_scan ON tasks(scan_id);

CREATE TABLE tool_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    raw_output_pointer TEXT NOT NULL,    -- object storage key
    normalized_payload JSONB NOT NULL,   -- tool-agnostic normalized structure
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Findings & Risk
CREATE TABLE findings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    asset_id UUID REFERENCES assets(id),
    title VARCHAR(500) NOT NULL,
    description TEXT,
    ai_explanation TEXT,                 -- AI-drafted, human-reviewable
    severity VARCHAR(20) NOT NULL,       -- info/low/medium/high/critical
    status VARCHAR(30) NOT NULL DEFAULT 'open', -- open/confirmed/false_positive/remediated
    cvss_score NUMERIC(3,1),
    dedup_key TEXT NOT NULL,             -- for correlation-engine merge
    reviewed_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_findings_project_severity ON findings(project_id, severity);

CREATE TABLE finding_sources (      -- many-to-many: a finding can be corroborated by multiple tool_results
    finding_id UUID REFERENCES findings(id) ON DELETE CASCADE,
    tool_result_id UUID REFERENCES tool_results(id) ON DELETE CASCADE,
    PRIMARY KEY (finding_id, tool_result_id)
);

CREATE TABLE risk_scores (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    finding_id UUID NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    base_score NUMERIC(4,2) NOT NULL,     -- deterministic, computed
    exposure_modifier NUMERIC(4,2) NOT NULL DEFAULT 0,
    ai_rationale TEXT,                    -- explanation only, not the score source
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Evidence
CREATE TABLE evidence (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    finding_id UUID REFERENCES findings(id) ON DELETE CASCADE,
    evidence_type VARCHAR(30) NOT NULL,  -- screenshot/raw_log/session_recording/request_response
    storage_pointer TEXT NOT NULL,
    content_hash VARCHAR(128) NOT NULL,
    collected_by UUID NOT NULL REFERENCES users(id),
    collected_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Reports
CREATE TABLE reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'draft', -- draft/final
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE report_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id UUID NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    version_number INT NOT NULL,
    file_pointer TEXT NOT NULL,
    is_redacted BOOLEAN NOT NULL DEFAULT false,
    generated_by UUID NOT NULL REFERENCES users(id),
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (report_id, version_number)
);

-- Notifications, API keys, Sessions, Audit
CREATE TABLE notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type VARCHAR(50) NOT NULL,
    payload JSONB NOT NULL,
    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    key_hash TEXT NOT NULL,
    scopes JSONB NOT NULL,               -- role-equivalent scope list
    created_by UUID NOT NULL REFERENCES users(id),
    expires_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    refresh_token_hash TEXT NOT NULL,
    user_agent TEXT,
    ip_address INET,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Knowledge Graph (native Postgres implementation, Phase 1-3;
-- see §15A for the seam that allows swapping in Neo4j/Memgraph later)
CREATE TABLE graph_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    node_type VARCHAR(30) NOT NULL,       -- asset/finding/credential/technology/evidence
    source_table VARCHAR(50) NOT NULL,    -- e.g. 'assets', 'findings' - which table this projects from
    source_id UUID NOT NULL,              -- FK value into that source table (polymorphic, no formal FK)
    label VARCHAR(500) NOT NULL,          -- human-readable display label
    properties JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_graph_node_source ON graph_nodes(project_id, source_table, source_id);
CREATE INDEX idx_graph_nodes_project_type ON graph_nodes(project_id, node_type);

CREATE TABLE graph_edges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    from_node_id UUID NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
    to_node_id UUID NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
    relationship_type VARCHAR(50) NOT NULL, -- hosts/runs/exposes/authenticates_as/vulnerable_to/derived_from/communicates_with
    weight NUMERIC(5,2) DEFAULT 1.0,        -- used for shortest-path / attack-cost queries
    properties JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_graph_edges_from ON graph_edges(from_node_id, relationship_type);
CREATE INDEX idx_graph_edges_to ON graph_edges(to_node_id, relationship_type);
CREATE INDEX idx_graph_edges_project ON graph_edges(project_id);

CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID REFERENCES organizations(id),
    actor_id UUID REFERENCES users(id),
    action VARCHAR(100) NOT NULL,        -- e.g. 'scan.launch', 'report.export'
    target_type VARCHAR(50),
    target_id UUID,
    before_state JSONB,
    after_state JSONB,
    ip_address INET,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_org_time ON audit_logs(organization_id, created_at DESC);
```

### 5.3 Normalization Notes
- 3NF throughout; JSONB used only for genuinely variable-shape data (plugin manifests, tool-normalized payloads, risk exposure metadata) — never as a substitute for real columns on frequently-queried fields.
- `dedup_key` on `findings` is a deterministic hash (host+port+CWE/template-id) computed by the Correlation Engine so re-running the same scan doesn't create duplicate findings — it upserts and appends a new `finding_sources` row instead.

---

## 6. API Design

### 6.1 Conventions
- Base path: `/api/v1`
- Auth: `Authorization: Bearer <JWT>` or `X-API-Key: <key>`
- Pagination: cursor-based, `?limit=50&cursor=<opaque>`, response includes `next_cursor`
- Filtering: `?filter[severity]=high,critical&filter[status]=open`
- Errors: RFC 7807 problem+json — `{ "type", "title", "status", "detail", "instance" }`

### 6.2 Representative Endpoint Set

| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/login` | Issue access + refresh token |
| POST | `/auth/refresh` | Rotate refresh token |
| POST | `/auth/logout` | Revoke session |
| GET/POST | `/organizations` | List / create orgs |
| GET/POST | `/organizations/{id}/projects` | List / create projects |
| POST | `/projects/{id}/authorization` | Attach signed scope doc (required before Active) |
| PATCH | `/projects/{id}/state` | Transition lifecycle state (validated transitions only) |
| GET/POST | `/projects/{id}/targets` | Manage targets |
| GET | `/projects/{id}/assets` | Query asset inventory (filterable) |
| GET | `/plugins` | List available plugins + manifests |
| POST | `/projects/{id}/workflows` | Define a DAG workflow |
| POST | `/projects/{id}/scans` | Launch a scan (workflow or single-plugin), scope-checked server-side |
| GET | `/scans/{id}` | Scan status + task tree |
| GET | `/scans/{id}/stream` (WS) | Live task status stream |
| GET | `/projects/{id}/findings` | List findings, filter by severity/status |
| PATCH | `/findings/{id}` | Update status, add human review notes |
| POST | `/findings/{id}/evidence` | Attach evidence |
| GET | `/ai/planner/suggestions?project_id=` | Get AI-suggested next actions (not executed) |
| POST | `/ai/planner/suggestions/{id}/approve` | Human approves → creates real workflow/scan |
| POST | `/projects/{id}/reports` | Generate report draft |
| POST | `/reports/{id}/finalize` | Lock report version |
| GET | `/projects/{id}/audit-logs` | Query audit trail |
| GET | `/notifications` | List user notifications |

### 6.3 Example Request/Response

```
POST /api/v1/projects/{project_id}/scans
Authorization: Bearer <token>

{
  "workflow_id": "b6e1...9f",
  "target_ids": ["a1c9...", "a1ca..."]
}

201 Created
{
  "id": "scan_01H...",
  "status": "queued",
  "tasks": [
    { "id": "task_01", "plugin": "subfinder", "target_id": "a1c9...", "status": "queued" }
  ],
  "created_at": "2026-07-16T10:02:00Z"
}
```

Scope violation example:
```
422 Unprocessable Entity
{
  "type": "https://specter.ai/errors/out-of-scope-target",
  "title": "Target outside authorized scope",
  "status": 422,
  "detail": "Target a1cb... is not present in the project's active authorization record."
}
```

---

## 7. Plugin Architecture

### 7.1 Interface Contract
Every plugin implements exactly four methods:

```python
class SecurityPlugin(Protocol):
    def run(self, target: TargetSpec, options: dict) -> RawExecutionResult:
        """Executes the underlying tool inside its isolated container. Returns raw stdout/stderr + exit code."""

    def parse(self, raw: RawExecutionResult) -> ParsedOutput:
        """Tool-specific parsing of raw output into a structured (but still tool-shaped) object."""

    def normalize(self, parsed: ParsedOutput) -> NormalizedFinding | NormalizedAsset:
        """Maps tool-specific structure into SPECTER_AI's canonical schema."""

    def validate(self, normalized) -> ValidationResult:
        """Schema + sanity validation before the object is allowed to reach persistence."""
```

### 7.2 Plugin Manifest (`plugin.yaml`)
```yaml
name: nuclei
version: "3.2.0"
category: scanning
image: specter-plugins/nuclei:3.2.0
inputs:
  - name: target
    type: url_or_host
    required: true
  - name: templates
    type: string_list
    required: false
resource_limits:
  cpu: "1"
  memory: "512Mi"
  timeout_seconds: 900
network_policy: target-only     # container can ONLY reach the declared target, nothing else
output_schema: normalized_finding_v1
```

### 7.3 Isolation Model
- One ephemeral container per task invocation; destroyed after result extraction.
- No shared volume with the API/DB containers.
- Network policy defaults to `target-only`: the plugin container's egress is restricted (via Docker network + iptables rules generated per-task) to exactly the IPs/domains listed in the task's target and nothing else — this is what makes the Scope Guard (§16.3) enforceable at the infrastructure level, not just the application level.
- Plugin containers run as non-root, read-only root filesystem, dropped Linux capabilities.

### 7.4 Plugins never touch the database
`run()`/`parse()`/`normalize()`/`validate()` return plain Python objects. The Plugin Manager (a core-domain service) is the only thing that persists `ToolResult`/`Asset`/`Finding` rows, after validation passes. This keeps every plugin trivially unit-testable and prevents a bad plugin from corrupting shared state.

### 7.5 Supported Plugin List (Phase 1–3)
Recon: Subfinder, Assetfinder, dnsx, httpx
Scanning: Nmap, RustScan, Nuclei, Nikto, WhatWeb, WPScan
Fuzzing: ffuf, Gobuster, Feroxbuster
Credential/AD: NetExec, enum4linux-ng, BloodHound
TLS: SSLScan, testssl.sh
Secrets: TruffleHog, Gitleaks

---

## 8. AI Architecture

### 8.1 Components
- **Planner** — given current project asset/finding state, proposes a ranked list of next recon/scan actions with justification. Output: `PlannedAction[]`, never auto-executed.
- **Analyzer / Correlation Engine** — deduplicates findings across tools (via `dedup_key`), links related findings into attack chains (e.g., "subdomain takeover" + "exposed API key" → escalated composite finding).
- **Risk Engine** — computes deterministic `base_score` from CVSS/exposure heuristics; AI only supplies the `ai_rationale` narrative, never the number.
- **Explainer** — turns a Finding into plain-English description, business impact, and remediation guidance.
- **Reporter** — assembles Explainer + Analyzer output into report-section drafts (Executive Summary, per-finding narrative).
- **Context Memory** — a per-project retrieval store (pgvector or a lightweight vector index) holding prior findings/report text so the AI has continuity across a multi-week engagement without re-sending the whole history each call.
- **Reasoning Pipeline** — orchestration layer (LangGraph-style or hand-rolled) chaining Planner→Analyzer→Risk→Explainer→Reporter with intermediate human checkpoints.

### 8.2 Prompt Library
Versioned prompt templates stored in the repo (not hardcoded inline), each with:
- Purpose
- Required context variables
- Expected output schema (JSON mode enforced where the provider supports it)
- A test fixture (sample input → expected-shape output) for regression testing prompts like code

### 8.3 Provider Abstraction
```python
class LLMProvider(Protocol):
    def complete(self, messages: list[Message], response_schema: type[BaseModel] | None) -> LLMResponse: ...
```
Concrete implementations: `OllamaProvider` (local, default for self-host/air-gapped labs), `OpenAICompatibleProvider` (for hosted Qwen/Llama/Gemma-serving endpoints or actual OpenAI-compatible APIs). Swappable per Organization via config — no code change needed to switch models.

### 8.4 Hard Boundary: AI → Execution
```
AI Planner  ──emits──▶  PlannedAction (status=pending_review)
                              │
                    human reviews in UI
                              │
                    clicks "Approve & Run"
                              │
                    API requires approved_by=<user_id>
                              │
                    Workflow Engine enqueues real Scan
```
No code path exists where an LLM response is deserialized directly into a Celery task dispatch. This is enforced by the API schema itself (the scan-launch endpoint has no field the AI's raw text output could populate without going through the PlannedAction→approval table).

---

## 9. Frontend Architecture

- **Framework**: React + TypeScript, Vite build.
- **State/data**: React Query for all server state (no redundant global store for server data); minimal local component state otherwise.
- **Styling**: TailwindCSS with a small design-token layer (see `frontend-design` conventions) — no default-looking generic admin template aesthetic.
- **Visualization**: Apache ECharts for trend/analytics charts, React Flow for the Attack Graph, xterm.js for live/recorded terminal session viewing.
- **Routing**: file-based route structure mirroring the page list in §10 of the dashboard section.
- **Key pages**: Projects, Targets, Assets, Scans (live status), Plugins, Attack Graph, Findings, Reports, Analytics, Audit, User Management, Settings.
- **Real-time**: a single WebSocket connection per active project view, multiplexing scan-status and notification events.
- **Accessibility**: WCAG AA color contrast minimum, full keyboard navigation on data tables.

---

## 10. Backend Architecture

### 10.1 Layering (Clean Architecture)
```
api/            → FastAPI routers, request/response schemas (Pydantic), no business logic
application/    → use-case services (e.g., LaunchScanService, GenerateReportService)
domain/         → entities, value objects, domain events, repository interfaces
infrastructure/ → SQLAlchemy repositories, Celery tasks, LLM provider adapters, object storage adapters
```
- `domain/` has zero imports from `infrastructure/` or `api/` — dependency direction always points inward.
- Use-cases in `application/` are the only place that orchestrate multiple repositories/services in one transaction.

### 10.2 Async & Task Boundaries
- FastAPI endpoints are `async def`; anything CPU-bound or long-running (plugin execution, report PDF rendering, LLM calls) is dispatched to Celery, never run inline in the request/response cycle.
- WebSocket updates are pushed from Celery task callbacks via a Redis pub/sub bridge back to connected API workers.

### 10.3 Migrations
- Alembic, one migration per schema change, autogenerate reviewed manually every time (never blindly applied) — especially for the CHECK constraints and unique indexes above.

---

## 11. Folder Structure

```
specter-ai/
├── backend/
│   ├── app/
│   │   ├── api/                    # FastAPI routers
│   │   │   ├── v1/
│   │   │   │   ├── auth.py
│   │   │   │   ├── projects.py
│   │   │   │   ├── scans.py
│   │   │   │   ├── findings.py
│   │   │   │   ├── ai.py
│   │   │   │   └── reports.py
│   │   ├── application/            # use-case services
│   │   ├── domain/                 # entities, repository interfaces, events
│   │   ├── infrastructure/
│   │   │   ├── db/                 # SQLAlchemy models, repos
│   │   │   ├── celery_app/
│   │   │   ├── llm/                # provider adapters
│   │   │   └── storage/            # object storage adapter
│   │   ├── plugins/                # plugin manager + plugin manifests
│   │   │   ├── manager.py
│   │   │   └── registry/
│   │   │       ├── nmap/
│   │   │       │   ├── plugin.yaml
│   │   │       │   └── plugin.py
│   │   │       └── nuclei/
│   │   ├── graph/                  # GraphProjector, GraphRepository interface + Postgres impl
│   │   │   ├── projector.py
│   │   │   ├── repository.py       # domain-layer interface
│   │   │   └── postgres_impl.py    # infrastructure-layer implementation
│   │   ├── core/                   # config, security, logging
│   │   └── main.py
│   ├── alembic/
│   ├── tests/
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   ├── components/
│   │   ├── features/                # feature-sliced: scans/, findings/, attack-graph/
│   │   ├── api/                      # React Query hooks
│   │   └── styles/
│   ├── package.json
├── plugin-images/                    # Dockerfiles for each tool container
├── infra/
│   ├── docker-compose.yml
│   ├── docker-compose.prod.yml
│   └── github-actions/
├── docs/
└── README.md
```

---

## 12. Deployment Architecture

### 12.1 Docker Compose Services (self-host default)
`api`, `worker` (Celery), `beat` (scheduler), `frontend`, `postgres`, `redis`, `minio` (object storage), `ollama` (optional local LLM), plus one image per enabled plugin (pulled/built on demand, not always-running).

### 12.2 Environment Variables (representative)
```
DATABASE_URL=postgresql+asyncpg://...
REDIS_URL=redis://redis:6379/0
JWT_SECRET=...
JWT_ACCESS_TTL_MIN=15
OBJECT_STORAGE_ENDPOINT=http://minio:9000
LLM_PROVIDER=ollama            # or openai_compatible
LLM_BASE_URL=http://ollama:11434
LLM_MODEL=qwen2.5:14b
SCOPE_GUARD_STRICT=true        # refuses execution against unauthorized targets, always true in prod
```

### 12.3 CI/CD (GitHub Actions)
- `lint` → ruff/mypy (backend), eslint/tsc (frontend)
- `test` → pytest w/ coverage gate, vitest for frontend
- `build` → build + push versioned images
- `plugin-validate` → validates every `plugin.yaml` manifest against a JSON schema before merge
- `deploy` (manual gate) → applies to staging, then production on tag

### 12.4 Monitoring & Backups
- Prometheus metrics: task queue depth, plugin execution duration/failure rate, API latency.
- Nightly encrypted Postgres + object storage backups, retention configurable per Organization.
- Structured JSON logs shipped to any OTLP-compatible collector.

---

## 13. Event Flow (representative: launching a scan)

1. User selects a workflow + targets in the UI, clicks "Run".
2. API validates: project is `Active`, all target IDs are within the current `authorization_records.allowed_targets`.
3. `LaunchScanService` creates a `Scan` row + `Task` rows (one per plugin/target pair in the DAG), status `queued`.
4. Each `Task` is enqueued as a Celery job; the Plugin Manager spins up an isolated container scoped to that one target.
5. Plugin `run()` executes; `parse()` → `normalize()` → `validate()` chain runs inside the worker.
6. Valid normalized objects are persisted as `ToolResult` + upserted `Asset`/`Finding` rows.
7. A domain event `FindingCreated` is published → Correlation Engine consumes it, may merge into an existing finding via `dedup_key`.
8. WebSocket push notifies the connected frontend of task/finding updates in near-real-time.
9. On DAG completion, an `AI Analysis Requested` event is queued (still human-approval-gated for anything beyond passive analysis of already-collected data).

---

## 14. Sequence Diagrams (textual)

### 14.1 Scan Launch → Result Persistence
```
User → API: POST /projects/{id}/scans
API → ScopeGuard: validate(target_ids, authorization_record)
ScopeGuard → API: OK
API → LaunchScanService: create(scan, tasks)
LaunchScanService → DB: INSERT scan, tasks (status=queued)
LaunchScanService → Celery: enqueue(task) x N
API → User: 201 { scan_id, tasks }

Celery Worker → PluginManager: execute(task)
PluginManager → Docker: spawn(plugin_image, network_policy=target-only)
Docker → PluginManager: raw_output
PluginManager → Plugin.parse/normalize/validate
PluginManager → DB: INSERT tool_result, UPSERT asset/finding
PluginManager → Redis(pubsub): publish(task.updated)
API(WS) → Frontend: push task/finding update
```

### 14.2 AI Suggestion → Human Approval → Execution
```
Scheduler → AI Planner: analyze(project_state)
AI Planner → DB: INSERT planned_action (status=pending_review)
Frontend → API: GET /ai/planner/suggestions
User → Frontend: clicks "Approve & Run"
Frontend → API: POST /ai/planner/suggestions/{id}/approve
API → DB: UPDATE planned_action SET approved_by=user_id
API → LaunchScanService: create(scan from planned_action)
  (continues as 14.1)
```

---

## 15. Data Flow Diagrams (textual, Level 1)

```
[Analyst] → (defines) → [Project + Authorization Record]
                                   │
                                   ▼
[Targets] → (Recon Plugins) → [Assets] → (Scan Plugins) → [ToolResults]
                                                                 │
                                                                 ▼
                                                     [Normalization Layer]
                                                                 │
                                                                 ▼
                                                   [Findings + Correlation Engine]
                                                                 │
                                       ┌─────────────────────────┼─────────────────────────┐
                                       ▼                         ▼                         ▼
                               [Risk Engine]           [Attack Graph Builder]     [Evidence Store]
                                       │                         │                         │
                                       └─────────────┬───────────┴─────────────────────────┘
                                                     ▼
                                          [AI Reporter (drafts)]
                                                     │
                                            (human review/edit)
                                                     ▼
                                          [Final Report (PDF/DOCX)]
```

---

## 15A. Knowledge Graph Subsystem

### 15A.1 Why This Exists
Every subsystem above it in the pipeline — Correlation, Risk, Attack Graph, Reporting — is ultimately answering relationship questions: *what does this finding touch, what does this credential unlock, what's downstream of this host.* Without a graph, those questions get answered with ad-hoc joins scattered across services, and every new "impact analysis"-style feature means writing another bespoke traversal query. The Knowledge Graph centralizes that into one queryable structure that every consumer shares.

### 15A.2 Node & Edge Model
**Node types**: `asset`, `finding`, `credential`, `technology`, `evidence`.
**Edge types** (directed, typed):

| Edge | Meaning | Example |
|---|---|---|
| `hosts` | asset → asset | server hosts a subdomain/service |
| `runs` | asset → technology | host runs Apache 2.4.41 |
| `exposes` | asset → finding | host exposes CVE-2023-XXXX |
| `vulnerable_to` | technology → finding | Apache 2.4.41 vulnerable_to <finding> |
| `authenticates_as` | credential → asset | discovered creds authenticate to a host |
| `derived_from` | credential/asset → finding | a credential was extracted from a secret-leak finding |
| `communicates_with` | asset → asset | observed network relationship between hosts |
| `evidenced_by` | finding → evidence | links a finding to its proof artifact |

### 15A.3 Population Pipeline
The graph is never written to directly by any service other than a single `GraphProjector`. It subscribes to the same domain events already flowing through the platform (`AssetUpserted`, `FindingCreated`, `EvidenceAttached`, `CredentialDiscovered`) and translates each into idempotent node/edge upserts. This keeps mutation logic in exactly one place and guarantees the graph is always reconstructable by replaying persisted relational data — there is a `rebuild_graph_from_scratch` maintenance task for exactly this purpose (useful after a schema change to the edge model, or to recover from any projection bug without ever touching source data).

### 15A.4 Query Patterns Supported
- **Path queries**: shortest/all paths between two nodes (recursive CTE in the native Postgres implementation; native Cypher traversal if/when migrated to Neo4j).
- **Reachability / blast radius**: all nodes within N hops of a given "foothold" node — powers Impact Analysis (FR-14.4) and the Attack Graph's "highlight shortest path to crown jewel" feature (FR-8.3).
- **Credential impact**: given a `credential` node, every `asset` it authenticates to and every `finding` reachable from those assets.
- **Technology exposure**: given a `technology` node (e.g., a vulnerable library version), every asset running it across the whole project, and every finding attached to those assets — this is the query that makes "we found this vulnerable component in one place, where else does it appear" fast instead of manual.

### 15A.5 Storage Strategy & Future Seam
Phase 1–3: native Postgres tables (`graph_nodes`/`graph_edges`, §5.2), recursive CTEs for traversal. This avoids operating a second datastore while the project is proving itself, and is genuinely sufficient at per-project graph sizes typical of a single engagement (thousands, not billions, of nodes).

The `GraphProjector` and all graph read access go through a `GraphRepository` interface (domain-layer, per the Clean Architecture rules in §10.1). If/when engagement scale or the desire for native graph algorithms (PageRank-style "most central asset", built-in shortest-weighted-path) justifies it, only the `infrastructure/graph/` adapter needs to change to a Neo4j/Memgraph-backed implementation — no consumer of `GraphRepository` (Attack Graph API, AI Planner, Report Engine) needs to know or change. This is called out explicitly as a Phase 5/6 candidate, not a Phase 1 requirement, so the flagship-project narrative can honestly show "started simple, scaled the data layer as the design proved itself" — a stronger portfolio story than over-engineering a graph database into a Phase 1 MVP.

### 15A.6 AI Interaction
The AI Engine (Planner, Analyzer) queries the graph read-only to generate better-informed suggestions ("this credential reaches 6 other in-scope hosts, prioritize confirming it works before moving on"). Consistent with §8.4, the AI never writes to the graph directly — all graph mutations flow through the `GraphProjector` reacting to already-validated domain events, so an AI hallucination can, at absolute worst, produce a bad *suggestion* a human rejects — never a corrupted graph.

---

## 16. Security Model

### 16.1 Authentication & Session Security
- Argon2id password hashing, configurable work factor.
- Refresh tokens stored hashed, rotated on every use, revocable per-session (supports "log out all devices").
- Optional TOTP-based MFA per user.

### 16.2 RBAC Enforcement
- Enforced server-side on every endpoint via a dependency-injected permission checker — never trust frontend role display alone.
- Role checks are scoped to `(organization_id, project_id)` pairs, not global flags.

### 16.3 Scope Guard (the core safety control)
This is the mechanism that keeps the platform aligned with "authorized environments only":
- A Project cannot leave `draft` state without an attached `authorization_record` containing an explicit target allow-list and a validity date range.
- Every scan-launch request is checked against the active authorization record; targets outside it are rejected at the API layer (`422 out-of-scope-target`).
- Enforcement is duplicated at the infrastructure layer: each plugin container's network policy is generated per-task from the same allow-list, so even a bug in the API-layer check doesn't let a container reach an arbitrary IP.
- Authorization records outside their date range automatically flip the project's effective scan permission off (checked at launch time, not just at project-state-change time).

### 16.4 Secrets Management
- No secrets in source control or logs; `.env` for local dev, a proper secrets manager (Vault / cloud KMS) recommended for production and documented as such.
- Evidence and tool-result object storage encrypted at rest (SSE-KMS or MinIO server-side encryption).

### 16.5 Audit Logging
- Immutable append-only `audit_logs` table (no UPDATE/DELETE grants for the application DB role — only INSERT).
- Every RBAC change, auth event, scan launch, report export, and evidence access is logged with actor, before/after state, and source IP.

### 16.6 AI-Specific Safeguards
- AI output is never auto-executed (§8.4).
- AI-drafted content is visually distinguished in the UI until explicitly approved by a human, at every stage (findings text, risk rationale, report sections).
- Prompt templates never include live credentials/secrets in context sent to a remote LLM provider; when using a non-local (`openai_compatible`) provider, sensitive evidence (credentials, raw session recordings) is excluded from the context sent, and only sanitized/structured summaries are shared — local `ollama` provider is recommended default for engagements requiring absolute data locality.

---

## 17. User Stories (representative sample)

- As a **Lead Tester**, I want to attach a signed authorization document to a project before I can launch any scan, so that no one on my team can accidentally test out-of-scope systems.
- As a **Tester**, I want recon results automatically deduplicated against previous runs, so I only look at what's new.
- As a **Tester**, I want the AI to suggest my next three most valuable actions with a one-line justification, so I don't have to manually correlate five tools' output myself.
- As a **Client Viewer**, I want to see only a redacted, executive-level view of findings, without raw technical evidence.
- As an **Admin**, I want a full audit trail of who launched which scans and exported which reports, for compliance purposes.
- As a **Lead Tester**, I want to see an attack graph showing the shortest path from an initial foothold to a defined crown-jewel asset, to help me plan the next phase of an internal assessment.

---

## 18. Roadmap

| Milestone | Theme |
|---|---|
| M1 | Core platform: auth, RBAC, projects, targets, 6 core plugins, manual reporting |
| M2 | Plugin ecosystem expansion (full 20-tool list), Workflow DAG builder, Scheduler |
| M3 | AI Decision Engine v1 (Planner/Analyzer/Explainer, human-approval gated) |
| M4 | Knowledge Graph v1 (Postgres-native), Attack Graph, Executive Dashboard, Historical Trends |
| M5 | Team collaboration, Notifications, Slack/Teams integration |
| M6 | Enterprise: SSO/OIDC, HA deployment topology, community plugin submission pipeline, Knowledge Graph migration to dedicated graph store if scale justifies it |

---

## 19. Development Phases

**Phase 1 — Foundation**
Auth, RBAC, Organizations/Projects/Targets, Authorization-record gating, PostgreSQL schema, base API, minimal frontend shell, Docker Compose dev environment. Deliverable: a tester can create an authorized project and manually record a target list. No scanning yet.

**Phase 2 — Plugin & Scan Core**
Plugin Manager + isolation model, first 6 plugins (Nmap, Subfinder, httpx, Nuclei, ffuf, Gitleaks), Scan Orchestrator, Celery integration, Asset Inventory population, Findings table + basic dashboard.

**Phase 3 — Workflow & Correlation**
Workflow DAG builder, Scheduler, remaining plugin set, Correlation Engine (`dedup_key` merge logic), Evidence Collection, basic PDF report generation, **Knowledge Graph v1**: `GraphProjector` + Postgres-native `graph_nodes`/`graph_edges` tables, populated automatically as Findings/Assets/Evidence are persisted (§15A).

**Phase 4 — AI Decision Engine**
Planner/Analyzer/Explainer/Reporter, Prompt Library, Context Memory, human-approval workflow UI, Risk Engine scoring.

**Phase 5 — Visualization & Collaboration**
Attack Graph (React Flow + BloodHound integration, powered by Knowledge Graph path/reachability queries), Impact Analysis feature, Executive/Technical Dashboards, Historical Trends, comments/notifications, WebSocket live updates.

**Phase 6 — Enterprise Hardening**
SSO/OIDC, HA topology, monitoring/observability stack, community plugin validation pipeline, compliance/data-retention tooling.

> Per your instruction, implementation does not begin until you say **"Start Phase 1."**

---

## 20. Testing Strategy

| Layer | Approach |
|---|---|
| Domain/Application | Unit tests, >80% coverage target, no I/O — pure logic (risk scoring, dedup key computation, state transition validation) |
| Plugin `parse/normalize` | Golden-file tests: real captured tool output → expected normalized object, per plugin |
| API | Contract tests (schemathesis or similar) against the OpenAPI spec + integration tests with a test Postgres |
| Scope Guard | Dedicated adversarial test suite: attempts to launch scans against out-of-scope targets, expired authorization windows, malformed target lists — must always fail closed |
| AI pipeline | Regression fixtures per prompt template (fixed input → assert output schema validity, not exact text); human-in-the-loop UI tested for the "AI-drafted" visual state |
| Knowledge Graph | Projection tests: given a fixed set of domain events, assert exact resulting node/edge set; rebuild-from-scratch test asserts `rebuild_graph_from_scratch` produces an identical graph to incremental projection; traversal query tests against fixture graphs of known shape (path exists / doesn't exist / multiple paths) |
| Frontend | Component tests (Vitest + Testing Library), E2E critical paths (Playwright): login → create project → attach authorization → launch scan → view finding → generate report |
| Security | Dependency scanning (pip-audit/npm audit) in CI, container image scanning (Trivy), periodic internal review of the plugin isolation/network-policy implementation itself |
| Load | Locust-based load tests on the API gateway and Celery throughput before each major release |

---

## 21. Coding Standards

- **Python**: `ruff` + `black` formatting, `mypy --strict` on `domain/` and `application/`, Pydantic v2 models for all API schemas, docstrings on every public service method explaining *why*, not just *what*.
- **TypeScript**: `strict: true`, no `any` without an inline justification comment, ESLint + Prettier enforced in CI.
- **Git**: Conventional Commits, trunk-based development with short-lived feature branches, mandatory PR review, no direct pushes to `main`.
- **SOLID**: enforced structurally — e.g., new plugins require implementing the `SecurityPlugin` protocol (Interface Segregation/Liskov by construction), use-case services depend on repository interfaces defined in `domain/`, not concrete SQLAlchemy classes (Dependency Inversion).
- **Domain-Driven Design**: used where real domain complexity exists (Scan/Finding/Correlation bounded context) — deliberately *not* over-applied to simple CRUD areas like user profile settings, to avoid ceremony without payoff.
- **Documentation**: every module has a top-of-file docstring stating its bounded context and its explicit non-goals.

---

## 22. Future Expansion

- Community/marketplace plugin submission with automated manifest + sandbox validation.
- Continuous monitoring mode (scheduled re-recon + diff-based alerting) for retainer-style engagements.
- Purple-team mode: read-only blue-team view correlating SPECTER_AI findings against SIEM alerts to measure detection coverage.
- Multi-LLM ensemble scoring for the Risk Engine's rationale layer (compare explanations across providers for consistency, flag divergence for human review).
- Plugin SDK in additional languages (currently Python-first) for community contributions.
- Formal SOC 2 readiness track once the platform handles real client engagement data at scale.
- Native graph-algorithm features once/if migrated to a dedicated graph store: centrality scoring ("most structurally important asset in this engagement"), community detection for automatic segment/zone discovery, weighted shortest-path attack-cost modeling instead of unweighted hop-count.

---

*End of SRS v1.1. Awaiting explicit instruction to "Start Phase 1."*
