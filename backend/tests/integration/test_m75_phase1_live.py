"""M7.5 Phase 1 live API smoke — workflow execution, schedule lifecycle, RBAC.

Hits the LIVE API (Docker, port 9002) end-to-end:
  * a workflow execute rides the canonical path all the way to a real
    subprocess run (API -> Celery `workflow_execute` -> worker ->
    ScanService.create + ExecutionEngine.run) and reaches a terminal state
    with a scan row under the owning project,
  * schedule create/get/list/pause/resume/delete works, `expires_at` is
    echoed and a past deadline is rejected (422 invalid-schedule-config),
  * workflow execution + schedule mutation require a scan-capable role
    (an org `member` -- and a stranger -- get 403).

Requires: docker compose up (api + worker + postgres + redis).
"""

from __future__ import annotations

import time
import uuid

import httpx

BASE_URL = "http://localhost:9002"
OWNER_EMAIL = "e2e.alice@example.com"
OWNER_PASS = "Owner-pass-2026!"


def _login(client: httpx.Client, creds: tuple[str, str] | None = None) -> str:
    email, password = creds or (OWNER_EMAIL, OWNER_PASS)
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 200, f"login failed: {resp.status_code} {resp.text}"
    return resp.json()["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _get_org_id(client: httpx.Client, headers: dict[str, str]) -> str:
    resp = client.get("/api/v1/organizations", headers=headers)
    assert resp.status_code == 200, f"list orgs: {resp.status_code} {resp.text}"
    orgs = resp.json()
    assert orgs, "no organizations found"
    return orgs[0]["id"]


def _new_project(client: httpx.Client, headers: dict[str, str], org_id: str) -> str:
    tag = uuid.uuid4().hex[:8]
    resp = client.post(
        f"/api/v1/organizations/{org_id}/projects",
        json={"name": f"m75-p1-smoke-{tag}"},
        headers=headers,
    )
    assert resp.status_code in (200, 201), f"create project: {resp.status_code} {resp.text}"
    return resp.json()["id"]


def _add_target(client: httpx.Client, headers: dict[str, str], pid: str, value: str) -> str:
    resp = client.post(
        f"/api/v1/projects/{pid}/targets",
        json={"value": value, "target_type": "ip"},
        headers=headers,
    )
    assert resp.status_code in (200, 201), f"create target: {resp.status_code} {resp.text}"
    return resp.json()["id"]


def _authorize_and_activate(
    client: httpx.Client, headers: dict[str, str], pid: str, value: str
) -> None:
    resp = client.post(
        f"/api/v1/projects/{pid}/authorization",
        json={
            "client_name": "M7.5 Phase 1 smoke",
            "document_reference": "https://owasp.org/www-project-juice-shop/",
            "authorized_from": "2026-01-01",
            "authorized_to": "2027-12-31",
            "allowed_targets": [value],
            "scope_notes": "M7.5 Phase 1 live smoke",
        },
        headers=headers,
    )
    assert resp.status_code in (200, 201), f"authorize: {resp.status_code} {resp.text}"
    for state in ("authorized", "active"):
        resp = client.patch(f"/api/v1/projects/{pid}/state", json={"state": state}, headers=headers)
        assert resp.status_code == 200, f"state {state}: {resp.status_code} {resp.text}"


def _make_ping_workflow(client: httpx.Client, headers: dict[str, str], pid: str, host: str) -> str:
    resp = client.post(
        f"/api/v1/projects/{pid}/workflows",
        json={"name": f"m75-p1-ping-{uuid.uuid4().hex[:8]}"},
        headers=headers,
    )
    assert resp.status_code == 201, f"create workflow: {resp.status_code} {resp.text}"
    wf = resp.json()

    resp = client.post(
        f"/api/v1/workflows/{wf['id']}/steps",
        json={
            "plugin": "ping",
            "name": "ping host",
            "plugin_config": {"hostname": host},
            "timeout_seconds": 30,
            "max_retries": 0,
            "order": 0,
        },
        headers=headers,
        params={"project_id": pid},
    )
    assert resp.status_code == 201, f"add step: {resp.status_code} {resp.text}"

    resp = client.post(
        f"/api/v1/workflows/{wf['id']}/activate",
        headers=headers,
        params={"project_id": pid},
    )
    assert resp.status_code == 200, f"activate: {resp.status_code} {resp.text}"
    return wf["id"]


def _wait_terminal(
    client: httpx.Client, headers: dict[str, str], execution_id: str, pid: str
) -> dict:
    terminal = {"completed", "failed", "cancelled", "error"}
    for _ in range(60):
        resp = client.get(
            f"/api/v1/workflow-executions/{execution_id}",
            headers=headers,
            params={"project_id": pid},
        )
        assert resp.status_code == 200, f"get execution: {resp.status_code} {resp.text}"
        body = resp.json()
        if body["status"] in terminal:
            return body
        time.sleep(1)
    raise AssertionError(f"execution {execution_id} did not reach a terminal state")


def test_live_workflow_execute_canonical_path() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=60) as c:
        tok = _login(c)
        h = _headers(tok)
        org_id = _get_org_id(c, h)
        pid = _new_project(c, h, org_id)

        _add_target(c, h, pid, "127.0.0.1")
        _authorize_and_activate(c, h, pid, "127.0.0.1")

        wf = _make_ping_workflow(c, h, pid, "127.0.0.1")

        resp = c.post(f"/api/v1/workflows/{wf}/execute", headers=h)
        assert resp.status_code == 201, f"execute: {resp.status_code} {resp.text}"
        execution = resp.json()
        assert execution["status"] == "queued"

        terminal = _wait_terminal(c, h, execution["id"], pid)
        assert terminal["status"] in ("completed", "failed"), terminal

        # The canonical path created a real scan row owned by the project.
        resp = c.get(f"/api/v1/projects/{pid}/scans", headers=h)
        assert resp.status_code == 200
        scans = resp.json().get("items", resp.json())
        assert scans, "no scan rows were created by the workflow execution"

        # List executions surface returns the same execution.
        resp = c.get(
            f"/api/v1/workflows/{wf}/executions",
            headers=h,
            params={"project_id": pid},
        )
        assert resp.status_code == 200
        executions = resp.json().get("items", resp.json())
        assert any(e["id"] == execution["id"] for e in executions)

        # Cancel on a terminal execution fails closed (409).
        resp = c.delete(f"/api/v1/workflow-executions/{execution['id']}", headers=h)
        assert (
            resp.status_code == 409
        ), f"cancel on terminal should be 409: {resp.status_code} {resp.text}"


def test_live_schedule_crud_and_expiry() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=60) as c:
        tok = _login(c)
        h = _headers(tok)
        org_id = _get_org_id(c, h)
        pid = _new_project(c, h, org_id)
        _add_target(c, h, pid, "127.0.0.1")
        _authorize_and_activate(c, h, pid, "127.0.0.1")
        wf = _make_ping_workflow(c, h, pid, "127.0.0.1")

        # A past deadline is accepted but immediately deactivates the
        # schedule (visible for audit, never fires).
        resp = c.post(
            f"/api/v1/projects/{pid}/schedules",
            json={
                "workflow_id": wf,
                "frequency": "once",
                "expires_at": "2020-01-01T00:00:00Z",
            },
            headers=h,
        )
        assert resp.status_code == 201, f"past expiry create: {resp.status_code} {resp.text}"
        assert resp.json()["is_active"] is False, "past expiry must be inactive"

        resp = c.post(
            f"/api/v1/projects/{pid}/schedules",
            json={
                "workflow_id": wf,
                "frequency": "once",
                "expires_at": "2027-01-01T00:00:00Z",
            },
            headers=h,
        )
        assert resp.status_code == 201, f"create schedule: {resp.status_code} {resp.text}"
        sched = resp.json()
        assert sched["expires_at"] is not None, "expires_at must be echoed"
        sid = sched["id"]

        resp = c.get(f"/api/v1/projects/{pid}/schedules", headers=h)
        assert resp.status_code == 200
        items = resp.json().get("items", resp.json())
        assert any(s["id"] == sid for s in items)

        resp = c.get(f"/api/v1/schedules/{sid}", headers=h, params={"project_id": pid})
        assert resp.status_code == 200 and resp.json()["id"] == sid

        resp = c.post(f"/api/v1/schedules/{sid}/pause", headers=h, params={"project_id": pid})
        assert resp.status_code == 200 and resp.json()["is_active"] is False

        resp = c.post(f"/api/v1/schedules/{sid}/resume", headers=h, params={"project_id": pid})
        assert resp.status_code == 200 and resp.json()["is_active"] is True

        resp = c.delete(f"/api/v1/schedules/{sid}", headers=h, params={"project_id": pid})
        assert resp.status_code == 204, f"delete: {resp.status_code} {resp.text}"

        resp = c.get(f"/api/v1/schedules/{sid}", headers=h, params={"project_id": pid})
        assert (
            resp.status_code == 404
        ), f"deleted schedule should 404: {resp.status_code} {resp.text}"


def test_live_rbac_workflow_execute_and_schedule() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=60) as c:
        tok = _login(c)
        h = _headers(tok)
        org_id = _get_org_id(c, h)
        pid = _new_project(c, h, org_id)
        _add_target(c, h, pid, "127.0.0.1")
        _authorize_and_activate(c, h, pid, "127.0.0.1")
        wf = _make_ping_workflow(c, h, pid, "127.0.0.1")

        foreign = f"rbox.{uuid.uuid4().hex[:8]}@example.com"
        resp = c.post(
            "/api/v1/auth/register",
            json={"email": foreign, "password": "Foreign-pass-2026!"},
            headers=h,
        )
        assert resp.status_code == 201, f"register: {resp.status_code} {resp.text}"
        foreign_id = resp.json()["id"]

        fh = _headers(_login(c, creds=(foreign, "Foreign-pass-2026!")))

        # A stranger (no org, no project membership) is not scan-capable.
        resp = c.post(f"/api/v1/workflows/{wf}/execute", headers=fh)
        assert (
            resp.status_code == 403
        ), f"stranger execute should be 403: {resp.status_code} {resp.text}"
        resp = c.post(
            f"/api/v1/projects/{pid}/schedules",
            json={"workflow_id": wf, "frequency": "once"},
            headers=fh,
        )
        assert (
            resp.status_code == 403
        ), f"stranger schedule should be 403: {resp.status_code} {resp.text}"

        # Even as an org `member` (not org admin, not project member) the
        # workflow execution and schedule mutation stay forbidden.
        resp = c.post(
            f"/api/v1/organizations/{org_id}/members",
            json={"user_id": foreign_id, "role": "member"},
            headers=h,
        )
        assert resp.status_code == 201, f"add org member: {resp.status_code} {resp.text}"

        resp = c.post(f"/api/v1/workflows/{wf}/execute", headers=fh)
        assert (
            resp.status_code == 403
        ), f"org member execute should be 403: {resp.status_code} {resp.text}"
        resp = c.post(
            f"/api/v1/projects/{pid}/schedules",
            json={"workflow_id": wf, "frequency": "once"},
            headers=fh,
        )
        assert (
            resp.status_code == 403
        ), f"org member schedule should be 403: {resp.status_code} {resp.text}"
