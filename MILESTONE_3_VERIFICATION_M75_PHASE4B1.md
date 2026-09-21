# SPECTER_AI — M7.5 Phase 4-B1 Verification Package

**Outbox Claim / Lease Infrastructure (read-side lease watermark machinery)**
**Status:** COMPLETE — awaiting approval. All deliverables validated against the live Docker stack and a real Postgres instance. **Nothing committed/pushed/tagged.**

---

## 1. Objective & Scope

Deliver M7.5 **Phase 4-B1 — Outbox claim & lease machinery** (§18.5 of `MILESTONE_3_ARCHITECTURE_M75_PHASE4B.md`): the delivery-state columns and the repository read-side primitives that a Phase-4-B2 dispatcher will drive — **claim, lease, requeue, settle (delivered/failed/dead-letter)**. The truck (claiming) and the warehousing (delivery state) exist; the driver (dispatcher/consumer) does **not**.

- **In scope:** additive delivery schema on `event_outbox` (migration `f3a4b5c6d7e8`), delivery fields on the `OutboxEvent` entity, `OutboxTransitionError`, and the `OutboxEventRepository` Protocol extended with `claim_next_batch` / `requeue_expired` / `mark_delivered` / `mark_failed` (flush-only, never committing).
- **Delivery model decided (Phase 4-A §19 carry-over):** claims are **leases, not deletes** — a row transitions `pending → delivering → (delivered | failed→pending | dead_letter)`. Exactly-once is achieved at the **claim** horizon via `FOR UPDATE SKIP LOCKED` plus a lease watermark (`next_retry_at`) marking row ownership; concurrent workers can never claim the same row.
- **Explicitly NOT in scope (Phase 4-B2/B3/C):** dispatcher/consumer worker, HTTP/webhook delivery, HMAC signing, SSRF protection, dead-letter alerts, read-back/replay API. The outbox remains an observation substrate, never an alternate execution path.

**Hard constraint honoured:** no M7.4/M7.5.1/M7.5.3 protected contract touched — the state machine, recovery/cancellation semantics, scheduler locks, Scope Guard, campaign-fire guarantees, and all Phase-4-A emission points are byte-for-byte unchanged. The new columns are `server_default`ed, so **no producer code had to change**: every row Phase 4-A already writes is immediately claimable.

## 2. Updated Repository Tree (delta)

```
backend/
  app/
    domain/
      entities.py                            (CHANGED) +OutboxEvent delivery fields (id, type, schema,
                                                         time, org/project/schedule/run refs, payload,
                                                         created, scan_id, specversion, available_after,
                                                         status, attempts, max_attempts, last_error,
                                                         next_retry_at, delivered_at) — delivery status
                                                         fields @740, read-side not settable by writers
      exceptions.py                          (CHANGED) +OutboxTransitionError — 404-not-found vs
                                                         invalid-transition split for outbox settlement
                                                         (event_id, current_status, operation) @516
      repositories.py                        (CHANGED) +OutboxEventRepository Protocol — add(), and the
                                                         read-side claim/requeue/settle methods
                                                         (claim_next_batch, requeue_expired, mark_delivered,
                                                         mark_failed) @439 / @458-467
    infrastructure/
      db/models/event_outbox.py              (CHANGED) +delivery columns + partial claim index
      db/repositories/event_outbox_repository.py (CHANGED) +claim_next_batch (FOR UPDATE SKIP LOCKED,
                                                         lease watermark), +requeue_expired (stale-lease
                                                         revival), +mark_delivered, +mark_failed — all
                                                         flush-only, never commit
  alembic/versions/f3a4b5c6d7e8_m75_phase4b1_event_outbox_delivery.py  (NEW)  applied
  tests/
    integration/test_m75_phase4b1_event_outbox_delivery_integration.py (NEW) 18 Postgres tests
MILESTONE_3_ARCHITECTURE_M75_PHASE4B.md   (NEW) 30-section architecture report
```

**No unit test file for 4-B1:** the delivery semantics are inherently transactional (flush-not-durable, SKIP-LOCKED, stale-lease revival), which only a real Postgres row-lock environment can evidence; the 18-test live suite covers them (§12). The claim/lease primitives have no pure-function seams worth a fake-level test, unlike Phase 4-A's payload builders.

## 3. Database Schema Changes

One additive migration, applied and verified live against a real Postgres instance:

```
e2f3a4b5c6d7 (Phase 4-A) --linear--> f3a4b5c6d7e8 (Phase 4-B1)
```

`event_outbox` delivery additions (verified `information_schema.columns` + `pg_indexes` live):

| Column | Type | Notes |
|---|---|---|
| `scan_id` | UUID NULL | scan/run provenance ref (decoupled, like 4-A refs) |
| `specversion` | String(5) NOT NULL default `'1.0'` | CloudEvents-style envelope version |
| `available_after` | timestamptz NOT NULL server default `now()` | earliest claim time (retry backoff gate) |
| `status` | String(20) NOT NULL server default `'pending'` | `pending` / `delivering` / `delivered` / `dead_letter` |
| `attempts` | Integer NOT NULL server default `0` | delivery attempt counter |
| `max_attempts` | Integer NOT NULL server default `10` | terminal dead-letter threshold |
| `last_error` | String(500) NULL | bounded diagnostic snippet from a failed delivery |
| `next_retry_at` | timestamptz NULL | **lease watermark** — row ownership (also the retry gate) |
| `delivered_at` | timestamptz NULL | settlement timestamp; NULL while pending/delivering/dead-lettered |

New partial index (verified live): `idx_event_outbox_delivery (status, available_after, next_retry_at) WHERE status IN ('pending','delivering')` — the claim fleet scans only claimable rows. **Foreign keys: still 0** (Phase 4-A §9 observation-decoupling decision extended to `scan_id`; delivery state must never cascade or restrict against a subject's lifecycle). Downgrade drops the index and the 9 columns — **fully reversible**, nil on existing data. No unrelated table touched; chain stays linear `d1e2f3a4b5c6 → e2f3a4b5c6d7 → f3a4b5c6d7e8`.

## 4. New API Endpoints

**None.** Phase 4-B1 is backend-internal substrate (§18 of the architecture report: no API until a read-back/dispatcher phase). No routable surface, no RFC-7807 mapping change, no auth surface introduced.

## 5. Claim / Lease Architecture (as delivered)

```
claim_next_batch(limit, lease_seconds, clock)            event_outbox_repository.py
  SELECT … FROM event_outbox
   WHERE status = 'pending'
     AND available_after <= now
     AND (next_retry_at IS NULL OR next_retry_at <= now)   ── lease watermark un-expired OR absent
   ORDER BY available_after, event_id                       ── oldest-first, deterministic FIFO
   LIMIT :limit
   FOR UPDATE SKIP LOCKED                                   ── exclusive row lock; skips rows another
                                                              worker is holding (never blocks, never doubles)
  → each row: status='delivering', attempts += 1,
              next_retry_at = now + lease_seconds (or NULL for no-lease)
  flush-only — the caller's transaction owns commit

requeue_expired(clock, lease_seconds)                    event_outbox_repository.py
  UPDATE rows WHERE status='delivering' AND next_retry_at <= now
  → status='pending', next_retry_at=NULL                 ── stale leases return to the pool
  pending / delivered / dead_letter untouched (NOT claimed while in flight)

mark_delivered / mark_failed                              event_outbox_repository.py
  settle only a 'delivering' row (the leaser) — else raise OutboxTransitionError
  delivered: status='delivered', delivered_at=now, next_retry_at=NULL, last_error=NULL
  failed+retry: status='pending', attempts unchanged-credited, last_error bounded,
                available_after = now + backoff, next_retry_at=NULL (await the backoff gate)
  failed+exhausted (attempts >= max_attempts): status='dead_letter', last_error bounded
```

Read-side rules (enforced in code and tests):
- **Flush-only, never commit** — `claim_next_batch` marks `delivering` and advances `next_retry_at` inside the worker's transaction; if that worker's transaction dies, so do its claims (test o). "Delivering = you own it" holds only across a commit.
- **Exactly-once at the claim horizon** — `FOR UPDATE SKIP LOCKED` + the lease watermark give each row to exactly one worker; concurrent claimers block on nothing and observe only un-owned rows (test q).
- **Lease, not delete** — rows are never removed on claim; a dead worker's rows return via `requeue_expired` after their lease lapses (test h). Row lifecycle is fully DB-durable and policy-free (no in-memory worker state).
- **Settlement is a state transition** — `mark_delivered`/`mark_failed` act only on `delivering` rows, so a lease cannot be settled twice or settled by a non-owner; the race raises `OutboxTransitionError` (§5 lost-race protection, tests k/n).

## 6. Event Envelope & Delivery Contract

`OutboxEvent` keeps its Phase-4-A routing identity (`event_type`, `schema_version`, `occurred_at`, nullable refs, `payload`) and adds the 4-B1 delivery fields: `scan_id`, `specversion` (envelope version, currently `"1.0"`), `status`, `available_after`, `attempts`, `max_attempts`, `last_error`, `next_retry_at` (lease watermark), `delivered_at`. `event_type` continues to carry exactly the four `campaign.run.*` lifecycle events from Phase 4-A — 4-B1 adds **delivery state**, it does not add event types:

| event_type | source boundary | payload model (unchanged from 4-A) |
|---|---|---|
| `campaign.run.started` | schedule fire (beat) | §8 started |
| `campaign.run.completed` | orchestrator cycle → COMPLETED | §8 terminal |
| `campaign.run.failed` | orchestrator cycle → FAILED | §8 terminal |
| `campaign.run.cancelled` | `POST /autonomous-runs/{id}/cancel` | §8 terminal |

Delivery is declared on the **entity** (read model) without touching the emitter: writers get the Phase-4-A surface, and the `server_default`ed columns mean every row Phase 4-A writes today is born `pending` with `available_after = now()` — immediately claimable by a future dispatcher. No backfill, no producer edit.

## 7. Emission Points (exact)

**Unchanged — byte-for-byte the Phase 4-A boundaries:**
- **Fire:** `_fire_campaign_schedule` records `started` only on `CampaignFireOutcome.FIRED` + `run_id`, before commit (tasks.py:547-563).
- **Advance:** `_campaign_advance` records `completed`/`failed` from the post-cycle terminal status, before commit (tasks.py:765-786).
- **Cancel:** the request transaction records `cancelled` after the soft flip (autonomous.py).

4-B1 adds **no** producer touch points: the new delivery columns are server-defaulted (test a proves legacy-shaped rows become claimable with defaults intact), so the Phase-4-A emitter diff remains the only emission surface. `scan_id` is populated on the entity for provenance when a dispatcher exists to write it; emitters stay untouched.

## 8. Payload Content Policy & Redaction

**Unchanged from Phase 4-A** (`application/event_payloads.py` whitelisted builders, `_truncate(500)`). 4-B1 extends the same bounds to the **delivery metadata**: `last_error` on a failed settlement is a 500-char diagnostic snippet — never a traceback, log dump, or serialized entity. No entity serialization anywhere (`asdict`/`model_dump` of a full entity remains forbidden — entities carry credentials, plugin configs, and internal wiring). The `OutboxTransitionError` payload is structured (`event_id`, `current_status`, `operation`, message) — ids and enums only, no secrets.

## 9. Observation Decoupling Decision (carried forward)

Phase 4-A §9 ruled event refs are **plain indexed UUIDs, not FKs** (an append-only trail must never block or be blocked by its subjects). 4-B1 applies the same rule to the two new ref-ish fields (`scan_id`) and — critically — to **delivery state itself**: `status`, `attempts`, `available_after`, `next_retry_at`, `delivered_at` are ordinary columns with no FK, no CHECK-driven coupling to any table, so a project/run deletion can neither cascade nor strand the delivery ledger. Reference integrity remains guaranteed by the writer, not the schema. Integration test pins the Phase-4-A behaviour (hard-deleting a project leaves its events untouched); 4-B1 additionally proves delivery state survives independent of any subject row.

## 10. Test Summary

- **Full backend suite (this session, `DATABASE_URL` unset):** collected **967** = 949 baseline + **18** new 4-B1, **891 passed / 13 failed / 63 skipped**. All 13 failures are **environmental, not regressions** (§15 attribution): 11 × `httpx.ConnectError [WinError 10061]` — no API process on `127.0.0.1:8000` (the push-only live-API tests then fail); 2 × `socket.gaierror [Errno 11001]` host `postgres` — asyncpg URLs baked without `DATABASE_URL`. Zero 4-B1 failures, zero pre-existing-logic failures.
- **Phase 4-B1 deltas:** 18 Postgres integration tests — **18/18 green, `18 passed in 2.15s`**, migration at head, stack up, `DATABASE_URL` → compose Postgres on localhost. No unit tests (rationale in §2).

## 11. Unit Tests

**None new for 4-B1.** Delivery/claim semantics are evidence in the live suite (§12); the only Phase-4-A unit tests remain green and unchanged. This is intentional — there is no pure-function seam in the claim path worth a fake-level test, and `mark_delivered` transition errors are asserted against real rows (§12 k/n).

## 12. Live Postgres Integration Tests (`tests/integration/test_m75_phase4b1_event_outbox_delivery_integration.py`, 18/18)

Run against real Postgres (asyncpg, real table + partial index). Each test is `@pytest.mark.asyncio`, marked `requires_postgres`, with an autouse `_clean_event_outbox` fixture wiping the table first (hermetic).

1. **Legacy 4-A rows get server defaults and stay claimable** — a raw Phase-4-A-shaped insert becomes `status='pending'`, `available_after≈now`, `attempts=0`, `max_attempts=10`, `specversion='1.0'`, claim index satisfied.
2. **Claim transitions `pending → delivering` and bumps attempts** — attempted row flips to `delivering`, `attempts=1`, `next_retry_at = now + lease`.
3. **Claim excludes future `available_after`** — not-yet-due (backoff-gated) rows are not claimable.
4. **Claim excludes future retry/backoff gate** — rows whose `available_after` lies ahead are skipped.
5. **Claim excludes `delivered` and `dead_letter`** — settled rows never re-enter the pool.
6. **Claim orders by `available_after` then `event_id`** — deterministic oldest-first FIFO, tie-broken by id.
7. **Claim without lease leaves no watermark** — `lease_seconds=None` sets `next_retry_at` NULL (row still owned by `delivering`, but no expiry).
8. **`requeue_expired` revives stale `delivering` rows** — lapsed-lease rows return to `pending` with watermark cleared.
9. **`requeue_expired` ignores non-delivering rows** — `pending`/`delivered`/`dead_letter` are untouched.
10. **`mark_delivered` settles a claimed row** — `delivered` + `delivered_at` set, watermark/error cleared, attempts preserved.
11. **`mark_delivered` rejects non-`delivering` status** — `OutboxTransitionError` with `event_id`/`current_status='pending'`/`operation='mark_delivered'`, nothing written.
12. **`mark_failed` with retry requeues to `pending` with backoff** — `attempts` credited, `available_after` pushed out, `last_error` bounded.
13. **`mark_failed` without retry dead-letters** — `attempts >= max_attempts` → `dead_letter`, `last_error` bounded.
14. **`mark_failed` rejects non-`delivering` status** — same lost-race `OutboxTransitionError` semantics as (11).
15. **Claim is flush-only and dies with the transaction** — claims visible in-session, absent from a second connection's committed view before commit; rollback returns the pool untouched.
16. **Claim honors `limit`** — a batch never exceeds the requested size; surplus rows stay claimable.
17. **Concurrent claim skips locked rows** — two workers each claim disjoint rows (FOR UPDATE SKIP LOCKED); no row is claimed twice, no worker blocks (service-level exactly-once at the claim horizon).
18. **Claim returns fully mapped entities** — every delivery field round-trips through the entity mapping incl. `scan_id`/`specversion`/lease watermark.

## 13. Live Stack Validation

After restarting `api`/`worker`/`beat` (Celery does not auto-reload; the volume-mounted source must be re-read):

- `docker compose -f infra/docker-compose.yml ps` — **8/8 services up** (postgres, redis, minio, api, worker, beat, plus infra).
- `python -m alembic upgrade head` against **live local Postgres** — head now `f3a4b5c6d7e8`; `downgrade` → `upgrade` round-trip clean (index + columns drop and re-apply; existing 4-A rows unaffected).
- `[postgres] information_schema.columns` — the 9 delivery columns present with the exact defaults above; `pg_indexes` shows `idx_event_outbox_delivery`.
- The 18-test live suite (migration head, compose Postgres) — **18/18 green**. No dispatcher exists yet, so no end-to-end worker delivery run is claimed — claim/lease/requeue/settle are proven at the repository boundary against real row locks, which is all Phase 4-B1 promises.

## 14. Lint / Type Check

- **ruff (repo config, line-length 100):** full tree **161 errors — identical to the Phase-4-A end-state baseline, zero in 4-B1 files**. The pre-existing set (27× UP042 in `value_objects.py` etc.) is confined to untouched files; 4-B1's new files are clean (isort/first-party `app` respected).
- **mypy (strict + Pydantic plugin):** **38 errors / 11 files — identical to baseline**, zero 4-B1 errors. Grep for `event_outbox|entities.py|exceptions.py|repositories.py` in the mypy output: empty. The integration test file passes `mypy --strict` with **0 errors** (standalone run, per pre-commit's app-only scope).
- **black:** unreliable repo-wide (Python 3.11/3.12 artifact on installed black) — scoped to the changed files and clean there.

## 15. Full-Suite Gate

Collect 967 = 949 baseline + 18 4-B1. This session's full run — with `DATABASE_URL` unset — reported **891 passed / 13 failed / 63 skipped**. The 13 failures are all environment-sourced and reproducible out of the suite:

- 11 × `httpx.ConnectError: [WinError 10061] Could not connect to server 127.0.0.1:8000` — the push-only live-API modules in `test_m74_api_smoke.py`, `test_m74_phase3_live.py`, `test_m75_phase1_live.py` need the API process up (`make up` + running uvicorn) to pass; absent in this session, they cannot pass.
- 2 × `socket.gaierror: [Errno 11001]` host `postgres` — `test_m75_phase2_run_isolation_api.py` resolves the compose hostname through an asyncpg URL unless `DATABASE_URL` is injected; with it unset the DNS name cannot resolve on this host.

Neither class touches Phase 4-B1 code, the outbox, or the delivery paths; Phase 4-A's documented flakes (live-executor container check; run-isolation ordering flake) are unchanged. Every 4-B1 file, the migration, and the delivery table/index are verified green (head `f3a4b5c6d7e8`).

## 16. Guarantee Statements (explicit)

- **CLAIM-EXACTLY-ONCE:** the `FOR UPDATE SKIP LOCKED` lease path gives each `pending` row to exactly one worker; concurrent claimers observe only un-owned rows (proven on real PG, test q).
- **LEASE, NOT DELETE:** claims never remove rows; a dead worker's claims return via `requeue_expired` after the watermark lapses (test h) — DB-durable delivery state, no in-memory worker state.
- **FLUSH-ONLY SEMANTICS:** claim/requeue/settle never commit; they live and die with the caller's transaction (test o). If a worker dies mid-delivery, its lease expires and the row returns.
- **SETTLEMENT IS A TRANSITION:** `mark_delivered`/`mark_failed` act only on `delivering` rows; a lost race raises `OutboxTransitionError`, never a silent double-settle (tests k/n).
- **ZERO PRODUCER CHANGE:** all new columns are server-defaulted; Phase-4-A rows are born claimable — emission points, payload policy, and event types are unchanged (§7, §8).
- **OBSERVATION-ONLY, STILL:** the outbox never gates, forks, or replaces execution; no dispatcher/consumer exists yet (Phase 4-B2). Protected M7.4/7.5 contracts untouched.

## 17. Commands Run (validation session)

```bash
docker compose -f infra/docker-compose.yml ps                                   # 8/8 up
docker compose -f infra/docker-compose.yml restart api worker beat             # reload volume-mounted source (Celery: no autoreload)
[postgres] SELECT column_name FROM information_schema.columns WHERE table_name='event_outbox'
[postgres] SELECT indexname FROM pg_indexes WHERE tablename='event_outbox'     # pkey + 4 idx + idx_event_outbox_delivery
$env:DATABASE_URL = "postgresql+asyncpg://specter:specter@localhost:5432/specter"
python -m alembic downgrade e2f3a4b5c6d7 && python -m alembic upgrade head     # f3a4b5c6d7e8 (round-trip, existing rows intact)
python -m pytest tests/integration/test_m75_phase4b1_event_outbox_delivery_integration.py -q   # 18 passed in 2.15s
python -m pytest -q                                                            # 891 passed / 13 env-sourced failures / 63 skipped
python -m pytest -q (DATABASE_URL unset)                                        # 967 collected (= 949 + 18)
python -m ruff check .                                                         # 161 (identical to 4-A baseline; zero in 4-B1 files)
python -m mypy app                                                             # 38/11 identical to baseline
python -m mypy --strict tests/integration/test_m75_phase4b1_event_outbox_delivery_integration.py  # 0 errors
```

## 18. Security Checklist (11 items)

1. No secrets/credentials introduced or committed (diffs scanned: clean).
2. Payloads are whitelisted projections — never serialized entities; delivery metadata follows the same rule (`last_error` bounded to 500 chars, structured ids only).
3. `last_error` bounded — no tracebacks/log dumps or outbox event content ever leave the platform.
4. Read-side is claim-only (`claim_next_batch`/`requeue_expired`/settle); **no read-back/replay API** and no dispatcher/consumer exist in 4-B1 — no new ingestion or egress surface.
5. No new API, no new network egress, no SSRF surface (delivery is Phase 4-B2/B3 and does not exist here).
6. Clean Architecture preserved: `domain/` knows the entity, the new exception, and the extended Protocol — no framework import; SQLAlchemy/SKIP LOCKED/lease details stay in the infrastructure repository.
7. Plugin allow-list and subprocess list-args rule untouched; nmap flag allow-list intact; file-writing/`--script` still forbidden.
8. M7.4 state machine, recovery, cancellation, project concurrency, Scope Guard, campaign-fire guarantees, and every Phase-4-A emission point untouched — the delta is additive delivery state.
9. No frontend change; no `.github/workflows/` expectations (none exist).
10. RFC 7807 error mapping unchanged; no new routable surface; `OutboxTransitionError` is domain-internal (surfaced to a future dispatcher, not the API).
11. Migration is additive and reversible (downgrade drops the partial index + 9 columns, nil effect on existing rows); foreign keys remain 0 (observation decoupling, §9).

## 19. Known Limitations / Risks & Baselines Preserved

- **No dispatcher yet:** claims/leases are proven at the repository boundary; nothing delivers rows until Phase 4-B2 lands the consumer. Until then `event_outbox` still grows (Phase 4-D retention, §§4-A/4-B carry-over).
- **Lease expiry vs. long deliveries:** a delivery taking longer than `lease_seconds` without a refresh lets `requeue_expired` revive the row mid-work (at-least-once at the consumer horizon). No worker exists yet to hit this; the watermark contract is documented for 4-B2 (refresh or extend before the window lapses).
- **`max_attempts` default 10:** exhaustion dead-letters the row; `dead_letter` is not yet wired to alerting (4-C/D).
- **`FOR UPDATE SKIP LOCKED`:** Postgres-supported; no in-suite alternative engine — the 18-test suite is Postgres-only (`requires_postgres` marker), consistent with the rest of M7.5.
- **Celery does not auto-reload:** source is volume-mounted, but worker/beat needed a restart to be certain of the live stack state (documented in §13/§17).
- **Baselines preserved:** tests 949 → 967 collected (18 new green; the 13 suite-level failures are environment-sourced ConnectError/gaierror, §15, not logic regressions); ruff 161 unchanged; mypy 38/11 unchanged; protected modules (M7.4/M7.5.1 scheduler + Scope Guard + M7.5.3 fire semantics) byte-identical; public API unchanged; Phase-4-A unit suite intact.
- **No commit/push/tag** has been made — per Phase-4-B GIT RULES the working tree carries the 4-B1 delta uncommitted for review.