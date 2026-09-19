# SPECTER_AI — M7.5 Phase 4-A Verification Package

**Transactional Outbox Substrate & Atomic Lifecycle Emission**
**Status:** COMPLETE — awaiting approval. All deliverables validated against the live Docker stack and a real Postgres instance. **Nothing committed/pushed/tagged.**

---

## 1. Objective & Scope

Deliver M7.5 **Phase 4-A — Outbox substrate + transactional emission** (§19 of `MILESTONE_3_ARCHITECTURE_M75_PHASE4.md`): a durable `event_outbox` table, a canonical versioned event envelope, and a session-owned outbox writer that persists each campaign lifecycle event **in the same transaction** as the state change it describes. Phase 4-A is **write-only by design**:

- **In scope:** `event_outbox` schema (additive migration), `EventOutbox` domain entity + `OutboxEventRepository` (add-only) Protocol, `OutboxService`, whitelisted payload builders, emission at the fire/advance/cancel boundaries (`tasks.py:545-563`, `tasks.py:765-786`), cancel endpoint in-request emission.
- **Only four event types, all in the `campaign.run.*` namespace:** `started`, `completed`, `failed`, `cancelled`.
- **Explicitly NOT in scope (Phase 4-B/C/D):** relay task, delivery, webhook endpoints, HMAC signing, SSRF protection, retry/DLQ, read-back/replay API, observability metrics, frontend. The outbox is an observation substrate, never an alternate execution path.

**Hard constraint honoured:** no M7.4/M7.5.1/M7.5.3 protected contract touched — the state machine, recovery/cancellation semantics, scheduler locks, Scope Guard, and campaign-fire guarantees are byte-for-byte unchanged. Emission is additive observation on top of untouched transitions.

## 2. Updated Repository Tree (delta)

```
backend/
  app/
    domain/
      value_objects.py                  (CHANGED) +OutboxEventType — StrEnum (campaign.run.*),
                                                 deliberately StrEnum not (str, Enum) to keep
                                                 ruff's pre-existing UP042 count at exactly zero new
      entities.py                       (CHANGED) +OutboxEvent dataclass (id, type, schema, time,
                                                 nullable org/project/schedule/run refs, payload, created)
      repositories.py                   (CHANGED) +OutboxEventRepository Protocol — add() only
    application/
      outbox_service.py                 (NEW)     OutboxService.record_campaign_run_{started,completed,
                                                 failed,cancelled}; injected clock/id_factory/schema_version;
                                                 stores via repository.add — never reads, never commits
      event_payloads.py                 (NEW)     whitelisted payload builders + _truncate(500)
    api/v1/deps.py                      (CHANGED) +get_outbox_event_repository, +get_outbox_service
    api/v1/routers/autonomous.py        (CHANGED) cancel endpoint emits campaign.run.cancelled in the
                                                 request transaction
    infrastructure/
      celery_app/tasks.py               (CHANGED) fire → started (before commit); advance → completed/
                                                 failed (after cycle, before commit)
      db/models/event_outbox.py         (NEW)     EventOutboxModel
      db/repositories/event_outbox_repository.py (NEW) add = session.add + flush (never commit);
                                                 + get_outbox_event_by_id() read-back helper purely for
                                                 tests (outside the domain Protocol)
  alembic/versions/e2f3a4b5c6d7_m75_phase4a_event_outbox.py  (NEW)  applied
  tests/
    unit/test_m75_phase4a_outbox.py            (NEW) 6 tests
    integration/test_m75_phase4a_outbox_integration.py (NEW) 5 Postgres tests
```

## 3. Database Schema Changes

One additive migration, applied and verified live twice (first draft → rebuilt after the FK-decoupling decision in §9):

```
d1e2f3a4b5c6 (Phase 3) --linear--> e2f3a4b5c6d7 (Phase 4-A)
```

`event_outbox` (verified `information_schema.columns` + `pg_indexes` live):

| Column | Type | Notes |
|---|---|---|
| `event_id` | UUID PK | default uuid4 |
| `event_type` | String(100) NOT NULL | `campaign.run.*` |
| `schema_version` | Integer NOT NULL default 1 | `DEFAULT_OUTBOX_SCHEMA_VERSION` |
| `organization_id` | UUID NULL | index — tenant ref (decoupled, §9) |
| `project_id` | UUID NULL | index — project ref (decoupled, §9) |
| `schedule_id` | UUID NULL | schedule ref (decoupled, §9) |
| `autonomous_run_id` | UUID NULL | index — run ref (decoupled, §9) |
| `occurred_at` | timestamptz NOT NULL | business event time |
| `payload` | JSONB NOT NULL | whitelisted builder output |
| `created_at` | timestamptz NOT NULL | server default now() |

Indexes (verified live): `idx_event_outbox_org_time (organization_id, created_at)`, `idx_event_outbox_project (project_id)`, `idx_event_outbox_run (autonomous_run_id)`, `idx_event_outbox_type (event_type)`. **Foreign keys: 0** (deliberate, §9). Downgrade drops the table — fully reversible. No unrelated table touched; chain linear.

## 4. New API Endpoints

**None.** Phase 4-A is backend-internal (§18 of the architecture report: "No API is required for the substrate phase — verifiable by tests + logs"). The only behavioural enrichment is the cancel route **writer** adding one outbox row in its existing transaction; the route's signature, RBAC, and response shape are untouched.

## 5. Transactional Outbox Architecture (as delivered)

```
fire (__tick_schedules → _fire_campaign_schedule)   tasks.py:545-563
  campaign_scheduler.fire(schedule) → FIRED + run_id
  ├─ project_repo.get_by_id(schedule.project_id)      (exists in the same session)
  ├─ outbox_service.record_campaign_run_started(...)   (single INSERT, flush)
  └─ session.commit()  ── run + schedule advance + started event, ATOMIC
      → rollback (any failure) = NO run, NO event (at-least-once, re-queued by next tick)

advance (_campaign_advance)                          tasks.py:765-786
  outcome = orchestrator.cycle(run_id)
  ├─ status COMPLETED → record_campaign_run_completed(...)
  ├─ status FAILED    → record_campaign_run_failed(...)
  └─ session.commit()  ── terminal state + terminal event, ATOMIC
      CANCELLED is NEVER emitted here — it belongs exclusively to the cancel endpoint,
      so one run can never emit cancelled twice.

cancel (POST /autonomous-runs/{id}/cancel)           autonomous.py
  resolve project → record_campaign_run_cancelled(...) in the SAME request transaction
```

Writer rules (enforced in code and tests):
- `OutboxService` (and repository) **never call `session.commit()`** — the caller owns the transaction, so the event lives and dies with the state change.
- `add()` = `session.add` + `flush` only — visible to the rest of the transaction, not visible to concurrent readers until commit.
- Emission failure **fails the whole transition** (mirrors the pre-existing coupling of `audit_logs`); no phantom events, no silently-missed events.
- `organization_id` is always derived from the live project row (`project → organization`), fixing the audit's known §1.2 gap from day one.

## 6. Event Envelope & Event Type Contract

`OutboxEvent` carries: `id` (`OutboxEventType` StrEnum value), `schema_version` (int, currently 1), `occurred_at` (UTC, injected clock), nullable `organization_id` / `project_id` / `schedule_id` / `autonomous_run_id`, and a JSONB `payload`. The `event_type` is the routing key for exactly four lifecycle events:

| event_type | source boundary | payload model |
|---|---|---|
| `campaign.run.started` | schedule fire (beat) | §8 started |
| `campaign.run.completed` | orchestrator cycle → COMPLETED | §8 terminal |
| `campaign.run.failed` | orchestrator cycle → FAILED | §8 terminal |
| `campaign.run.cancelled` | `POST /autonomous-runs/{id}/cancel` | §8 terminal |

The type namespaces as `campaign.run.*` per the mission's campaign/run identification criterion; a later phase can add `scan.*` etc. without a schema break.

## 7. Emission Points (exact)

- **Fire:** `_fire_campaign_schedule` records `started` only when `CampaignFireOutcome.FIRED` **and** `run_id is not None`, and only **before** `session.commit()` (tasks.py:547-563). A rejected/skipped fire emits nothing (observation of a non-transition). If emission raises, the entire fire transaction rolls back and the schedule stays due — exactly the pre-existing at-least-once contract.
- **Advance:** `_campaign_advance` records `completed`/`failed` strictly from the post-cycle terminal status, before `session.commit()` (tasks.py:765-786). Non-terminal outcomes emit nothing.
- **Cancel:** the request transaction records `cancelled` after the soft status flip (autonomous.py). No separate read/commit is introduced; the provider `get_outbox_service` (deps.py) injects the same request-scoped session.

Every row is persisted **inside** the transition's transaction via `async_session` shared with the domain write — the §8.2 "never in-memory-only" guarantee.

## 8. Payload Content Policy & Redaction

Builders live in `application/event_payloads.py` and enumerate their keys explicitly; **no ORM/domain object serialization** (`asdict`/`model_dump` of a full entity is forbidden — entities carry credentials, plugin configs, and internal wiring). Only stable ids + concise non-sensitive lifecycle metrics:

- `started`: `run_id`, `project_id`, `schedule_id`, `objective`, `max_actions`, `max_runtime_seconds`, `initiated_by`
- `terminal`: `run_id`, `project_id`, `status`, `current_cycle`, `actions_completed`, `error_message` — where `error_message` (FAILED only) is **truncated to 500 chars** (`_truncate`): a diagnostic snippet, never a traceback/log dump.

Unit + integration + live evidence assert **no** keys named `password`/`token`/`secret`/`hash` and **no** extra keys beyond the whitelist.

## 9. Observation Decoupling Decision (FK → plain indexed UUIDs)

The architecture report's §8.1 sketched UUID FKs for the *full Phase-4-B table* (which also carries delivery columns). Phase 4-A deliberately deviates from the draft: entity refs are **plain indexed UUID columns, not foreign keys**. Rationale, decided after a live-suite FK constraint error: an append-only observation trail must never block or be blocked by the lifecycle of its subjects (a project/run deletion must not cascade, restrict, or SET-NULL the history), and reference integrity is already guaranteed by the writer deriving `organization_id` from a live project row. Delivery columns themselves are deferred to Phase 4-B; referential coupling is not part of them. Integration test pins the behaviour: hard-deleting a project leaves the event row untouched with all refs intact.

## 10. Test Summary

- **Full backend suite:** collected **949** (baseline 938 + **11** new Phase-4A), **947 passed / 2 failed / 3 warnings**, stack up, `DATABASE_URL` → compose Postgres on localhost. The 2 failures are **pre-existing ordering/environmental flakes, both of which pass in isolation** (`test_plugin_container_runs_as_non_root` — live executor container; `test_bob_cannot_control_his_own_project_via_error_setup` — run-isolation API test that passes standalone) and are unrelated to Phase 4-A. My previously-failing `test_alice_can_cancel_own_run` now passes (it was failing only because of the now-removed hard FK, §9).
- **Phase 4-A deltas:** 6 unit + 5 Postgres integration — **11/11 green**, individually re-run to confirm.

## 11. Unit Tests (`tests/unit/test_m75_phase4a_outbox.py`, 6/6)

1. Started-event field mapping with a fixed injected clock/id (deterministic `event_id` + `occurred_at`).
2. Terminal events map to the exact event types (`completed`/`failed`/`cancelled`).
3. **Secret-leak negative:** an entity carrying sensitive fields (slots-safe `result_summary`) never appears in the payload — builder keys are a strict whitelist.
4. `error_message` truncated to 500 chars; None stays None.
5. `OutboxService` stores via `add` only — the fake has no read path, structurally proving "never reads, never commits".
6. Payload whitelist key-sets are exact (no drift).

## 12. Live Postgres Integration Tests (`tests/integration/test_m75_phase4a_outbox_integration.py`, 5/5)

Run against real Postgres (asyncpg, real table + indexes):

1. **Atomic commit:** run + started event written in one commit; a **second connection** (fresh transaction) sees the row only after commit — flush ≠ durability.
2. **Atomic rollback:** rollback after the domain write leaves zero outbox rows (no phantom events).
3. **Add flushes but never commits:** after `add`, the row is visible in-session, absent from the committed view.
4. **Emission failure → no event:** an outbox `add` that raises aborts the whole fire (no run, no event) — mirrors production rollback semantics.
5. **Observation decoupling:** hard-deleting the project leaves the event row intact with all refs preserved (no FK cascade/SET NULL; §9).

## 13. Live Stack Validation

After restarting `api`/`worker`/`beat` (Celery does not auto-reload; the volume-mounted source must be re-read), a due campaign schedule was seeded and **left to the real containers**:

- The **real beat** claimed and fired it → `campaign.run.started` row (schema 1, org/project/schedule refs correct, run_id matches payload, no secret keys, no extra keys).
- The **real worker** drove `campaign_advance` to `COMPLETED` (0 actions, no targets) → `campaign.run.completed` row (schema 1, refs correct, `status:"completed"`, run_id matches, clean payload).
- Both committed atomically through production code; the run never emitted a second event. Seed rows cleaned afterwards (`event_outbox` count 0, test orgs/projects/audits removed). Cancel path is exercised end-to-end by the passing API test `test_alice_can_cancel_own_run` (real router + repos + DB).

## 14. Lint / Type Check

- **ruff (repo config, line-length 100):** full tree **161 errors — down 1 from the 162 rediscovered baseline, zero in Phase 4-A files** (the -1 is the Phase-4A-broken isort block that the earlier `--fix` repaired). Remaining errors are the pre-existing set (27× UP042 in `value_objects.py` etc.), confined to untouched files.
- **mypy (strict + Pydantic plugin):** **38 errors / 11 files — identical to baseline**, zero Phase-4A errors. Grep for `outbox|event_payloads|tasks.py|autonomous.py|deps.py|event_outbox` in the mypy output: empty. Added `# type: ignore[attr-defined]` on the `object`-typed task params is the existing tasks.py style.
- `py_compile` clean on every touched file.

## 15. Full-Suite Gate

Collect 949 = 938 baseline + 6 unit + 5 integration. **947 passed / 2 failed / 3 warnings** (~2 min, stack up). The two failures pass standalone and are the documented environmental/order flakes (live-executor container check; run-isolation ordering flake). Every Phase-4A file, migration, and the outbox table are verified green (0-FK table, head `e2f3a4b5c6d7`).

## 16. Guarantee Statements (explicit)

- **ATOMIC EMISSION:** every committed campaign transition writes exactly its outbox row in the same transaction — commit ⇒ row exists, rollback ⇒ no row (proven on real PG, §12).
- **OBSERVATION-ONLY:** the outbox never gates, forks, or replaces execution; protected M7.4/7.5 contracts are untouched; delivery simply does not exist yet (Phase 4-B).
- **NO REFERENTIAL COUPLING:** plain indexed UUID refs — deleting a project/run cannot cascade, null, or block the history (§9, §12.5).
- **PAYLOAD SAFETY:** whitelisted builders only, 500-char error truncation, no secrets by construction and by test (§8, §11).
- **CANCELLED EMITTED EXACTLY ONCE:** owned solely by the cancel endpoint; the advance path can never double-emit it (§7).
- **Emission failure ⇒ transition failure:** a transition that cannot write its event behaves exactly as a transition that fails today (audit-coupled precedent) — never a silent miss. No async emission.

## 17. Commands Run (validation session)

```bash
docker compose -f infra/docker-compose.yml ps                                   # 8/8 up
docker compose -f infra/docker-compose.yml restart api worker beat             # reload volume-mounted source (Celery: no autoreload)
[postgres] SELECT column_name FROM information_schema.columns WHERE table_name='event_outbox'
[postgres] SELECT conname FROM pg_constraint WHERE conrelid='event_outbox'::regclass AND contype='f'   # 0 FKs (deliberate)
[postgres] SELECT indexname FROM pg_indexes WHERE tablename='event_outbox'     # pkey + 4 idx
$env:DATABASE_URL = "postgresql+asyncpg://specter:specter@localhost:5432/specter"
python -m alembic downgrade d1e2f3a4b5c6 && python -m alembic upgrade head     # e2f3a4b5c6d7 (rebuilt after §9)
python -m pytest tests/unit/test_m75_phase4a_outbox.py -q                      # 6 passed
python -m pytest tests/integration/test_m75_phase4a_outbox_integration.py -q   # 5 passed
python -m pytest -q                                                            # 947 passed / 2 self-passing flakes / 3 warnings
python -m ruff check .                                                         # 161 (baseline 162; zero in Phase-4A files)
python -m mypy app                                                             # 38/11 identical to baseline
python <live_validate_outbox.py>                                               # REAL beat+worker → started + completed rows verified
```

## 18. Security Checklist (11 items)

1. No secrets/credentials introduced or committed (diffs scanned: clean).
2. Payloads are whitelisted projections — never serialized entities (no tokens, plugin configs, auth headers, internal wiring).
3. `error_message` bounded to 500 chars — no tracebacks/log dumps leave the platform.
4. Writer is insert-only (`add` + flush); no read/replay surface in Phase 4-A.
5. No new API, no new network egress, no SSRF surface (delivery is Phase 4-B/C and does not exist here).
6. Clean Architecture preserved: `domain/` knows only the `OutboxEvent`/`OutboxEventType`/add-only Protocol; `OutboxService` (application) imports only domain interfaces; SQLAlchemy/Celery details stay in infrastructure; no framework import crosses layers.
7. Plugin allow-list and subprocess list-args rule untouched; nmap flag allow-list intact; file-writing/`--script` still forbidden.
8. M7.4 state machine, recovery, cancellation, project concurrency, Scope Guard, and campaign-fire guarantees untouched — emission is additive only.
9. No frontend change; No `.github/workflows/` expectations (none exist).
10. RFC 7807 error mapping unchanged; no new routable surface.
11. Migration is additive and reversible (downgrade drops `event_outbox`), no destructive effects on existing data.

## 19. Known Limitations / Risks & Baselines Preserved

- **Phase 4-A writes only:** events are stored, not delivered (no relay/retry/DLQ/webhooks — Phase 4-B/C). Consumers see them only once Phase 4-B lands.
- **No retention policy yet:** `event_outbox` grows until Phase 4-D adds retention parity with audit (alert on depth then).
- **Emission is coupled to the transition:** an event-write failure rolls the transition back (deliberate, matches the audit precedent). If this ever became a bottleneck, the Phase-4-B relay reads committed rows only — no future need to un-couple the write itself.
- **Celery does not auto-reload:** source is volume-mounted, but worker/beat needed a restart to pick up the new modules (documented in §17).
- **Baselines preserved:** tests 938 → 949 collected (11 new green; the 2 suite-level failures are pre-existing flakes that pass in isolation); ruff 162 → 161 (zero new); mypy 38/11 unchanged; protected modules (M7.4/M7.5.1 scheduler + Scope Guard + M7.5.3 fire semantics) byte-identical; public API unchanged.
- **DECISION (deviation):** event refs are plain indexed UUIDs, not the §8.1 FKs — observation decoupling (§9). This was the one live-found design correction of the phase, and it removed a real FK-enforcement break in the existing suite while strengthening the append-only semantics.
- **No commit/push/tag** has been made — per Phase-4A GIT RULES the working tree carries the Phase-4A delta uncommitted for review.