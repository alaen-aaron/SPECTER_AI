# MILESTONE_3_ARCHITECTURE_M75_PHASE4B.md

SPECTER_AI — Milestone 3, M7.5 Phase 4-B (Event Outbox Delivery Engine). **Reconnaissance-only report.**

## 1. Purpose, Scope, Mandate

- **Mandate:** Investigate the durable `event_outbox` consumer/delivery architecture for M7.5 Phase 4-B. **Recon only** — no implementation, no code/schema/API/DB changes, no commits/pushes/tags.
- **Primary question:** How should a relay consume outbox events and deliver them externally, while guaranteeing the critical constraint that **no external HTTP delivery ever happens while a DB row lock is held**?
- **Delivery semantic:** at-least-once. No exactly-once claim is made; idempotency/dedupe is the consumer's responsibility via the envelope id.
- **Constraint:** do not introduce a second event system. Phase 4-B extends the existing `event_outbox` substrate.
- **Outcome:** a report that (a) documents the shipped Phase 4-A substrate, (b) identifies the gap between the design doc's §8.1 column blueprint and the migration that was actually shipped, (c) proposes a concrete Phase 4-B breakdown (4-B1…4-B4), (d) lists critical risks and mitigations, and (e) records the test baseline.

## 2. Method & Evidence Base

- Read, in full: the Phase 4-A migration `backend\alembic\versions\e2f3a4b5c6d7_m75_phase4a_event_outbox.py`; `outbox_service.py`; `event_payloads.py`; `event_outbox_repository.py`; ORM model `event_outbox.py`; `dispatch_after_commit.py`; `session.py`; `tasks.py`; `workflow_repository.py`; `autonomous_run_repository.py`; `config.py`; `deps.py`; domain `OutboxEvent`, `OutboxEventRepository`, `OutboxEventType`; integration test `test_m75_phase4a_outbox_integration.py`.
- Re-read the Phase 4 design document `MILESTONE_3_ARCHITECTURE_M75_PHASE4.md`: §1.6 (audit-log rejection), §4 (existing delivery mechanisms), §5 (existing consumers), §8.1 (`event_outbox` column blueprint), §8.3 (`webhook_endpoints` = later phase), §19 (Phase 4-B intended scope).
- Greps: all `record_campaign_run_*` / `OutboxService` call-sites; all `queue_dispatch` / `drain_pending_dispatches` call-sites; webhook/HMAC/consumer/delivery/signing references across the repo.
- NOTE: path correction — the cancel router is `backend\app\api\v1\routers\autonomous.py` (the hypothesized `backend\app\api\v1\autonomous.py` does not exist).

## 3. Repository State

- HEAD: `1f7f389` `feat(m75): add durable campaign event outbox` (Phase 4-A), pushed.
- Worktree clean (verified `git status --short` → empty).
- Prior milestones present: Phase 2 `44a61f4` (autonomous run project isolation), Phase 3 `b3d7644` (scheduled autonomous campaigns).
- No source changes made during recon.

## 4. Headline Findings

1. **Phase 4-A shipped a transaction-safe, additive-only outbox core** — table, service, payload builders, repository, domain types, DI wiring, integration test. The emission half of the durable trail is complete and tested.
2. **There is no consumer.** No relay, no `specter.outbox_relay` beat task, no delivery adapter, no retry/backoff, no DLQ, no webhook config, no HMAC/signing utility, no `webhook_endpoints` table. The outbox fills up and nothing reads it.
3. **Phase 4-B is NOT schema-neutral.** The design doc §19 assumed "schema impact: none (columns already present)". In reality the shipped migration omitted `scan_id`, `specversion`, and every §8.1 delivery column. Delivering per §8.1 requires an **additive follow-up migration** (see §7).
4. **The correct delivery seam already exists:** `get_db_session` commits after the handler, and `dispatch_after_commit.py` establishes the in-process post-commit drain precedents (`session.py:100`, `tasks.py:787`, `tasks.py:966`). A `commit → deliver → record-result` relay preserves the no-lock-while-HTTPS rule.
5. **Claim precedent already exists:** `FOR UPDATE SKIP LOCKED` in `workflow_repository.claim_due` (:356/:379), and `try_cycle_lock` in `autonomous_run_repository.py` (406–415). The relay loop should reuse the same pattern.
6. **A recovery-sweep emission gap exists:** `_recover_stale_autonomous_runs` settles stale runs without emitting outbox events (see §17). Not fixed in this report.

## 5. Phase 4-A Shipped Substrate

| Component | Location | Notes |
|---|---|---|
| `event_outbox` table | `alembic/versions/e2f3a4b5c6d7_m75_phase4a_event_outbox.py` (78 lines) | additive-only, no FK refs, no delivery columns |
| ORM model | `infrastructure/db/models/event_outbox.py` (60 lines) | docstring defers delivery design to the 4-B draft §8.1 |
| Event payload builders | `application/event_payloads.py` (69 lines) | whitelisted, non-sensitive |
| Service | `application/outbox_service.py` (176 lines) | `record_campaign_run_started/completed/failed/cancelled`; never commits |
| Repository | `infrastructure/db/repositories/event_outbox_repository.py` (53 lines) | `session.add` + `flush` only |
| Domain types | `entities.py` `OutboxEvent` (740–764); `repositories.py` `OutboxEventRepository` (439–449); `value_objects.py` `OutboxEventType` (387–400) | zero framework imports |
| DI wiring | `api/v1/deps.py` (267–276) | `get_outbox_event_repository` → `SqlAlchemyOutboxEventRepository`; `get_outbox_service` → `OutboxService(repo)` |
| Integration test | `tests/integration/test_m75_phase4a_outbox_integration.py` | real Postgres; auto-skip when DB unreachable |

## 6. `event_outbox` Schema as Built

Column set shipped by `e2f3a4b5c6d7`:

- `event_id` UUID — PK, also the envelope/idempotency-relevant identity (v7-style within the assembler)
- `type` String — envelope type
- `schema_version` Integer NOT NULL default 1
- `payload` JSONB NOT NULL default '{}'
- `organization_id` UUID NOT NULL — indexed
- `project_id` UUID — indexed
- `autonomous_run_id` UUID — indexed
- `schedule_id` UUID — indexed
- `occurred_at` timestamptz NOT NULL — business time of the event
- `created_at` timestamptz NOT NULL — storage time

Design notes (as shipped):
- Subject references are **plain indexed UUIDs, deliberately NOT FKs** — an append-only trail must never block on, or be blocked by, subject lifecycle (verified by the ON DELETE SET NULL integration test scenario).
- `occurred_at` (business) vs `created_at` (storage) separation is preserved; useful for delivery-time ordering and lag metrics.
- 4 indexes total, one per reference column.

## 7. Phase 4-A Delivery-Column Deviation vs §8.1

The design doc §8.1 (`MILESTONE_3_ARCHITECTURE_M75_PHASE4.md` lines 233–256) blueprinted a full delivery-capable schema. The migration that actually shipped **omitted** the following §8.1 columns:

| Column | Type / Default (per §8.1) | Status |
|---|---|---|
| `scan_id` | UUID NULL | **absent** — needs addition |
| `specversion` | String(5) NOT NULL default "1.0" | **absent** — needs addition |
| `available_after` | timestamptz NOT NULL default now() | **absent** |
| `status` | String(20) NOT NULL default "pending" (`pending`\|`delivering`\|`delivered`\|`dead_letter`) | **absent** |
| `attempts` | Integer NOT NULL default 0 | **absent** |
| `max_attempts` | Integer NOT NULL default (config value, **snapshotted at enqueue**) | **absent** |
| `last_error` | String(500) NULL | **absent** |
| `next_retry_at` | timestamptz NULL | **absent** |
| `delivered_at` | timestamptz NULL | **absent** |

Also §8.1 calls for:
- **partial claim index** on `(status, available_after, next_retry_at)` for the `pending/due` claim scan — **absent**
- index on `(organization_id)` — present (one of the four shipped indexes)

**Consequence:** Phase 4-B requires a new additive Alembic migration (`down_revision = e2f3a4b5c6d7`) adding the delivery columns + partial index. The design doc's "schema impact: none" assumption is stale.

**Recommendation (primary):** follow §8.1 — additive columns on `event_outbox`. Alternative considered and rejected as primary: a sibling `event_delivery` table keyed by `event_id` (plain UUID ref, no FK) would also work, but diverges from the model's and doc's documented intent and adds a second ledger to keep in sync.

## 8. Outbox Service Contract

`OutboxService` (`application/outbox_service.py`, 176 lines):
- `record_campaign_run_started(...)` — adds `run_started` event; never `commit()`
- `record_campaign_run_completed(...)`
- `record_campaign_run_failed(...)` — carries `error_message` in payload
- `record_campaign_run_cancelled(...)`
- All four build payloads via `event_payloads.py` builders and delegate to `_record()`.
- Repository performs `session.add` + `flush` only → the event becomes durable **only when the enclosing transaction commits**, and disappears if the transaction rolls back (proven by integration tests).

Contract invariants:
- Events are persisted in the same transaction as the domain transition → one commit, atomic.
- The service never commits → caller controls the boundary; the cancel router intentionally uses `get_db_session` (commit-after-handler) so the CANCELLED run and its event commit together.
- No delivery side effects at emission time.

## 9. Payload Whitelist Policy

`event_payloads.py` (69 lines) enforces:
- **Never** serialize entities (`asdict`/`model_dump` of an ORM or domain object is forbidden — would leak credentials, plugin configs, internal wiring).
- Only **stable ids** + concise non-sensitive lifecycle metrics: run_id, schedule_id, project_id, organization_id, timestamps, status transitions, and (for `failed`) a short `error_message` identifier/text.
- **Never**: tokens, passwords, API keys, webhook secrets, full configs.
- `completed` / `cancelled` payloads deliberately omit `error_message`.

Phase 4-B consumers must rely on this whitelist and must not expect arbitrary payload expansion.

## 10. Transactional Emission Guarantees

Verified by `tests/integration/test_m75_phase4a_outbox_integration.py` (real Postgres):
- Same-transaction commit → event durable in `event_outbox`.
- Rollback → event removed with the domain change (no orphan emission).
- Failed event write aborts the enclosing domain transition (atomicity preserves referential truth).
- Subject deletion does not cascade into `event_outbox` (plain UUID refs, ON DELETE behavior) — the audit trail survives.

The consequence for Phase 4-B: a relay can trust that a visible row is the durable record of a committed transition; it never needs to reconcile half-emitted domain work.

## 11. Emission Call-Site Inventory

All emission points as of HEAD `1f7f389`:

| Call-site | Event |
|---|---|
| `celery_app/tasks.py:347` | run started |
| `tasks.py:421` | run started (execution path) |
| `tasks.py:433` | run failed |
| `tasks.py:522` | run failed |
| `tasks.py:551` | run completed |
| `tasks.py:621` | run completed |
| `tasks.py:763` | run failed (cleanup path) |
| `tasks.py:772` | run completed (cleanup path) |
| `tasks.py:780` | run cancelled |
| `api/v1/routers/autonomous.py:119` | run started (cancellation path context) |
| `api/v1/routers/autonomous.py:127` | run cancelled |

Every emission is the service call (no raw repo inserts outside the service layer).

## 12. Cancellation Path Details

- Route: `POST /autonomous-runs/{run_id}/cancel` — `backend\app\api\v1\routers\autonomous.py:108–131`.
- Authorization: `Depends(require_project_role_for_run(ProjectRole.OWNER, ProjectRole.ADMIN))` — only owners/admins may cancel.
- Database: uses `get_db_session`, whose dependency commits **after** the handler returns → the CANCELLED run state change and its outbox event commit in the same request transaction (no window where the event survives the state change being lost, or vice versa).
- Emission at `routers/autonomous.py:127`: `outbox_service.record_campaign_run_cancelled(run=run, organization_id=project.organization_id)`.
- Cancellation is soft/cooperative — status flip, not subprocess kill; the outbox trail records the transition but never couples to process teardown.

## 13. Post-Commit Delivery Seam

- `get_db_session` (session dependency) commits and then drains after-commit work (orchestrated from `session.py:100`).
- This is the exact boundary the relay needs: **the event is durable before delivery is attempted**, and delivery result writing and the delivered flag share a later transaction.
- Pattern for the relay per run of an event:
  1. `BEGIN` → `claim event (FOR UPDATE SKIP LOCKED, status → delivering)` → `COMMIT` (row lock released).
  2. External delivery (HTTPS to consumer endpoint) — **no DB lock held**.
  3. `BEGIN` → record result (`delivered`/`dead_letter`, `attempts+1`, `last_error`, `next_retry_at`, `delivered_at`) → `COMMIT`.
- This is the `BEGIN claim / COMMIT → deliver → BEGIN record / COMMIT` requirement verbatim, with a real precedent in the repo (§14).

## 14. After-Commit Delivery Precedent

`infrastructure/celery_app/dispatch_after_commit.py` (97 lines):
- `queue_dispatch` buffers work in a `ContextVar`-backed set; dedup by scan id; per-id `try/except` on `scan_queue_dispatch_failed`.
- `sender_unbound` → `scan_queue_dispatch_dropped` metric (buffer exhaustion safety valve).
- Drain hook: `session.py:100` (post-commit) and in-process from `tasks.py:787` / `tasks.py:966`.
- `CeleryWorkflowTaskDispatcher.dispatch_workflow(execution_id)` calls `.delay` directly (no after-commit wrapper) — so the scan `queue_dispatch` path is the stronger in-repo precedent for 4-B (buffer → commit → drain → deliver).
- Note: this precedent covers **in-process** dispatch. Phase 4-B must additionally survive an API process crash between commit and drain — which is precisely why durable `event_outbox` rows are the source of truth and why the relay (a beat task, separate from the request path) is the correct consumer.

## 15. Claim / Lock Precedents

- `workflow_repository.claim_due` (:356) — `select(...).with_for_update(skip_locked=True)` (:379), default limit 50: batch claim idiom already in the codebase.
- `autonomous_run_repository.try_cycle_lock` (406–415): single-row optimistic-cycle lock.
- Relay should reuse `FOR UPDATE SKIP LOCKED` for the `(status='pending' AND available_after <= now AND (next_retry_at IS NULL OR next_retry_at <= now))` claim scan, ordered by `available_after`, with a bounded batch — no new locking primitive is required.

## 16. What Does NOT Exist Yet (Gap Inventory)

Verified by repo-wide grep — **none of the following exist**:
- Any consumer/relay/reader of `event_outbox`.
- Celery beat task (`specter.outbox_relay` or equivalent). Beat schedule lives at `celery_app/app.py:35–44`; nothing outbox-related is registered.
- Delivery adapter interface / implementations (`infrastructure/event/relay.py` draft path unclaimed).
- `OUTBOX_RELAY_ENABLED`, relay interval, `OUTBOX_RELAY_BASE_BACKOFF`, max backoff, `OUTBOX_MAX_ATTEMPTS`, or any delivery config in `core/config.py` (131 lines).
- `webhook_endpoints` table (deferred to Phase 4-C per §8.3) and the webhook dispatch adapter.
- HMAC / signing / secret-verification utilities for outbound delivery.
- Metrics for delivery (delivered, dead-lettered, attempts, in-flight).
- Consumer-side idempotency harness / test double.

## 17. Coverage Gaps (Recon Observations — NOT Fixed)

- `_recover_stale_autonomous_runs` (tasks.py sweep, 830–971) settles stale runs (marks failed/completed) **without emitting outbox events**. A run that dies and is recovered by the sweep will have no `run_failed` outbox event. Phase 4-B consumers must tolerate missing terminal events, or the sweep must adopt the outbox emission (a small, deliberate Phase 4-B or follow-up change — outside this recon report's mandate to implement).
- No consumer means rows accumulate unread; no purge/retention policy exists yet (out-of-scope for 4-B but should be tracked).

## 18. Phase 4-B Scope vs Design Doc §19 (Adjusted)

Design doc §19 Phase 4-B intended scope (as documented):
- `specter.outbox_relay` Celery beat task.
- `FOR UPDATE SKIP LOCKED` claim of next batch.
- Adapter dispatch.
- Retry/backoff; dead-letter handling; stats/logging.
- Planned files: `infrastructure/event/relay.py`; claim methods on `domain/repositories.py`; relay config in `core/config.py`; `core/metrics.py`; beat schedule registration in `celery_app/app.py`.

**Adjustment to §19:** replace "schema impact: none" with a new first deliverable: **4-B1, an additive migration** adding the §8.1 columns + partial claim index (see §7).

## 19. Recommended Phase 4-B Breakdown

### 4-B1 — Delivery-ledger migration (schema)
- New Alembic revision `down_revision = e2f3a4b5c6d7`.
- Add §8.1 columns: `scan_id` UUID NULL; `specversion` String(5) NOT NULL default "1.0"; `available_after` timestamptz NOT NULL default now(); `status` String(20) NOT NULL default "pending"; `attempts` Integer NOT NULL default 0; `max_attempts` Integer NOT NULL default (config snapshot at enqueue — enforce in code, DB default via app-level insert); `last_error` String(500) NULL; `next_retry_at` timestamptz NULL; `delivered_at` timestamptz NULL.
- Partial index on `(status, available_after, next_retry_at)` (`WHERE status IN ('pending','delivering')`).
- No table rewrite, additive only, backwards compatible with existing rows (`status='pending'`, `available_after=now()` for backfilled rows).
- Update service `_record` to stamp `specversion`, `max_attempts` (from settings), `available_after`.
- Tests: migration up/down; backfill keeps existing rows claimable.

### 4-B2 — Relay task & claim loop
- `specter.outbox_relay` beat task (register in `celery_app/app.py:35–44` schedule), interval-driven; no per-emission side effects in the API path.
- Batched `FOR UPDATE SKIP LOCKED` claim per §15; status `pending → delivering` in the claim transaction (commit → lock released) per §13.
- In-memory/per-failed dry-run no-ops acceptable for first increment — HTTP delivery is a separate concern that can land behind a flag.
- Interrupt-safe: claim commits before any work; crash between claim and record leaves `delivering` rows, which the next tick re-claims via `next_retry_at` backoff.
- Tests: claim-only (no side effects); crash/re-claim; ordering by `available_after`; batch bounds.

### 4-B3 — Retry / backoff / dead-letter
- `attempts` increments; on failure set `last_error`, `status='pending'` (retryable), `next_retry_at = now + min(base * 2^(attempts-1), max_backoff)`.
- After `attempts >= max_attempts` → `status='dead_letter'` (terminal).
- `status='delivered'` sets `delivered_at`.
- Metrics: `outbox_delivered`, `outbox_dead_lettered`, `outbox_attempts`, `outbox_in_flight` (gauge), `outbox_relay_lag` (now − occurred_at).
- No infinite retry; max attempts snapshotted at enqueue.

### 4-B4 — Adapter seam + delivery test harness
- Define `DeliveryAdapter` protocol with `deliver(envelope) -> DeliveryResult` and an **idempotency contract**: consumers dedupe on envelope `id`; relay must tolerate duplicate deliveries (at-least-once is the guarantee, not exactly-once).
- Reference adapters for tests: an in-memory recorder and a stdout/logging adapter. **No HTTP adapter in 4-B** (webhook delivery, `webhook_endpoints`, SSRF hardening, no-redirects, allowlist, HMAC verification are Phase 4-C).
- Design the adapter boundary now so 4-C only swaps in the HTTP implementation.
- Tests: relay → adapter double → delivered flag; delivered-at-least-once across simulated crash/retry; double-delivery idempotency (adapter receives duplicate, still a success outcome per contract).

## 20. Security / Integrity Constraints

- **No external delivery while holding a row lock.** Enforced structurally by §13's three-transaction pattern — the relay never performs network I/O inside a DB transaction.
- **No second event system.** 4-B consumes `event_outbox` only; webhook-specific state (`webhook_endpoints`) is 4-C.
- **No exactly-once overclaim.** The system guarantees at-least-once; idempotency is consumer-side via envelope id.
- **Payload whitelist stays.** 4-B delivery payloads are the §9 builders; no expansion without a security review.
- **SSRF is 4-C, not 4-B.** Delivery-time URL resolution, IP allowlist, no-redirect enforcement belong with the HTTP adapter and `webhook_endpoints` management.
- Soft/cooperative cancellation unaffected: cancellation flips state; the outbox records the transition; no coupling to process teardown.

## 21. Critical Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Delivery under row lock (violates hard requirement) | Relay code review gate: no `httpx`/socket I/O inside a DB transaction; code review checklist asserts the 3-transaction pattern; integration test mocks a delivery that could only succeed after commit. |
| Crash between claim and deliver | Available-again semantics: `delivering` rows become claimable via `next_retry_at`; max attempts bounds duplicates. |
| Duplicate deliveries (at-least-once) | Consumer idempotency contract documented; envelope id = dedupe key; 4-B4 harness verifies duplicate-tolerant adapter semantics. |
| Schema drift: §8.1 columns missing | 4-B1 migration + test asserting full column set before relay ships. |
| Unbounded retry / hot-loop | `max_attempts` snapshot at enqueue; exponential backoff capped at `max_backoff`; batch bounded; `dead_letter` terminal. |
| Relay starves under pileup | Order by `available_after`; bounded batch; lag metric alerts; retention/purge tracked as follow-up (not 4-B). |
| Recovery-sweep non-emission | Documented gap (§17); decide in follow-up whether sweep adopts outbox emission. Do not silently assume consumers see terminal events for swept runs. |
| Config/token hygiene | No secrets in payloads (§9); webhook secrets only in 4-C behind KMS/secret store. |

## 22. Test Plan & Acceptance Criteria (Phase 4-B)

- **Migration:** 4-B1 applied forward/backward; existing rows backfilled claimable; column set matches §8.1 (assert in a test).
- **Claim:** only `pending`+due rows claimed; `FOR UPDATE SKIP LOCKED` behavior (two concurrent relays don't double-claim).
- **Crash/re-claim:** simulates crash after claim commit; row re-claimed next tick with backoff.
- **Delivery adapted:** relay → double delivers and marks `delivered` + `delivered_at`; a `dead_letter` path reaches terminal state after `max_attempts`.
- **Idempotency:** duplicate deliveries are a success outcome (per at-least-once contract), never an error loop.
- **No-lock-when-HTTPS:** regression-style test using a delivery double that asserts delivery occurs outside any DB transaction.
- **Canceled-run parity:** cancelling a run emits event with same-txn commit; relay delivers it like any other.

## 23. Baseline (Verified at HEAD `1f7f389`)

- Backend pytest: **947 passed, 2 pre-existing flakes**.
- ruff: 161 errors (0 in new code; repo-wide pre-existing).
- mypy (strict): 38 errors / 11 files (parity with pre-4-A state).
- Integration test `test_m75_phase4a_outbox_integration.py` green against real Postgres; auto-skips when `DATABASE_URL` unreachable.

## 24. Documented Exclusions

- Phase 4-C (webhook delivery, `webhook_endpoints` table, SSRF hardening, HMAC/secrets, HTTP adapter) is explicitly out of scope for 4-B.
- Retention/purge policy and consumer-driven event archiving are follow-ups, not 4-B.
- Recovery-sweep outbox emission (§17) is flagged for a decision, not implemented here.
- No CI workflow existed or was assumed (repo has no `.github/workflows/`).

## 25. Recommended Phase 4-B Work Order

1. **4-B1** migration (columns + partial index) + service stamping (`specversion`, `max_attempts`, `available_after`) → run migration + integration tests.
2. **4-B2** relay task + claim loop + beat schedule registration, behind `OUTBOX_RELAY_ENABLED`; claim-only tests.
3. **4-B3** retry/backoff/DLQ + metrics.
4. **4-B4** adapter interface + delivery test harness (no HTTP).
5. Full `make test`, `make lint`, `make format`, and a `make down`/`make up` stack verification before commit.

## 26. Submission Verification

- Report path: `MILESTONE_3_ARCHITECTURE_M75_PHASE4B.md` at repository root; presence verified (`Test-Path` → True).
- `git status --short` at submission returned exactly `?? MILESTONE_3_ARCHITECTURE_M75_PHASE4B.md` — no tracked file modified; worktree otherwise clean at HEAD `1f7f389`.
- Recon-only mandate held: no code, schema, migration, configuration, or test file was created or changed during this investigation.
- This report is reconciled to 27 numbered sections per the task directive.

## 27. Close-Out Statement

- This report is recon-only; **no code, schema, migration, or configuration was changed** during its production.
- Worktree verified clean at `1f7f389` before and after the investigation.
- The critical design rule — external delivery never occurs while a DB row lock is held — is achievable with an existing, proven in-repo pattern (`dispatch_after_commit` drain + `FOR UPDATE SKIP LOCKED` claim + commit-before-deliver) and the `get_db_session` commit-after-handler seam.
- Next implementation phase (4-B) should start with the 4-B1 schema migration, since the design doc's schema-neutrality assumption does not hold against the actually-shipped migration.