# MILESTONE 3 — M7.5 Phase 4 Architecture: Durable Event Delivery

**Type:** Architecture reconnaissance (read-only). **No source code, migration, API, or test changes were made.**
**Baseline:** `938 passed, 0 failed, 2 warnings` (re-confirmed during this investigation with the full live stack up).
**Head commit:** `b3d7644` (`feat(m75-phase3): scheduled autonomous campaigns`). Working tree clean.

---

## 1. Current event/audit architecture

SPECTER_AI has **one** durable, append-only, event-like record today: the `audit_logs` table.

### 1.1 Domain entity — `AuditLogEntry` (`backend/app/domain/entities.py:189-203`)

```python
@dataclass(slots=True)
class AuditLogEntry:                 # SRS §16.5 — immutable, append-only
    id: UUID
    organization_id: UUID | None
    actor_id: UUID | None
    action: str                       # the only "type" discriminator
    target_type: str | None           # user | schedule | scan | workflow_execution | planned_action | autonomous_run | autonomous_action
    target_id: UUID | None
    ip_address: str | None
    created_at: datetime
    before_state: dict[str, object]   # ~always empty at call sites
    after_state: dict[str, object]    # the JSONB payload blob
```

### 1.2 DB model — `AuditLogModel` (`backend/app/infrastructure/db/models/audit_log.py:17-44`)

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `organization_id` | UUID \| NULL | FK → organizations; **`None` on nearly every row** (only `planner_service._audit_decision` sets it) |
| `actor_id` | UUID \| NULL | FK → users |
| `action` | String(100) NOT NULL | free-text, no CHECK, no enum |
| `target_type` | String(50) \| NULL | |
| `target_id` | UUID \| NULL | |
| `before_state` / `after_state` | JSONB \| NULL | |
| `ip_address` | INET \| NULL | |
| `created_at` | timestamptz | single supporting index |

Index: `idx_audit_org_time (organization_id, created_at)` only (`audit_log.py:44`; migration `e805ba666950:62-90`).

### 1.3 Repository — write-only

- Protocol: `add()`, `list_for_organization()` (`backend/app/domain/repositories.py:139-141`).
- SQLAlchemy impl: `add()` = `session.add()` + `session.flush()`; `list_for_organization()` (`backend/app/infrastructure/db/repositories/audit_log_repository.py:35-57`).
- **`list_for_organization` is dead code** — no API endpoint, no schema, no router exposes audit entries. The audit trail is write-only with no read-back path anywhere in `backend/app`.

### 1.4 Distinct audit `action` values in use (38 total, 7 `target_type`s)

Auth (`auth.register`, `auth.login`, `auth.refresh`, `auth.logout`, `auth.logout_all`) — `api/v1/routers/auth.py:86-159`.
Scheduler/campaigns (`scheduler.schedule_fired`, `scheduler.schedule_disabled`, `scheduler.schedule_fire_failed`, `scheduler.campaign_fire_failed`, `scheduler.campaign_rejected`, `scheduler.campaign_skipped_active_run`, `scheduler.campaign_created`) — `tasks.py:444-552`, `campaign_scheduler_service.py:142-202`.
Scan engine (`scan.started`, `scan.failed`, `scan.completed`) — `execution/engine.py:123-343`.
Workflow executor (`workflow.execution.started/completed/cancelled/failed`, `workflow.step.skipped/failed/completed`) — `workflow_executor.py:187-403`.
Planner (`ai.planner.proposal`, `ai.action.execute_rejected`, `ai.action.execute_started`) — `planner_service.py:493-597`.
Orchestrator (`ai.autonomous.concurrent_cycle_blocked`, `.planner_error`, `.blocked`, `.duplicate`, `.execute_failed`, `.scope_rejected`, `.executed`, `.awaiting_human`, `.observation_error`, `.observation`) — `autonomous_orchestrator.py:152-530`.
Recovery (`ai.autonomous.stalled`, `.recovered`, `.execution_retry`) — `autonomous_recovery.py:170-220`.

### 1.5 Transaction behavior of audit writes

All audit writes share the **same session/transaction** as the state change they describe:
- API path: request-scoped `get_db_session` (`session.py:80-103`) commits once on success, rolls back on failure; `get_audit_log_repository` reuses that session (`api/v1/deps.py:185-188`).
- Celery path: each task body builds one `AsyncSession`; audit rows share it (commits at `tasks.py:155, 318, 456, 480, 498, 535, 559, 733, 764, 912`).
- **Best-effort wrappers** (`_audit_event`/`_audit_decision`/`_write_audit`: `orchestrator.py:679-708`, `recovery.py:249-252`, `workflow_executor.py:451-465`) swallow `add()` exceptions but still use the same session — a flushed-then-rolled-back state can poison the outer transaction; best-effort isn't cleanly achieved.
- Deliberately **non-atomic tombstones**: after an unexpected schedule-fire rollback, `scheduler.schedule_fire_failed` / `scheduler.campaign_fire_failed` are committed as fresh, separate transactions (`tasks.py:481-500`, `541-561`).

### 1.6 Verdict: can audit serve as event delivery infrastructure? **No.**

- No delivery/outbox/idempotency/destination columns; append-only semantics (INSERT-only by DB role, `audit_log.py:17-23`).
- No consumer reads these rows; `list_for_organization` is dead. No idempotency key, no unique constraint beyond PK, no retry state.
- It is a business read-back *trail*, not a delivery queue. **Do not bolt delivery onto `audit_logs`.** The outbox must be a separate table with a different write/lifecycle contract.

---

## 2. Current campaign lifecycle

```
Celery beat (30s)
  └─ specter.tick_schedules                     tasks.py:166-174 → 326-507
       └─ schedule_repo.claim_due (FOR UPDATE SKIP LOCKED)       tasks.py:418
            ├─ CAMPAIGN → _fire_campaign_schedule                tasks.py:422-430, 510-566
            │    └─ campaign_scheduler.fire(schedule)            campaign_scheduler_service.py:118-222
            │         ├─ scope preflight (project ACTIVE + active authorization)   :134-158
            │         ├─ AutonomousService.create(run)            (AutonomousRunStatus.CREATED)
            │         ├─ audit scheduler.campaign_created         :197-216
            │         ├─ mark_run (consume occurrence)            :217
            │         └─ ONE commit makes {run + schedule advance + audit} atomic   tasks.py:535
            │         → campaign_advance_task.apply_async(task_id=run_id)          tasks.py:537-540
            └─ WORKFLOW → WorkflowExecution queued + mark_run + audit + commit      tasks.py:432-456

  specter.campaign_advance (per run, idempotent task_id=run_id)  tasks.py:569-736
    └─ run must still be CREATED (else no-op)                    tasks.py:671-676
    └─ orchestrator.cycle(run_id) (M7.4 protected machinery)     tasks.py:732
         ├─ CREATED → PLANNING        autonomous_orchestrator.py:175-200
         ├─ planner proposal          planner_service.py
         ├─ approval gate             M7.4 (awaiting_human / auto-approve per policy)
         ├─ execution via ScanService + AfterCommitScanTaskDispatcher
         └─ OBSERVING → observation   observation_complete gates continue vs complete
    └─ commit + drain_pending_dispatches()                       tasks.py:733-734

  specter.recover_autonomous_runs (60s)                          tasks.py:769-918
    └─ stalled recovery + per-run commit + drain                 tasks.py:911-913
```

Run states (`domain/value_objects.py` `AutonomousRunStatus`; `domain/entities.py:661-698`): CREATED → PLANNING → AWAITING_APPROVAL → EXECUTING → OBSERVING → COMPLETED (terminal), plus FAILED / CANCELLED (terminal). Recovery re-arms transport-failed actions and fails closed (`autonomous_recovery.py:120-224`).

---

## 3. Existing transaction boundaries (outbox insert points)

Every commit point below has a **live session at the mutation site**, so a durable outbox row can be created *in the same transaction* with zero architectural redesign:

| # | Boundary | Commit | Session available | Mutations in the commit |
|---|---|---|---|---|
| 1 | Scan request (API) | `session.py:96` | request session | scan row `queued` + audit |
| 2 | Celery scan execution | `tasks.py:155` | task session | scan results/status + audit |
| 3 | Workflow execution (API) | `tasks.py:318` | task session | workflow execution + audit |
| 4 | Beat workflow-schedule fire | `tasks.py:456` | task session | workflow execution + mark_run + audit |
| 5 | Beat dead-schedule disable | `tasks.py:480` | task session | schedule disabled + audit |
| 6 | **Campaign fire** | `tasks.py:535` | task session | **run CREATED + mark_run + audit** |
| 7 | **Campaign advance (cycle)** | `tasks.py:733` | task session | **run state transition + actions + observations + audit** |
| 8 | AI analysis | `tasks.py:764` | task session | analysis + audit |
| 9 | **Autonomous recovery** | `tasks.py:912` | task session | **run state + retries + audit** |

The two best candidates for a first outbox integration are **#6 (campaign fire)** and **#7 (campaign advance)** — exactly the scheduled-autonomous-campaign lifecycle from Phase 3.

---

## 4. Existing delivery mechanisms ("outbox-ish")

### 4.1 The in-memory scan-dispatch buffer (`dispatch_after_commit.py` + `dispatcher.py`)

- `AfterCommitScanTaskDispatcher` (`dispatcher.py:27-43`) does **not** call Celery eagerly; it buffers scan ids into a `ContextVar` pending set (`dispatch_after_commit.py:42-44`, `queue_dispatch() :61-67`).
- `drain_pending_dispatches()` (`dispatch_after_commit.py:70-97`) is invoked **after** the transaction commits, only at three places: `session.py:100`, `tasks.py:734`, `tasks.py:913`.
- Purpose: fix a lost-scan race — the worker could observe a scan row before the request committed and the scan would stay `queued` forever (`dispatch_after_commit.py:1-31`).
- Deduplicates by scan id within a task/request; sender is a process-global delegate bound once per dispatcher construction.

### 4.2 Verdict

- **Durability: none** — the pending set is pure process memory. If the process dies between commit and drain, the scan stays `queued` with no retry (documented as the "pre-existing retry-less state"). A dropped scan is logged (`scan_queue_dispatch_dropped sender_unbound`).
- **Not reusable as the event outbox.** It is a *commit-ordering shim* for a single scan-dispatch channel, not a durable queue.
- **Must remain separate.** It guarantees scans are never handed to Celery before their row exists; the outbox conversely must be durable before any relay touches it. Do not merge them. (Optionally, a future outbox-derived "scan created" event would subsume its observability value, but the ordering guard stays for the execution path.)
- **Reusable pattern:** the three after-commit drain points are the template for a future durable relay trigger.

---

## 5. Existing consumers (only the ones that exist today)

| Consumer | Exists? | Evidence |
|---|---|---|
| Webhooks | **No** | zero code in `backend/app` or `frontend/src`; only SRS FR-12.2 / milestone docs |
| Notifications (email/Slack/Teams/etc.) | **No** | zero tables/services; no `notifications` anywhere |
| Event bus / outbox / pub-sub | **No** | greps for `DomainEvent|outbox|EventBus|publish|emit` hit only docstrings; `audit_logs` is the only durable event-ish record |
| Report generation | Partial (**API-triggered only**) | `POST /projects/{id}/reports`, `/versions`, `/finalize` (`routers/reports.py:32-107`); no celery task, no auto path |
| Internal automation | Yes | 7 celery tasks: `specter.ping`, `execute_scan`, `execute_workflow`, `tick_schedules`, `campaign_advance`, `run_ai_analysis`, `recover_autonomous_runs` (`tasks.py:25-769`) |
| Outbound HTTP | Yes (2) | `ExecutorHttpRunner` → executor service (`executor_runner.py`); LLM calls to Ollama/OpenAI-compatible (config `LLMProvider`) |
| API consumers | Yes | 18 routers + mounted `/metrics` (`api/v1/router.py:12-31`, `main.py:71`) |
| Frontend polling | Minimal | single route `/` → HealthPage; only `refetchInterval: 15_000` on health (`features/health/api.ts:22`); no WebSocket/SSE |
| CI/CD / integrations | **No** | none in repo |

**Conclusion:** the backend's only event *delivery* today is its own Celery queue. There is no external consumer to deliver to yet — Phase 4 must therefore build the durable outbox *substrate* so that consumers can attach (webhook endpoints in a later phase, report automation in Phase 5), and must **not pretend consumers exist**.

---

## 6. Event generation points (canonical lifecycle events)

Derived from the M7.5.3 + M7.4 lifecycle, mapped to the exact transaction where they'd be emitted atomically:

| Event | Transaction boundary | Description |
|---|---|---|
| `campaign.schedule.fired` | `tasks.py:535` | schedule occurrence claimed and processed (any outcome) |
| `campaign.run.created` | `tasks.py:535` | AutonomousRun created for a campaign |
| `campaign.run.skipped_active_run` | `tasks.py:535` | occurrence consumed; one-active-run invariant prevented a new run |
| `campaign.run.rejected` | `tasks.py:535` | preflight rejected (project inactive / no active authorization) |
| `campaign.run.planning_complete` | `tasks.py:733` | planner produced a proposal (post-cycle-1) |
| `campaign.action.executed` | `tasks.py:733` | an approved action executed (per action) |
| `campaign.action.scope_rejected` | `tasks.py:733` | execution-time Scope Guard rejected a target |
| `campaign.observation` | `tasks.py:733` | new observation recorded |
| `campaign.run.completed` | `tasks.py:733` | terminal COMPLETED |
| `campaign.run.failed` | `tasks.py:733` | terminal FAILED |
| `campaign.run.cancelled` | `tasks.py:733` | terminal CANCELLED |
| `campaign.run.stalled` / `.recovered` / `.retried` | `tasks.py:912` | recovery supervisor activity |

(Mirror audit actions from §1.4 one-for-one; each generation point is the same session where the corresponding `AuditLogEntry` is already written.)

---

## 7. Proposed canonical event envelope

A versioned envelope with only the fields required by the identification criteria in the mission:

```jsonc
{
  "id": "uuid-v7",                     // delivery idempotency key; consumer dedupes on this
  "specversion": "1.0",                // envelope schema version
  "type": "campaign.run.completed",    // dot-namespace lifecycle event type
  "time": "ISO-8601-UTC",              // state-transition commit time
  "organization_id": "uuid",
  "project_id": "uuid",
  "schedule_id": "uuid | null",        // campaign/schedule identifier
  "run_id": "uuid | null",             // autonomous run identifier
  "scan_id": "uuid | null",            // for scan-derived events
  "target_type": "autonomous_run",     // mirrors audit target_type; enables generic routing
  "target_id": "uuid | null",          // mirrors audit target_id
  "datacontenttype": "application/json",
  "schema_version": 1,                 // payload schema version (independent of specversion)
  "data": { }                          // the actual event payload (see §7.1)
}
```

Why each field exists (no field without a reason):
- `id` — consumer idempotency + delivery dedupe (the at-least-once cornerstone).
- `specversion` — lets the relay/consumers evolve the envelope without breaking old rows.
- `type` — routing key to webhook subscription filters and adapter dispatch.
- `time` — ordering, latency observability, and replay-window bound.
- `organization_id`/`project_id` — tenant isolation (webhook endpoints are org-scoped; dispatchers filter by org) and subscription scoping.
- `schedule_id`/`run_id`/`scan_id`/`target_type`/`target_id` — the mission's campaign/run identification + a path for generic routing.
- `schema_version` / `datacontenttype` / `data` — explicit payload contract.

### 7.1 Payload content policy

`data` is the *safest* minimal projection of the state change: status, objective, counts, links/ids — **and a redacted, severity-only summary, never raw findings** by default. Sensitive security-finding content must be excluded or redacted using the existing report redaction service (see §13). Evolve `schema_version` when payload fields change; never mutate `data` in place.

---

## 8. Proposed outbox architecture

**Decision: PostgreSQL table + existing Celery relay. No new broker** (postgres can hold this volume; Phase 3 already proved the SKIP-LOCKED claim pattern).

### 8.1 `event_outbox` table (new)

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | the event id (== envelope `id`) |
| `type` | String(100) NOT NULL | envelope `type` |
| `organization_id` | UUID FK \| NULL | tenant; **always set for campaign/run events** (derive from project → org) |
| `project_id` | UUID FK \| NULL | |
| `schedule_id` | UUID FK \| NULL | |
| `run_id` | UUID FK \| NULL | |
| `scan_id` | UUID FK \| NULL | |
| `payload` | JSONB NOT NULL | the redacted `data` |
| `specversion` | String(5) NOT NULL default "1.0" | |
| `payload_schema_version` | Integer NOT NULL default 1 | |
| `available_after` | timestamptz NOT NULL default now() | visibility / backoff scheduler |
| `status` | String(20) NOT NULL default "pending" | pending \| delivering \| delivered \| dead_letter |
| `attempts` | Integer NOT NULL default 0 | |
| `max_attempts` | Integer NOT NULL default (settings) | snapshotted at enqueue |
| `last_error` | String(500) \| NULL | |
| `next_retry_at` | timestamptz \| NULL | set on transient failure |
| `delivered_at` | timestamptz \| NULL | |
| `created_at` | timestamptz NOT NULL | |

Indexes: partial index on `(status, available_after, next_retry_at)` (claim scan), index on `(organization_id)`.

### 8.2 Creation is transactional (never in-memory-only)

An `EventEmitter` (domain repository protocol) constructed with the same session as the state change adds the row; the existing commit at each §3 boundary persists it with the state. If the transaction rolls back, the event rolls back too (matching state, no phantom events). The event **never exists only in process memory.**

### 8.3 `webhook_endpoints` table (new, later phase)

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `organization_id` | UUID FK NOT NULL | tenant isolation |
| `name` | String(100) | |
| `url` | Text NOT NULL | validated §13 |
| `secret_encrypted` | Text NOT NULL | symmetric-encrypted HMAC secret (§13) |
| `secret_key_ref` | String(100) | which KMS/app key decrypts it |
| `event_types` | JSONB \| NULL | subscription filter; `null` = all |
| `is_active` | Bool NOT NULL default true | circuit-breaker flag |
| `disabled_reason` | String(200) \| NULL | |
| `created_by` / `created_at` / `updated_at` | | |

---

## 9. Proposed dispatcher architecture

```
DB commit (state + outbox row, same txn)          [§3 boundaries]
        │
        ▼
Outbox relay task (Celery beat, e.g. every 15s)
  └─ claim outbox rows: (status='pending' OR retry due)
       WHERE available_after <= now AND next_retry_at <= now
       FOR UPDATE SKIP LOCKED LIMIT n                  → status='delivering'
  └─ for each: build envelope → routing (subscription match) → adapter.deliver()
       ├─ WebhookAdapter (later phase) — HTTP POST, HMAC-SHA256, timeout, §13
       └─ (future) NotificationAdapter, ReportAutomationAdapter, internal automation
  └─ outcome → delivered (ack) | transient fail (attempts++, next_retry_at=backoff)
      | permanent fail (status='dead_letter') | crash (claim releases on rollback → retry)
```

- New **celery task** `specter.outbox_relay` (beat) + optional immediate kick from the after-commit drains is *not* used to avoid new failure surface; the relay is purely periodic → simple, crash-safe, at-least-once.
- Adapter interface lives in **domain** as a Protocol; HTTP delivery lives in **infrastructure** (keeps Clean Architecture: application uses interfaces, never `httpx`/celery directly — mirrors `ScanTaskDispatcher` precedent, `dispatcher.py:1-7`).
- **No dual-write problem**: relay reads only committed rows.

---

## 10. Delivery guarantee

- **At-least-once** — the only honest guarantee, matching Phase 3 scheduler semantics. A claimed row that fails mid-delivery releases its lock on rollback and is retried until `max_attempts`.
- **Effectively-once is consumer-side, never claimed on the producer side.** Consumers must dedupe on envelope `id` (idempotent handlers). The envelope's `id` + `specversion` make this possible.
- **Exactly-once delivery is NOT achievable** with HTTP webhooks and is expressly *not* claimed.
- Relates to Phase 3: outbox at-least-once composes with the scheduler's at-least-once fire. A campaign `run.completed` event is emitted once per committed terminal transition; a crashed-or-delayed relay can redeliver it — hence consumer idempotency is mandatory.

## 11. Retry strategy (conceptual)

| Class | Detection | Policy |
|---|---|---|
| Transient HTTP (5xx, timeouts, connection refused/reset, DNS) | status ≥500 or exception | exponential backoff: `base(1s) * 2^(attempts-1)`, jitter, cap `max_attempts` (default ~8) |
| Rate-limit (429 w/ Retry-After) | status 429 | honor `Retry-After` or slot into `next_retry_at` |
| Permanent 4xx (400/401/403/404/410/422) | status <500 | **no retry** → dead_letter immediately (a 404 endpoint will never succeed by retrying) |
| Consumer ack | HTTP 2xx | mark delivered |
| Worker crash mid-delivery | no ack / lock lost | claim rolled back → retry as above |
| Duplicate delivery | same `id` delivered twice | consumer dedupe in Phase 4-C-test harness (idempotency test double) |
| Process restart | relay re-reads committed rows | nothing lost; only in-flight claims re-run |

A dispatch/handler idempotency test suite must demonstrate: double-delivery of identical `id` is a no-op for the consumer.

## 12. Dead-letter strategy

- `status='dead_letter'` + `last_error` + `attempts == max_attempts`, retained in `event_outbox` (auditable trail, replayable).
- A replay API (Phase 4-D, admin-only, org-scoped) re-enqueues dead rows → `status='pending'`, `attempts=0` (log of replay events, outbox itself).
- No automatic push of DLQ rows to an external sink in Phase 4 (keep scope tight).

---

## 13. Webhook security model

The mission's mandatory concerns, mapped to concrete controls for the (later-phase) `WebhookAdapter`:

| Concern | Control |
|---|---|
| **Secret storage** | Per-endpoint secret, symmetric-encrypted in `webhook_endpoints.secret_encrypted` with `secret_key_ref` (reuse existing crypto primitives: `infrastructure/security/` has token/password machinery; app-level symmetric envelope follows the same storage pattern used for keys). Never log the secret. |
| **Signature verification** | `HMAC-SHA256(secret, body)` sent as `X-Specter-Signature`; consumer verifies timestamp + digest. Signature covers the raw body + timestamp + event id → integrity + replay binding. |
| **Replay protection** | Header `X-Specter-Time` + `X-Specter-Event-Id`; consumers accept only `|now - t| <= replay_window` (default 5 min) and dedupe/record event id. |
| **SSRF** | URL validation at registration **and** resolution-at-delivery: scheme allowlist (`https` prod / `http` dev), **DNS-rebind guard** — resolve host, reject private/loopback/link-local/metadata IPs (169.254.169.254 etc.) at delivery time using the already-proven IP-validation logic in `target_validation.py` / Scope Guard. No redirect following (or cap + re-validate). |
| **Target URL validation** | Registration rejects `http(s)` URLs pointing at internal schemes; delivery re-validates per attempt (DNS rebinding defense). |
| **Tenant isolation** | Endpoints are org-scoped; the relay only routes rows whose `organization_id` matches the endpoint's org; cross-tenant routing is impossible (§14). |
| **Authorization** | Webhook CRUD requires org Owner/Admin; secrets are write-only (never returned by GET). RF7807 problem-details errors reused. |
| **Payload leakage** | Redaction-before-emission (§7.1): default payloads carry severity/counts/ids, not raw findings; any future enriched payload goes through the same redactor `ReportService.`/`AIService` facility used by report versions. |

Important boundary finding: **the M7.4 execution-path Scope Guard is verified to remain the authoritative target gate.** The webhook URL allow-list is a *delivery* control, not a scan target control — the two must not be conflated.

## 14. Tenant isolation model

- Every outbox row carries `organization_id`; the relay's **subscribe filter is org-scoped** → you cannot cross an org boundary through routing.
- Endpoints belong to exactly one org (org-scoped table). A dispatcher instance is constructed per-org with that org's endpoints only.
- Campaign/run events derive `organization_id` from **project → organization** (project membership/hierarchy via `projects`/`organizations` models), fixing the audit's known gap (§1.2) from day one.
- API endpoints for webhook management: org Owner/Admin enforced by existing permission dependencies (extend `deps.py` pattern — read-only analysis confirms `require_*` permission dependencies exist, e.g. `require_scan_launch_permission` at ~`deps.py:843-860`).

## 15. Report automation assessment

**Recommendation: report automation is a PHASE 5 concern — keep it out of Phase 4.**

- Findings: reports are **API-triggered only** (`routers/reports.py:32-107`); `ReportService.create` (`report_service.py:71-78`) makes a draft row; `generate_version` produces versioned markdown; AI drafting is on-demand JSON (`ai_reporter_service.py`, `ai_engine.py:342-365`); intelligence `generate_report` returns in-memory objects, not persisted reports. **There is no celery task, no automatic report on completion.**
- Why Phase 5: automatic report generation is a *business action with its own state machine* (draft → generated → versioned → finalized, redaction, file storage). Phase 4's job is the **durable event substrate**; the report generator is one of its future consumers.
- Phase 4 interaction: Phase 4 defines and emits `campaign.run.completed` (the exact event a Phase 5 reporter consumes). Phase 5 adds the `ReportAutomationAdapter` that listens for it. This is the clean seam.

## 16. Observability requirements

Must eventually be observable (no implementation now):
- **Queue depth / latency**: pending count, oldest-pending age, delivered targets. Add Prometheus counters + a `GET /api/v1/metrics` histogram (metrics router already mounted at `main.py:71`).
- **Delivery attempts / successes / failures**: counters per status code class, per adapter — via `infrastructure/core/metrics.py` patterns.
- **Retries**: attempts histogram, backoff distribution.
- **Dead-letter**: current DLQ count, last DLQ timestamp (alertable).
- **Emission volume/timing**: events per `type` per org, emit→relay→delivered latency p50/p95.
- **Structured logs** for every relay decision: `outbox_claim`, `outbox_deliver`, `outbox_retry_scheduled`, `outbox_dead_letter` — consistent with existing `scan_queue_dispatch_*` logging style (`dispatch_after_commit.py:84-96`).

## 17. Database impact proposal

- One new table `event_outbox` (+ partial index). No changes to any existing table. Campaign fire/advance/recovery boundaries get an *insert* of one row inside their existing commits — no schema change to existing entities.
- `webhook_endpoints` table in the later webhook phase.
- **Rollback safety**: migration is additive; reverting the feature only stops emitting (no data dependency from existing code paths).
- Volume estimate at current cadence: events are proportional to campaign fires/cycles — trivial for postgres at this stage; no partitioning needed now. Index `(status, available_after, next_retry_at)` keeps claim scans narrow.

## 18. API impact proposal

- **Existing endpoints: none change.** Campaign/schedule/run APIs are untouched (protected contracts).
- New (later phases, additive):
  - `GET/POST /api/v1/organizations/{org_id}/webhooks` — CRUD, org-admin only (**Phase 4-C**).
  - `POST /api/v1/webhooks/{id}/secret/rotate` (**Phase 4-C**).
  - `POST /api/v1/organizations/{org_id}/events/{id}/replay` — admin replay of dead-letter (**Phase 4-D**).
  - `GET /api/v1/organizations/{org_id}/events` — read-back of emitted events (**Phase 4-D**, fills the audit read-back gap deliberately — extend only the event table, not `audit_logs`).
- No API is required for the substrate phase (4-A/4-B are backend-internal; verifiable by tests + logs).

## 19. Proposed implementation phases

Each phase is independently verifiable; Phase 3 GIT RULES analog: commit per phase when green.

### Phase 4-A — Outbox substrate + transactional emission
- **Objective:** durable `event_outbox`; emit the campaign lifecycle events atomically at the §6 boundaries with correct envelope construction.
- **Files:** `backend/alembic/versions/<new>_m75_phase4_event_outbox.py`; `domain/entities.py` (`EventRecord`), `domain/repositories.py` (`EventRepository`); `application/event_emitter.py`; `infrastructure/db/models/event_outbox.py` + `repositories/event_outbox_repository.py`; wire emit calls in `campaign_scheduler_service.py` / `autonomous_orchestrator.py` (or via a shared helper at `tasks.py:535/733` boundaries).
- **Schema impact:** `+event_outbox` (additive). **API impact:** none. **Security:** payloads redacted from day one.
- **Tests:** unit — emitter + envelope builder + redaction, atomicity simulated (fakes with rollback); integration — real PG: fire campaign in test txn → row committed atomically; rollback → no row. **Live:** run stack campaign, observe rows in `event_outbox`.
- **Rollback:** revert migration (additive only), remove emit calls. **Done when:** every §6 event appears exactly once per committed transition in PG under load.

### Phase 4-B — Relay, retries, dead-letter
- **Objective:** `specter.outbox_relay` beat task (FOR UPDATE SKIP LOCKED claim, adapter dispatch, backoff, DLQ); stats/logging.
- **Files:** `infrastructure/celery_app/tasks.py` (+beat schedule in `celery_app/app.py:35-44`), `infrastructure/event/relay.py`, `domain/repositories.py` (+claim methods), `core/config.py` (relay interval, base/max backoff, max attempts), `core/metrics.py`.
- **Schema impact:** none (columns already present). **API impact:** none.
- **Security:** rate-gate per endpoint/org (leroy: local rate limit in relay), no secrets logged, DLQ retains full row.
- **Tests:** unit — backoff schedule, crash mid-delivery (lock lost → retried), 4xx → DLQ; integration — real PG claim concurrency one-winner. **Live:** beat runs, rows progress pending→delivered / dead_letter; metrics counters move.
- **Rollback:** disable beat task. **Done when:** at-least-once demonstrated: kill relay mid-delivery → row retried, single delivered for consumer-supplied idempotency.

### Phase 4-C — Webhook delivery + security
- **Objective:** `WebhookAdapter` (HMAC-SHA256, time-window, SSRF-guarded delivery), endpoint CRUD API.
- **Files:** `api/v1/routers/webhooks.py` (+registry in `router.py`), `api/v1/schemas/webhooks.py`, `application/report?` no — `infrastructure/event/webhook_adapter.py`, `db/models/webhook_endpoint.py`, secrets plumbing in `infrastructure/security/`.
- **Schema impact:** `+webhook_endpoints`. **API impact:** new CRUD + secret rotate (§18).
- **Security:** full §13 matrix; endpoint registration re-validates URL/DNS; secrets write-only; tenant-scoped routing.
- **Tests:** unit — HMAC verify/forge detection, replay window, SSRF deny-list (localhost/metadata/rebind simulation), redaction; API — RBAC (owner/admin vs member/other-org), secret never leaked; integration — live HTTP test sink (echo server) success, 4xx→DLQ, 5xx→retry. **Live:** register endpoint in stack, observe signed delivery + ack.
- **Rollback:** remove router entry. **Done when:** every §13 control has a green test + live demo.

### Phase 4-D — Read-back, replay, observability, verification
- **Objective:** event read-back + DLQ replay endpoints, metrics histogram, full regression + verification report/handoff.
- **Files:** `api/v1/routers/events.py`, `core/metrics.py`, DLQ replay in relay, `MILESTONE_3_VERIFICATION_M75_PHASE4.md`.
- **Schema impact:** none. **API impact:** `GET /events`, `POST /events/{id}/replay`.
- **Security:** admin-only, org-scoped, replay audited. **Tests:** replay idempotency, metrics assertions; full `pytest` at baseline+new. **Live:** end-to-end campaign → outbox → signed webhook sink. **Rollback:** n/a (revertive). **Done when:** full clean run, security review note passed, report written.

## 20. Explicit non-goals (Phase 4)

- Frontend changes (frontend stays read-only poller). Email / Slack / Teams / Discord / WebSockets / SSE.
- CI/CD integration. New plugins, new AI/planner/executor work, execution-engine changes.
- Campaign-scheduler redesign; any M7.4/7.5-protected contract change (executor isolation, Scope Guard, plugin validation, planner/approval/correlation semantics, run state machine, recovery/cancellation, project concurrency, fire guarantees).
- Report automation (→ **Phase 5**, §15). Replacing or removing the M7.4 after-commit scan dispatcher (stays; ordering guard is separate from outbox).
- Distributed event broker / message queue substitution (postgres + celery is sufficient).
- Changing `audit_logs` semantics or backfilling.

## 21. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Outbox growth unbounded (DLQ/pending rows accumulate) | Retention policy (Phase 4-D), parity with audit retention; alert on DLQ depth |
| Relay claim storm on restart | `available_after` jitter, bounded claim batch size; same SKIP-LOCKED pattern already proven |
| Webhook SSRF/DNS-rebinding | §13 delivery-time resolution + no-redirects + denylist; re-validate every attempt |
| Consumer non-idempotency double-processing | envelope `id` contract; Phase 4-B test harness (dedupe double-delivery is no-op) |
| Secret leakage in logs | never log bodies/secrets; redacted payloads; structured log allow-list |
| Emit path introduces latency/failure into cycle txn | single INSERT, same commit; failures to *write* event = fail the transition as today (audit is already coupled the same way); optionally async later — never async now |
| Scope creep into M7.4 | enforced by §20 non-goals + review gate per phase |

## 22. Test strategy

- **Unit (fakes, no infra):** envelope builder, redaction, backoff math, de/claim logic, HMAC verify + tamper rejection, replay-window expiry, SSRF validation, RBAC on webhook CRUD, replay idempotency.
- **Integration (real PG, `requires_postgres` marker per existing `tests/integration/conftest.py`):** atomic emission (commit→row, rollback→no row); claim one-winner concurrency (SKIP LOCKED); retry progression; DLQ transition.
- **API (httpx ASGI client):** webhook endpoint CRUD + org isolation + secret write-only + 404/403/422 problem-details shapes (reuse `tests/fakes.py` doubles and `error_handlers.py`).
- **Live (stack):** campaign → outbox row → relay → signed delivery to a local HTTP sink; kill-relay retry demo; observability counters.
- **Baseline discipline:** every phase ends with full `pytest` — current baseline `938 passed / 0 failed / 2 warnings` must not regress.

## 23. Definition of Done

1. `event_outbox` schema + `EventRepository` + transactional emission at all §6 points; rollback-safe (additive migration).
2. Relay with SKIP-LOCKED claim, at-least-once, backoff retries, DLQ — exactly once per committed transition, no phantom events.
3. No M7.4/7.5 protected contract touched; execution-time Scope Guard untouched and authoritative; campaign fire guarantees intact.
4. Webhook security matrix (§13) fully green; tenant isolation unit-tested across agencies; redaction enforced for payloads.
5. Full test suite green (938 + new, 0 failures); ruff/mypy at baseline (accepted diff only for new modules).
6. Live end-to-end demonstration on the stack: campaign reaches COMPLETED → signed webhook delivered at least once, idempotent consumer dedupes.
7. `MILESTONE_3_VERIFICATION_M75_PHASE4.md` written; commit/push/tag per phase rules.

---

## STOP-CONDITION CHECKLIST (from mission)

| Condition | Result |
|---|---|
| Durable event cannot be atomically coupled to an important transition | **Not triggered** — every §3 boundary has a live session; outbox row joins the existing commit |
| Design requires weakening M7.4 guarantees | **Not triggered** — outbox sits outside the execution path; reads only committed state |
| Tenant isolation cannot be guaranteed | **Not triggered** — org-id on every row + org-scoped routing/endpoints |
| Webhook delivery introduces unavoidable SSRF problem | **Not triggered** — delivery-time resolution + no-redirects + allowlist (§13) |
| Transactions insufficient → major redesign | **Not triggered** — additive table + one insert per existing commit |
| New infrastructure dependency appears necessary | **Not triggered** — postgres + existing celery beat suffice |

---

## Key Findings Summary

1. The only durable event-ish record is `audit_logs`, which is **write-only, org-less on most rows, and has zero delivery machinery** — it cannot back event delivery.
2. The existing "outbox-ish" scan dispatch (`dispatch_after_commit.py`) is **in-memory and non-durable**; it is a commit-ordering shim, not a queue — keep separate, reuse its after-commit pattern.
3. All campaign/run/schedule commit points have a live session → a transactional outbox row is trivially atomic today, with no schema redesign.
4. **There are no external consumers yet** (no webhooks/notifications/integrations in code). Build the durable substrate; wire adapters only as consumers arrive.
5. Report automation lacks any automatic path and should be **Phase 5**, consuming the Phase 4 `campaign.run.completed` event.
6. Postgres + existing Celery beat are sufficient; no distributed broker needed.
7. Baseline confirmed: **938 passed / 0 failed / 2 warnings** (live stack up during verification run).

## Report Path

`MILESTONE_3_ARCHITECTURE_M75_PHASE4.md` (repo root).

## Git Status

Clean working tree at `b3d7644`. No source code, migration, API, or test files were modified during this investigation. No commit, push, or tag.