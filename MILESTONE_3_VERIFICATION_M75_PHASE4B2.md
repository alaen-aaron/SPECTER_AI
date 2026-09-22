# SPECTER_AI — M7.5 Phase 4-B2 Verification Package

**Outbox Relay Claim Loop & Gated Celery Beat Task (dry-run delivery)**
**Status:** COMPLETE — awaiting approval. All deliverables validated against a real Postgres instance (host-native, no backend compose service). **Nothing committed/pushed/tagged.**

---

## 1. Objective & Scope

Deliver M7.5 **Phase 4-B2 — Outbox relay & dispatcher claim loop** (§13-e of `MILESTONE_3_ARCHITECTURE_M75_PHASE4B.md`): the driver that Phase 4-B1's truck was built for. `run_outbox_relay` runs one pass of the §13 loop — **requeue stale leases, claim the next due batch and COMMIT the claim, deliver each event OUTSIDE any transaction, then settle every event in ONE final transaction** — and is wired to Celery Beat as `specter.outbox_relay`, gated behind `OUTBOX_RELAY_ENABLED`.

- **In scope:** framework-free `run_outbox_relay` (session factory injected, so the loop is unit-testable and reusable by a non-Celery supervisor), `OutboxRelayResult` summary, the Phase 4-B2 dry-run default delivery, the gated `"outbox-relay"` beat entry (30.0s) in `app.py`, and `OUTBOX_RELAY_ENABLED` / `OUTBOX_RELAY_BATCH_LIMIT` config flags.
- **Delivery model honoured (Phase 4-A §19 / 4-B1 §13 carry-over):** a row transitions `pending → delivering → (delivered | failed→pending | dead_letter)`; exactly-once is achieved at the **claim** horizon via `FOR UPDATE SKIP LOCKED` plus the lease watermark. The relay **commits the claim before any delivery** and **never delivers while holding a row lock**.
- **Phase 4-B2 settlement policy:** delivery success → `mark_delivered` (one final transaction, whole batch at once); delivery error → `mark_failed(error, retry_at=None)` — the **Phase 4-B2 terminal dead-letter** (the §20 backoff/DLQ/metrics machinery is **Phase 4-B3**, deliberately not here).
- **Explicitly NOT in scope (Phase 4-B3/B4):** retry/backoff policy, dead-letter alerting, metrics, real HTTP/webhook delivery, HMAC signing, SSRF protection. The outbox remains an observation substrate, never an alternate execution path.

**Hard constraint honoured:** no M7.4/M7.5.1/M7.5.3 protected contract touched, and 4-B1's emissions still stand — **4-B2 adds NO producer, NO schema, and NO migration**. The write path (`outbox_service`) never depends on `OUTBOX_RELAY_ENABLED`; gating lives in the beat schedule only, so an opted-out deployment never runs the relay.

## 2. Updated Repository Tree (delta)

```
backend/
  app/
    core/config.py                              (CHANGED) +OUTBOX_RELAY_ENABLED (default False),
                                                          +OUTBOX_RELAY_BATCH_LIMIT (default 50) @120-121
    infrastructure/
      celery_app/app.py                         (CHANGED) beat_schedule hoisted; `"outbox-relay"` entry
                                                          (specter.outbox_relay, 30.0s) compiled in ONLY
                                                          when OUTBOX_RELAY_ENABLED (@34-42)
      celery_app/tasks.py                       (CHANGED) +specter.outbox_relay task (@974-984) — safe
                                                          no-op when relay disabled — +_run_outbox_relay
                                                          (@987+, own async engine + session factory)
      event/__init__.py                         (NEW)     package marker
      event/relay.py                            (NEW)     run_outbox_relay claim loop (169 lines,
                                                          framework-free) + OutboxRelayResult + dry-run
  tests/
    integration/test_m75_phase4b2_outbox_relay_integration.py (NEW)  5 live Postgres tests
MILESTONE_3_VERIFICATION_M75_PHASE4B2.md        (NEW)     this package
```

**No migration for 4-B2:** 4-B1's `f3a4b5c6d7e8` is still head; the relay drives the exact columns/lease machinery 4-B1 delivered.

## 3. Database Schema Changes

**None.** `event_outbox` schema and index are exactly the Phase 4-B1 end-state (`f3a4b5c6d7e8`, additive columns). Alembic round-trip re-verified this session against live Postgres: `current` = `f3a4b5c6d7e8 (head)`. Chain stays linear `e2f3a4b5c6d7 → f3a4b5c6d7e8`.

## 4. New API Endpoints

**None.** 4-B2 is backend-internal substrate. No routable surface, no RFC-7807 mapping change, no auth surface introduced.

## 5. Relay Loop Architecture (as delivered)

```
run_outbox_relay(session_factory, now, limit, lease, deliver)      relay.py
  ── Transaction 1 (requeue + claim, COMMITTED):                    ── a crash here leaves the batch
     requeue_expired(now)           stale 'delivering' → 'pending'      claimed only until its lease
     claim_next_batch(now, limit, lease)                              expires; pre-commit work stays
        FOR UPDATE SKIP LOCKED, oldest-first FIFO                     'pending'
     → each row: 'delivering', attempts += 1, next_retry_at = now + lease
     → session.commit()                    ▼ the claim is durable
  ── Transaction-free delivery pass:                                 ── a slow/hanging transport never
     for each claimed event: deliver(event) OUTSIDE any transaction      blocks the claim commit or a
        success → nothing persisted here                                   peer worker's requeue_expired
        raise  → record {event_id: "ExcType: msg"}                        one bad event never aborts the pass
  ── Transaction 2 (settle the WHOLE batch in ONE commit):
     per event:
        no error → mark_delivered(event_id)               result.delivered += 1
        error    → mark_failed(event_id, error, retry_at=None)   ← 4-B2 terminal dead-letter
                 → result.dead_lettered += 1
        OutboxTransitionError → row was requeued + re-claimed elsewhere
            → skip, result.lost_race += 1  (other worker owns it)
     → session.commit()
  → logger.info("outbox_relay_completed", claimed/delivered/dead_lettered/lost_race)
```

Design decisions enforced in code and tests:
- **Claim is committed before any delivery** — the lease watermark is durable before the transport runs, so a dead worker's rows are recoverable via `requeue_expired` (§7-a/b model) and no event is delivered twice by two workers.
- **Delivery runs outside any transaction** — no row lock is held across the network-touching transport; a hanging delivery cannot stall the claim fleet or the settle commit.
- **Settle is one transaction for the whole batch** — `mark_delivered`/`mark_failed` are flush-only (§6 of 4-B1); the relay's single settle commit makes the batch outcome atomic.
- **Lost-race protection** — a row that was requeued and re-claimed elsewhere while we delivered raises `OutboxTransitionError`; the relay leaves that row untouched and counts it `lost_race` (actually correct, test e of 4-B1).
- **Framework-free core** — `relay.py` imports no Celery/SQLAlchemy-binding config; the beat task injects the session factory (mirroring production: `create_async_engine(str(settings.DATABASE_URL))` + `async_sessionmaker(expire_on_commit=False)`).

## 6. Beat Wiring & Runtime Gating

- `app.py` hoists the existing `_beat_schedule` (tick-schedules 30.0s, recover-autonomous-runs 60.0s) and appends `"outbox-relay"` (`specter.outbox_relay`, **30.0s**) **only when `settings.OUTBOX_RELAY_ENABLED` is true**.
- Default `OUTBOX_RELAY_ENABLED = False`; this repo's `.env` does not set it → **containers run the relay beat entry off** (intended). An operator opts a deployment in by setting the flag.
- `specter.outbox_relay` task (tasks.py:974-984) goes straight through to `_run_outbox_relay` when invoked — it does **not** consult the flag at runtime, because gating happens at the schedule level (the entry is only compiled in when the flag is on). A beat-off deployment never runs it, and an on-demand invocation is safe as a no-op on an empty outbox. `_run_outbox_relay` builds its own short-lived engine + session factory (local imports, no module-level state) and passes `OUTBOX_RELAY_BATCH_LIMIT` as `limit`.
- The task can **never raise** out of the loop: `run_outbox_relay` returns an `OutboxRelayResult`, and one bad event only dead-letters that row (§5).

## 7. Emission Points (exact)

**Unchanged from Phase 4-B1 / 4-A** — the three record boundaries (`_fire_campaign_schedule`, `_campaign_advance`, cancel request transaction) and their payload builders are byte-for-byte untouched. 4-B2 reads rows those emitters wrote; it adds no emission surface.

## 8. Payload Content Policy & Redaction

**Unchanged from Phase 4-A/4-B1.** The dry-run delivery logs only `event_id`, `event_type`, `schema_version`, `occurred_at` — never the payload (payloads can carry org/run references; the observation log must not leak them further). `last_error` on a dead-letter is a bounded diagnostic snippet (`"RuntimeError: boom"`), never a traceback or entity serialization.

## 9. Observation Decoupling Decision (carried forward)

The relay mutates only `event_outbox` rows via the 4-B1 repository; no FK, no subject-table touch. A project/run deletion can neither cascade nor strand relay state, and relay settlement never affects any subject.

## 10. Test Summary

- **4-B2 integration suite (`tests/integration/test_m75_phase4b2_outbox_relay_integration.py`, requires_postgres):** **5/5 green, `5 passed in 0.91s`** against live Postgres (host-native, migration at head). Unlike the 4-B1 suite, these tests **COMMIT their seeded rows before invoking the loop** and read post-pass state through fresh sessions — the relay opens and commits its own short-lived transactions, exactly like the beat task.
- **Full backend suite with 4-B2:** collected **972** = 967 baseline + **5 new 4-B2**: **953 passed / 10 failed / 9 skipped**. All 10 failures are the documented environment-sourced set (§15 of 4-B1), zero in 4-B2 files.
- **Apples-to-apples clean-tree control:** full suite on the same tree with the 4-B2 delta stashed → **967 collected / 947 passed / 11 failed / 9 skipped**. Every one of the 10 failures from the 4-B2 run reproduces identically on the clean tree; the 4-B2 run shows one *fewer* failure only because `test_smoke_create_autonomous_run` is order/state-flaky. **Net: +5 tests, all passing, zero new failures → no logic regressions.**

## 11. Unit Tests

**None new for 4-B2.** The relay's transactional semantics (committed claim vs settle, lost-race, dead-letter) are only evidenced against real row locks; the live suite covers them (§12). No pure-function seam warrants a fake-level test.

## 12. Live Postgres Integration Tests (`tests/integration/test_m75_phase4b2_outbox_relay_integration.py`, 5/5)

Run against real Postgres (asyncpg, real table + partial index). Each test is `@pytest.mark.asyncio`, marked `requires_postgres`, with an autouse `_clean_event_outbox` fixture wiping the table first (the relay COMMITS, so leftover rows would break exact-count assertions).

1. **Claim → deliver → settle delivered in one pass** — a due `pending` batch (2 rows) is claimed, dry-run delivered, and marked `delivered` with `delivered_at` set, `attempts==1`, watermark cleared; `OutboxRelayResult(claimed=2, delivered=2, dead_lettered=0, lost_race=0)`.
2. **Requeue stale lease, then claim and deliver** — an expired `delivering` row (attempts=2, lapsed `next_retry_at`) is requeued to `pending` (attempts kept), then claimed and settled; final `attempts==3` proves requeue(2) + claim-bump(3).
3. **Non-due rows untouched** — a `pending` row behind a future `available_after` and a `delivering` row with a **live** lease are neither requeued nor claimed; the pass is a no-op for them (`claimed==0`).
4. **Delivery error dead-letters only that event** — a raising transport (injected `deliver`) marks only the failing row `dead_letter` with `last_error` containing `"RuntimeError: boom"` and `next_retry_at` NULL; the healthy row is still delivered (`delivered==1, dead_lettered==1`).
5. **`limit` gates the batch** — 3 due rows with `limit=2`: exactly 2 delivered, surplus stays `pending` awaiting the next pass (total 3 rows, 2 delivered + 1 pending).

## 13. Live Stack Validation

Run host-natively against the compose Postgres (no `backend` compose service exists; the canonical flow is `uvicorn app.main:app` from `backend/`):

- `docker compose -f infra/docker-compose.yml ps` — Postgres reachable at `localhost:5432`.
- `python -m alembic current` — `f3a4b5c6d7e8 (head)`; no 4-B2 migration.
- `$env:DATABASE_URL = "postgresql+asyncpg://specter:specter@localhost:5432/specter"` then the 5-test live suite — **5 passed in 0.91s**.
- Beat gating verified by inspection: `.env` does not set `OUTBOX_RELAY_ENABLED`, so `_beat_schedule` carries no `"outbox-relay"` entry in the containers. **If a deployment opts in via the flag, `api worker beat` must be restarted** (Celery does not auto-reload; source is volume-mounted).

## 14. Lint / Type Check

- **ruff (repo config, line-length 100):** full tree **161 errors — identical to the Phase-4-A/4-B1 baseline, zero in 4-B2 files**. `relay.py` `# noqa: BLE001` (deliberate — one bad delivery never aborts the pass). `tests/integration/test_m75_phase4b2_outbox_relay_integration.py` clean (isort/first-party `app` respected).
- **mypy (strict + Pydantic plugin):** **38 errors / 11 files — identical to baseline**, zero 4-B2 errors (relay.py, tasks.py, app.py, config.py all clean). The integration test file passes `mypy --strict` with **0 errors**.
- **black:** scoped to the changed files and clean there.

## 15. Full-Suite Gate

Collect 972 = 967 baseline + 5 4-B2, `DATABASE_URL` unset:

- **With 4-B2:** **953 passed / 10 failed / 9 skipped / 972 collected**.
- **Clean-tree control (4-B2 delta stashed, same postgres, same session):** **947 passed / 11 failed / 9 skipped / 967 collected**.

Failure attribution (both runs): the 10 failures are the documented environment-sourced set — 4 × `httpx.ConnectError [WinError 10061]` no-API-on-8000 (`test_m74_api_smoke.py`, plus live modules), 2 × `socket.gaierror [Errno 11001]` host `postgres` (`test_m75_phase2_run_isolation_api.py` — needs `DATABASE_URL`), 4 × the pre-existing 4-A outbox `func.now()` DataError family in `tests/integration/test_m75_phase4a_outbox_integration.py`. The clean tree additionally fails `test_smoke_create_autonomous_run` (order/state flake while 4-B2 is present). **Zero failures in 4-B2 files in either run.** `test_smoke_create_autonomous_run` reproduces on the clean tree → not introduced by 4-B2.

## 16. Guarantee Statements (explicit)

- **DRY-RUN DELIVERY ONLY (4-B2):** the default transport logs and persists nothing; a real HTTP/webhook transport is Phase 4-B4. The injected `deliver` signature is the only delivery seam.
- **CLAIM COMMITS BEFORE DELIVERY:** the lease watermark is durable before the transport runs — a crash mid-pass leaves rows claimable by a peer only after lease expiry (never delivered twice).
- **NO DELIVERY UNDER A ROW LOCK:** the transport runs outside any transaction; the settle commit is a single final transaction (§13 flow).
- **FAILED EVENT ≠ FAILED PASS:** a raising transport dead-letters only that row (`mark_failed(retry_at=None)`, the 4-B2 terminal state); the rest of the batch still delivers.
- **LOST RACE IS SAFE:** a row settled elsewhere first raises `OutboxTransitionError`, is left untouched, and is counted `lost_race` — never double-delivered, never double-settled.
- **GATED, NON-RAISING TASK:** `OUTBOX_RELAY_ENABLED=False` (default) compiles the beat entry out — gating is at the schedule level, so an opted-out deployment never runs the relay, and an on-demand invocation is a safe no-op on an empty outbox; the write path never depends on the flag.
- **ZERO DELTA TO PROTECTED CODE:** no producer/schema/migration change; M7.4/M7.5.1/M7.5.3 and 4-B1 emissions byte-for-byte unchanged.
- **OBSERVATION-ONLY, STILL:** the outbox never gates, forks, or replaces execution.

## 17. Commands Run (validation session)

```bash
docker compose -f infra/docker-compose.yml ps                                   # postgres up
$env:DATABASE_URL = "postgresql+asyncpg://specter:specter@localhost:5432/specter"
python -m alembic current                                                       # f3a4b5c6d7e8 (head, no 4-B2 migration)
python -m pytest tests/integration/test_m75_phase4b2_outbox_relay_integration.py -q   # 5 passed in 0.91s
python -m pytest -q                                                            # 953 passed / 10 env-sourced failures / 9 skipped / 972 collected
python -m pytest -q (clean tree, 4-B2 delta stashed)                           # 947 passed / 11 failed / 9 skipped / 967 collected
python -m ruff check .                                                         # 161 (identical to 4-A/4-B1 baseline; zero in 4-B2 files)
python -m mypy app                                                             # 38/11 identical to baseline
python -m mypy --strict tests/integration/test_m75_phase4b2_outbox_relay_integration.py  # 0 errors
```

## 18. Security Checklist (11 items)

1. No secrets/credentials introduced or committed (diffs scanned: clean).
2. Payloads remain whitelisted projections; the dry-run relays metadata ids/types only — payload content never leaves `event_outbox`.
3. `last_error` bounded (500-char diagnostic snippet); no tracebacks/log dumps.
4. 4-B2 adds no read-back/replay API and no real egress — the only outbound seam is the injected, test-visible `deliver` callable (dry-run in 4-B2; HTTP + SSRF protection are 4-B4, still not present).
5. No new API, no new network egress, no SSRF surface.
6. Clean Architecture preserved: `relay.py` is framework-free (no Celery, no SQLAlchemy app imports); the beat task's engine/session wiring stays in `infrastructure/`.
7. Plugin allow-list and subprocess list-args rule untouched; nmap flag allow-list intact.
8. M7.4 state machine, recovery, cancellation, Scope Guard, campaign-fire guarantees, and every emission point untouched — the delta is a read-side driver only.
9. No frontend change; no `.github/workflows/` expectations (none exist).
10. RFC 7807 error mapping unchanged; no new routable surface.
11. No migration in 4-B2 (4-B1's is already applied and reversible); foreign keys remain 0 (observation decoupling, §9).

## 19. Known Limitations / Risks & Baselines Preserved

- **Dry-run delivery:** 4-B2 proves the loop; no real transport exists yet. The `deliver` seam is stable for 4-B4, but real delivery, retry/backoff, DLQ metrics, and alerts are **Phase 4-B3/B4** by explicit scope decision.
- **Terminal dead-letter in 4-B2:** a delivery error ends the row in `dead_letter` with no retry policy and no alerting — the §20 backoff machinery lands in 4-B3. `mark_failed(retry_at=None)` is the deliberate 4-B2 policy; a future `retry_at` call re-opens the retry path.
- **Lease expiry vs. long deliveries (carried from 4-B1):** a delivery longer than `DEFAULT_LEASE` (5 min) with no refresh lets `requeue_expired` revive the row mid-work (at-least-once at the consumer horizon). The watermark contract stands; a lease-refresh is a documented 4-B3 concern.
- **Celery does not auto-reload:** source is volume-mounted; the containers must be restarted (`docker compose … restart api worker beat`) before a flag-enabled relay takes effect — and with `.env` at defaults the beat entry is compiled out entirely (intended).
- **Baselines preserved:** tests 967 → 972 collected (5 new green; full-suite delta zero new failures, proven by the clean-tree control); ruff 161 unchanged; mypy 38/11 unchanged; protected modules byte-identical; public API unchanged; migration set unchanged.
- **No commit/push/tag** has been made — per Phase-4-B GIT RULES the working tree carries the 4-B2 delta uncommitted (3 modified + 3 new files) for review.