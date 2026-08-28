"""M7.4 Phase 3 live-loop validation — observation / controlled re-plan / completion,
plus execution-time Scope Guard rejection.

Hits the LIVE API (Docker, port 9002) end-to-end with the real
PersistentObservationSource + after-commit Celery dispatch.
Requires: docker compose up (api + worker + beat + postgres + redis).
"""

from __future__ import annotations

import time
from datetime import date, timedelta

import httpx

BASE_URL = "http://localhost:9002"
OWNER_EMAIL = "e2e.alice@example.com"
OWNER_PASS = "Owner-pass-2026!"

ACTIVE_STATUSES = {"created", "planning", "executing", "observing", "awaiting_approval"}
LOOP_BOUND = 24


def _login(client: httpx.Client) -> str:
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": OWNER_EMAIL, "password": OWNER_PASS},
    )
    assert resp.status_code == 200, f"login failed: {resp.status_code} {resp.text}"
    return resp.json()["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _org_id(client: httpx.Client, headers: dict[str, str]) -> str:
    resp = client.get("/api/v1/organizations", headers=headers)
    assert resp.status_code == 200, f"list orgs: {resp.status_code} {resp.text}"
    orgs = resp.json()
    if isinstance(orgs, dict) and "items" in orgs:
        orgs = orgs["items"]
    assert orgs, "no organizations found"
    return orgs[0]["id"]


def _create_project(
    client: httpx.Client,
    headers: dict[str, str],
    name: str,
    allowed: list[str],
) -> str:
    org_id = _org_id(client, headers)
    resp = client.post(
        f"/api/v1/organizations/{org_id}/projects",
        json={"name": name, "description": "M7.4 Phase 3 live validation"},
        headers=headers,
    )
    assert resp.status_code in (200, 201), f"create project: {resp.status_code} {resp.text}"
    pid = resp.json()["id"]
    _authorize(client, headers, pid, allowed)
    for state in ("authorized", "active"):
        t = client.patch(
            f"/api/v1/projects/{pid}/state",
            json={"state": state},
            headers=headers,
        )
        assert t.status_code == 200, (
            f"transition -> {state}: {t.status_code} {t.text}"
        )
    return pid


def _authorize(
    client: httpx.Client, headers: dict[str, str], pid: str, values: list[str]
) -> None:
    today = date.today()
    resp = client.post(
        f"/api/v1/projects/{pid}/authorization",
        json={
            "client_name": "M7.4 live loop",
            "document_reference": "s3://e2e/phase3-scope.pdf",
            "authorized_from": (today - timedelta(days=1)).isoformat(),
            "authorized_to": (today + timedelta(days=30)).isoformat(),
            "allowed_targets": values,
            "scope_notes": "unit-test scope",
            "evidence_pointer": "s3://e2e/evidence",
        },
        headers=headers,
    )
    assert resp.status_code == 201, f"authorize: {resp.status_code} {resp.text}"


def _add_target(
    client: httpx.Client, headers: dict[str, str], pid: str, value: str
) -> str:
    resp = client.post(
        f"/api/v1/projects/{pid}/targets",
        json={"value": value, "target_type": "ip"},
        headers=headers,
    )
    assert resp.status_code == 201, f"add target {value}: {resp.status_code} {resp.text}"
    return resp.json()["id"]


def _get_run(client: httpx.Client, headers: dict[str, str], pid: str, run_id: str) -> dict:
    resp = client.get(
        f"/api/v1/autonomous-runs/{run_id}", params={"project_id": pid}, headers=headers
    )
    assert resp.status_code == 200, f"get run: {resp.status_code} {resp.text}"
    return resp.json()


def _drive_to_completion(
    client: httpx.Client, headers: dict[str, str], pid: str, run_id: str
) -> list[dict]:
    """Call cycle repeatedly until the run reaches a terminal state.

    Returns the list of cycle responses. Bounded by LOOP_BOUND — an
    unbounded/fruitless loop fails the test instead of hanging forever.
    """
    responses: list[dict] = []
    for _ in range(LOOP_BOUND):
        run = _get_run(client, headers, pid, run_id)
        status = run["status"]
        if status not in ACTIVE_STATUSES:
            break
        if status == "awaiting_approval":
            a = client.post(
                f"/api/v1/autonomous-runs/{run_id}/approve",
                params={"project_id": pid},
                headers=headers,
            )
            assert a.status_code == 200, f"approve: {a.status_code} {a.text}"
            run = a.json()
            if run["status"] not in ACTIVE_STATUSES:
                break
        try:
            resp = client.post(
                f"/api/v1/autonomous-runs/{run_id}/cycle",
                params={"project_id": pid},
                headers=headers,
                timeout=30,
            )
        except httpx.HTTPError:  # transient commit/wal lag
            time.sleep(3)
            continue
        assert resp.status_code == 200, (
            f"cycle: {resp.status_code} {resp.text}"
        )
        responses.append(resp.json())
        time.sleep(2.5)
    return responses


def test_phase3_bounded_loop_observes_and_completes() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=30) as c:
        tok = _login(c)
        h = _headers(tok)
        pid = _create_project(c, h, f"M7.4 Phase 3 live loop {int(time.time())}", ["10.0.0.1"])
        _add_target(c, h, pid, "10.0.0.1")

        create = c.post(
            f"/api/v1/projects/{pid}/autonomous-runs",
            json={
                "objective": "Probe whether 10.0.0.1 is reachable (ICMP liveness)",
                "max_actions": 4,
                "max_runtime_seconds": 900,
            },
            headers=h,
        )
        assert create.status_code in (200, 201), f"create run: {create.status_code} {create.text}"
        run_id = create.json()["id"]

        responses = _drive_to_completion(c, h, pid, run_id)

        final = _get_run(c, h, pid, run_id)
        assert final["status"] == "completed", f"expected completed: {final['status']}"
        assert len(responses) < LOOP_BOUND, "loop never terminated — real hang risk"
        assert final["current_cycle"] >= 1, "no cycle was ever driven"

        summary = final.get("result_summary") or {}
        # The OBSERVING gate must have fired at least once: a signature was
        # persisted and either new facts re-planned the run or a duplicate
        # halted it. Absence proves execution never reached observation.
        assert "observation_signature" in summary, (
            f"observation never fired; summary={summary}"
        )

        # Real work happened: at least one scan row exists for the project.
        scans = c.get(f"/api/v1/projects/{pid}/scans", headers=h)
        assert scans.status_code == 200, f"list scans: {scans.status_code} {scans.text}"
        assert scans.json(), "loop declared done without producing a single scan"


def test_phase3_out_of_scope_target_never_executes() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=30) as c:
        tok = _login(c)
        h = _headers(tok)
        pid = _create_project(c, h, f"M7.4 Phase 3 scope guard {int(time.time())}", ["10.0.0.2"])
        in_scope = _add_target(c, h, pid, "10.0.0.2")
        out_of_scope = _add_target(c, h, pid, "8.8.8.8")

        # Scope Guard preview: in-scope passes...
        ok = c.post(
            f"/api/v1/projects/{pid}/scope-check",
            json={"target_ids": [in_scope]},
            headers=h,
        )
        assert ok.status_code == 200, f"scope-check in-scope: {ok.status_code} {ok.text}"

        # ...the public host is rejected by the SAME validate_targets call
        # that execute_approved() -> ScanService.create() runs.
        bad = c.post(
            f"/api/v1/projects/{pid}/scope-check",
            json={"target_ids": [out_of_scope]},
            headers=h,
        )
        assert bad.status_code in (400, 403, 422), (
            f"scope-check out-of-scope should fail: {bad.status_code} {bad.text}"
        )

        # Execution-time re-validation: a direct scan against 8.8.8.8 must
        # be rejected — this is the exact guard the autonomous loop relies on.
        scan = c.post(
            f"/api/v1/projects/{pid}/scans",
            json={
                "plugin": "ping",
                "plugin_config": {"hostname": "8.8.8.8"},
                "target_ids": [out_of_scope],
            },
            headers=h,
        )
        assert scan.status_code in (400, 403, 422), (
            f"scan to 8.8.8.8 must be rejected: {scan.status_code} {scan.text}"
        )
        assert "out of scope" in scan.text.lower() or "scope" in scan.text.lower()

        # Control: the same scan shape against the authorized host queues up
        # (and its dispatch happens only after commit — §18 path).
        ok_scan = c.post(
            f"/api/v1/projects/{pid}/scans",
            json={
                "plugin": "ping",
                "plugin_config": {"hostname": "10.0.0.2"},
                "target_ids": [in_scope],
            },
            headers=h,
        )
        assert ok_scan.status_code in (200, 201), (
            f"scan to 10.0.0.2 should queue: {ok_scan.status_code} {ok_scan.text}"
        )
        scan_id = ok_scan.json()["id"]
        terminal = None
        for _ in range(10):  # let worker pick it up and finish
            s = c.get(f"/api/v1/scans/{scan_id}", headers=h)
            assert s.status_code == 200, s.text
            if s.json()["status"] in ("completed", "failed"):
                terminal = s.json()["status"]
                break
            time.sleep(2)
        # The scan reached the worker (dispatch-after-commit worked — it was
        # NOT dropped as "missing"). completed/failed are both fine: 10.0.0.2
        # normally IS unreachable, so 'failed' is the realistic live outcome.
        assert terminal is not None, f"scan never picked up: {s.json()['status']}"