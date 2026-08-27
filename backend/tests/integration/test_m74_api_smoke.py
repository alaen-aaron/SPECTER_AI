"""M7.4 API smoke tests — create / list / get / cancel autonomous runs.

Hits the LIVE API (Docker, port 9002) end-to-end.
Requires: docker compose up (api + postgres + redis).
"""

from __future__ import annotations

import httpx

BASE_URL = "http://localhost:9002"
OWNER_EMAIL = "e2e.alice@example.com"
OWNER_PASS = "Owner-pass-2026!"


def _login(client: httpx.Client) -> str:
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": OWNER_EMAIL, "password": OWNER_PASS},
    )
    assert resp.status_code == 200, f"login failed: {resp.status_code} {resp.text}"
    return resp.json()["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _get_project_id(client: httpx.Client, headers: dict[str, str]) -> str:
    resp = client.get("/api/v1/organizations", headers=headers)
    assert resp.status_code == 200, f"list orgs: {resp.status_code} {resp.text}"
    orgs = resp.json()
    if isinstance(orgs, dict) and "items" in orgs:
        orgs = orgs["items"]
    assert orgs, "no organizations found"
    org_id = orgs[0]["id"]

    resp = client.get(f"/api/v1/organizations/{org_id}/projects", headers=headers)
    assert resp.status_code == 200, f"list projects: {resp.status_code} {resp.text}"
    projects = resp.json()
    if isinstance(projects, dict) and "items" in projects:
        projects = projects["items"]
    assert projects, "no projects found"
    return projects[0]["id"]


def _cancel_all_runs(client: httpx.Client, headers: dict[str, str], pid: str) -> None:
    """Cancel all active runs for the project to ensure a clean slate."""
    resp = client.get(f"/api/v1/projects/{pid}/autonomous-runs", headers=headers)
    if resp.status_code != 200:
        return
    body = resp.json()
    items = body.get("items", body) if isinstance(body, dict) else body
    if not isinstance(items, list):
        return
    for run in items:
        if run.get("status") not in ("completed", "failed", "cancelled"):
            client.post(
                f"/api/v1/autonomous-runs/{run['id']}/cancel",
                params={"project_id": pid},
                headers=headers,
            )


def test_smoke_create_autonomous_run() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=30) as c:
        tok = _login(c)
        h = _headers(tok)
        pid = _get_project_id(c, h)
        _cancel_all_runs(c, h, pid)

        resp = c.post(
            f"/api/v1/projects/{pid}/autonomous-runs",
            json={
                "objective": "API smoke: create",
                "max_actions": 5,
                "max_runtime_seconds": 300,
            },
            headers=h,
        )
        assert resp.status_code in (200, 201), f"create: {resp.status_code} {resp.text}"
        body = resp.json()
        assert body["status"] == "created"
        assert body["objective"] == "API smoke: create"
        assert body["id"]

        c.post(
            f"/api/v1/autonomous-runs/{body['id']}/cancel",
            params={"project_id": pid},
            headers=h,
        )


def test_smoke_list_autonomous_runs() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=30) as c:
        tok = _login(c)
        h = _headers(tok)
        pid = _get_project_id(c, h)
        _cancel_all_runs(c, h, pid)

        resp = c.get(f"/api/v1/projects/{pid}/autonomous-runs", headers=h)
        assert resp.status_code == 200, f"list: {resp.status_code} {resp.text}"
        body = resp.json()
        items = body.get("items", body) if isinstance(body, dict) else body
        assert isinstance(items, list)


def test_smoke_get_autonomous_run() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=30) as c:
        tok = _login(c)
        h = _headers(tok)
        pid = _get_project_id(c, h)
        _cancel_all_runs(c, h, pid)

        create_resp = c.post(
            f"/api/v1/projects/{pid}/autonomous-runs",
            json={"objective": "API smoke: get"},
            headers=h,
        )
        assert create_resp.status_code in (200, 201), create_resp.text
        run_id = create_resp.json()["id"]

        resp = c.get(
            f"/api/v1/autonomous-runs/{run_id}",
            params={"project_id": pid},
            headers=h,
        )
        assert resp.status_code == 200, f"get: {resp.status_code} {resp.text}"
        assert resp.json()["id"] == run_id

        c.post(
            f"/api/v1/autonomous-runs/{run_id}/cancel",
            params={"project_id": pid},
            headers=h,
        )


def test_smoke_cancel_autonomous_run() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=30) as c:
        tok = _login(c)
        h = _headers(tok)
        pid = _get_project_id(c, h)
        _cancel_all_runs(c, h, pid)

        create_resp = c.post(
            f"/api/v1/projects/{pid}/autonomous-runs",
            json={"objective": "API smoke: cancel"},
            headers=h,
        )
        assert create_resp.status_code in (200, 201), create_resp.text
        run_id = create_resp.json()["id"]

        resp = c.post(
            f"/api/v1/autonomous-runs/{run_id}/cancel",
            params={"project_id": pid},
            headers=h,
        )
        assert resp.status_code == 200, f"cancel: {resp.status_code} {resp.text}"
        assert resp.json()["status"] == "cancelled"


def test_smoke_invalid_transition() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=30) as c:
        tok = _login(c)
        h = _headers(tok)
        pid = _get_project_id(c, h)
        _cancel_all_runs(c, h, pid)

        create_resp = c.post(
            f"/api/v1/projects/{pid}/autonomous-runs",
            json={"objective": "API smoke: bad transition"},
            headers=h,
        )
        assert create_resp.status_code in (200, 201), create_resp.text
        run_id = create_resp.json()["id"]

        resp1 = c.post(
            f"/api/v1/autonomous-runs/{run_id}/cancel",
            params={"project_id": pid},
            headers=h,
        )
        assert resp1.status_code == 200, resp1.text

        resp2 = c.post(
            f"/api/v1/autonomous-runs/{run_id}/cancel",
            params={"project_id": pid},
            headers=h,
        )
        assert resp2.status_code in (400, 409, 422), (
            f"double-cancel should fail: {resp2.status_code} {resp2.text}"
        )


def test_smoke_concurrent_active_run_guard() -> None:
    """Guard blocks creating a second active run while one exists."""
    with httpx.Client(base_url=BASE_URL, timeout=30) as c:
        tok = _login(c)
        h = _headers(tok)
        pid = _get_project_id(c, h)
        _cancel_all_runs(c, h, pid)

        r1 = c.post(
            f"/api/v1/projects/{pid}/autonomous-runs",
            json={"objective": "concurrent-1"},
            headers=h,
        )
        assert r1.status_code in (200, 201), r1.text
        run1_id = r1.json()["id"]

        r2 = c.post(
            f"/api/v1/projects/{pid}/autonomous-runs",
            json={"objective": "concurrent-2"},
            headers=h,
        )
        assert r2.status_code == 400, (
            f"creating 2nd run should be blocked: {r2.status_code} {r2.text}"
        )

        c.post(
            f"/api/v1/autonomous-runs/{run1_id}/cancel",
            params={"project_id": pid},
            headers=h,
        )
