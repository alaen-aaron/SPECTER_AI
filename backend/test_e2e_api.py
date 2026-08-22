"""Full end-to-end API verification for SPECTER_AI."""
import io, sys, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import requests, json, time
from datetime import datetime

BASE = "http://localhost:9002"
E2E_EMAIL = os.environ.get("SPECTER_E2E_EMAIL", "e2e.alice@example.com")
E2E_PASSWORD = os.environ.get("SPECTER_E2E_PASSWORD", "Owner-pass-2026!")
_login = requests.post(
    f"{BASE}/api/v1/auth/login",
    json={"email": E2E_EMAIL, "password": E2E_PASSWORD},
    timeout=30,
)
_login.raise_for_status()
TOKEN = _login.json()["access_token"]
H = {"Authorization": f"Bearer {TOKEN}"}

passed = 0
failed = 0
results = []

def req(method, path, json_data=None, expect=None, label=None):
    global passed, failed
    url = f"{BASE}{path}"
    label = label or f"{method} {path}"
    try:
        r = requests.request(method, url, headers=H, json=json_data, timeout=30)
        status = r.status_code
        body = r.text[:300]
        if expect:
            expected = expect if isinstance(expect, list) else [expect]
            if status not in expected:
                results.append(("FAIL", label, status, f"expected {expected}, got {body[:100]}"))
                failed += 1
                return None
        elif not (200 <= status < 300):
            results.append(("FAIL", label, status, f"non-2xx, got {body[:100]}"))
            failed += 1
            return None
        results.append(("OK", label, status, body[:100]))
        passed += 1
        try:
            return r.json()
        except:
            return r.text
    except Exception as e:
        results.append(("FAIL", label, 0, str(e)[:100]))
        failed += 1
        return None

print("=" * 70)
print("SPECTER_AI FULL API VERIFICATION")
print(f"Target: {BASE} | User: Alice Smith")
print(f"Time: {datetime.now().isoformat()}")
print("=" * 70)

# ── AUTH ──
print("\n── AUTH ──")
me = req("GET", "/api/v1/auth/me", expect=200)
if me:
    print(f"  User: {me['full_name']} <{me['email']}>")
# (Refresh rotation, logout, logout-all verified separately above.)

# ── ORGANIZATIONS ──
print("\n── ORGANIZATIONS ──")
org = req("POST", "/api/v1/organizations", {"name": "Pentest Corp", "description": "Security testing org"}, expect=[200, 201])
org_id = org["id"] if org else None
if org_id:
    print(f"  Created org: {org['name']} ({org_id[:8]}...)")

    req("GET", f"/api/v1/organizations/{org_id}", expect=200, label="GET organization")
    req("GET", "/api/v1/organizations", expect=200, label="LIST organizations")
    req("PATCH", f"/api/v1/organizations/{org_id}", {"name": "Pentest Corp US"}, expect=200, label="PATCH organization")

    # Members
    members = req("GET", f"/api/v1/organizations/{org_id}/members", expect=200, label="LIST org members")
    if members:
        print(f"  Org has {len(members)} member(s)")

# ── PROJECTS ──
print("\n── PROJECTS ──")
project = None
if org_id:
    project = req("POST", f"/api/v1/organizations/{org_id}/projects", {"name": "Juice Shop Pentest", "description": "OWASP Juice Shop pentest engagement"}, expect=[200, 201])
    if project:
        print(f"  Created project: {project['name']} ({project['id'][:8]}...)")

proj_id = project["id"] if project else None
if proj_id:
    req("GET", f"/api/v1/projects/{proj_id}", expect=200, label="GET project")
    req("PATCH", f"/api/v1/projects/{proj_id}", {"description": "Updated description"}, expect=200, label="PATCH project")

    # Project members
    req("GET", f"/api/v1/projects/{proj_id}/members", expect=200, label="LIST project members")

# ── TARGETS ──
print("\n── TARGETS ──")
target_id = None
if proj_id:
    t1 = req("POST", f"/api/v1/projects/{proj_id}/targets", {"value": "172.18.0.4", "target_type": "ip"}, expect=[200, 201])
    if t1:
        target_id = t1["id"]
        print(f"  Created target: {t1['value']} ({t1['id'][:8]}...)")

    t2 = req("POST", f"/api/v1/projects/{proj_id}/targets", {"value": "172.18.0.9", "target_type": "ip"}, expect=[200, 201])
    if t2:
        print(f"  Created target: {t2['value']} ({t2['id'][:8]}...)")

    req("GET", f"/api/v1/projects/{proj_id}/targets", expect=200, label="LIST project targets")

    if target_id:
        req("GET", f"/api/v1/targets/{target_id}", expect=200, label="GET target")
        req("PATCH", f"/api/v1/targets/{target_id}", {"target_type": "ip"}, expect=200, label="PATCH target")

# ── AUTHORIZATION RECORDS ──
print("\n── AUTHORIZATION RECORDS ──")
auth_record = None
if proj_id:
    auth_record = req("POST", f"/api/v1/projects/{proj_id}/authorization", {
        "client_name": "OWASP Juice Shop",
        "document_reference": "https://owasp.org/www-project-juice-shop/",
        "authorized_from": "2026-01-01",
        "authorized_to": "2027-12-31",
        "allowed_targets": ["172.18.0.4", "172.18.0.9", "localhost", "127.0.0.1"],
        "scope_notes": "Full Juice Shop testing authorized"
    }, expect=[200, 201])
    if auth_record:
        print(f"  Created auth record: {auth_record['id'][:8]}...")
    req("GET", f"/api/v1/projects/{proj_id}/authorization", expect=200, label="LIST authorization records")

# Activate project (needs valid authorization); DRAFT → AUTHORIZED → ACTIVE
if proj_id:
    req("PATCH", f"/api/v1/projects/{proj_id}/state", {"state": "authorized"}, expect=200, label="AUTHORIZE project")
    req("PATCH", f"/api/v1/projects/{proj_id}/state", {"state": "active"}, expect=200, label="ACTIVATE project")

# ── SCOPE GUARD ──
print("\n── SCOPE GUARD ──")
if proj_id and target_id:
    scope = req("POST", f"/api/v1/projects/{proj_id}/scope-check", {"target_ids": [target_id]}, expect=[200, 201])
    if scope:
        print(f"  Scope check PASSED: {scope.get('validated_target_ids', [])}")

# ── PLUGINS ──
print("\n── PLUGINS ──")
plugins_resp = req("GET", "/api/v1/plugins", expect=200)
plugins = plugins_resp.get("items", []) if isinstance(plugins_resp, dict) else (plugins_resp or [])
if plugins:
    print(f"  {len(plugins)} plugins loaded")
    for p in plugins[:5]:
        print(f"    {p['name']:20s} v{p.get('version','?'):10s} healthy={p.get('healthy','?')}")
    if len(plugins) > 5:
        print(f"    ... and {len(plugins)-5} more")

health_resp = req("GET", "/api/v1/plugins/health", expect=200, label="PLUGIN health check")
if isinstance(health_resp, dict):
    print(f"  Health: {health_resp.get('healthy',0)} healthy, {health_resp.get('unhealthy',0)} unhealthy, {health_resp.get('total',0)} total")

if plugins and len(plugins) > 0:
    pname = plugins[0]["name"]
    req("GET", f"/api/v1/plugins/{pname}", expect=200, label=f"GET plugin {pname}")
    req("GET", f"/api/v1/plugins/{pname}/metadata", expect=200, label=f"METADATA {pname}")
    req("GET", f"/api/v1/plugins/{pname}/capability", expect=200, label=f"CAPABILITY {pname}")

# Plugin categories and tags
if plugins:
    first_cat = plugins[0].get("category", "reconnaissance")
    cat_resp = req("GET", f"/api/v1/plugins/category/{first_cat}", expect=200, label=f"PLUGINS by category {first_cat}")
    if isinstance(cat_resp, dict):
        print(f"  Category '{first_cat}': {len(cat_resp.get('items', []))} plugins")

# ── SCANS ──
print("\n── SCANS ──")
scan_ids = []
if proj_id and target_id:
    # Ping scan
    scan1 = req("POST", f"/api/v1/projects/{proj_id}/scans", {
        "plugin": "ping",
        "target_ids": [target_id],
        "plugin_config": {"hostname": "172.18.0.4"}
    }, expect=[200, 201])
    if scan1:
        scan_ids.append(scan1["id"])
        print(f"  Created PING scan: {scan1['id'][:8]}... status={scan1.get('status','?')}")

    # Nmap scan
    scan2 = req("POST", f"/api/v1/projects/{proj_id}/scans", {
        "plugin": "nmap",
        "target_ids": [target_id],
        "plugin_config": {"target": "172.18.0.4", "ports": "1-100", "arguments": ["-sV", "-T4"]}
    }, expect=[200, 201])
    if scan2:
        scan_ids.append(scan2["id"])
        print(f"  Created NMAP scan: {scan2['id'][:8]}... status={scan2.get('status','?')}")

    # List scans
    req("GET", f"/api/v1/projects/{proj_id}/scans", expect=200, label="LIST project scans")

    # Wait for scans to complete
    print("  Waiting 15s for scans to execute...")
    time.sleep(15)

    for sid in scan_ids:
        detail = req("GET", f"/api/v1/scans/{sid}", expect=200, label=f"GET scan {sid[:8]}")
        if detail:
            print(f"  Scan {sid[:8]}... status={detail.get('status','?')}")

# ── GRAPH ──
print("\n── GRAPH ──")
node_ids = []
if proj_id:
    req("POST", f"/api/v1/projects/{proj_id}/graph/rebuild", expect=[200, 201], label="REBUILD graph")
    time.sleep(2)

    summary = req("GET", f"/api/v1/projects/{proj_id}/graph/summary", expect=200, label="GRAPH summary")
    if summary:
        print(f"  Graph: {summary.get('total_nodes', summary.get('node_count',0))} nodes, {summary.get('total_edges', summary.get('edge_count',0))} edges")

    req("GET", f"/api/v1/projects/{proj_id}/graph/nodes", expect=200, label="GRAPH nodes")
    nodes_resp = req("GET", f"/api/v1/projects/{proj_id}/graph/nodes", expect=200, label="GRAPH nodes")
    if isinstance(nodes_resp, dict):
        for item in nodes_resp.get("items", []):
            node_ids.append(item.get("id"))
    elif isinstance(nodes_resp, list):
        node_ids = [n.get("id") for n in nodes_resp if n.get("id")]
    req("GET", f"/api/v1/projects/{proj_id}/graph/edges", expect=200, label="GRAPH edges")
    if node_ids:
        req("GET", f"/api/v1/projects/{proj_id}/graph/blast-radius?node_id={node_ids[0]}", expect=200, label="GRAPH blast-radius")
    else:
        print("  SKIP blast-radius (no graph nodes yet)")

# ── ASSETS ──
print("\n── ASSETS ──")
if proj_id:
    req("GET", f"/api/v1/projects/{proj_id}/assets", expect=200, label="LIST project assets")

# ── FINDINGS ──
print("\n── FINDINGS ──")
if proj_id:
    req("GET", f"/api/v1/projects/{proj_id}/findings", expect=200, label="LIST project findings")

# ── REPORTS ──
print("\n── REPORTS ──")
report_id = None
if proj_id:
    rpt = req("POST", f"/api/v1/projects/{proj_id}/reports", {"title": "Juice Shop Pentest Report"}, expect=[200, 201])
    if rpt:
        report_id = rpt["id"]
        print(f"  Created report: {rpt.get('title', rpt.get('name','?'))} ({report_id[:8]}...)")
    req("GET", f"/api/v1/projects/{proj_id}/reports", expect=200, label="LIST project reports")

    req("GET", f"/api/v1/report-templates?project_id={proj_id}", expect=200, label="LIST report templates")

if report_id and proj_id:
    req("GET", f"/api/v1/reports/{report_id}?project_id={proj_id}", expect=200, label="GET report")
    ver = req("POST", f"/api/v1/reports/{report_id}/versions?project_id={proj_id}", {"template_name": "pentest_report"}, expect=[200, 201])
    if ver:
        print(f"  Generated report version: {ver['version_number']}")
    req("POST", f"/api/v1/reports/{report_id}/finalize?project_id={proj_id}", expect=[200, 201], label="FINALIZE report")
    req("GET", f"/api/v1/reports/{report_id}/pdf?project_id={proj_id}", expect=200, label="DOWNLOAD PDF")

# ── WORKFLOWS ──
print("\n── WORKFLOWS ──")
wf_id = None
if proj_id:
    wt_resp = req("GET", "/api/v1/workflow-templates", expect=200, label="LIST workflow templates")
    if isinstance(wt_resp, dict):
        print(f"  Workflow templates: {len(wt_resp.get('items', []))}")
    wf = req("POST", f"/api/v1/projects/{proj_id}/workflows", {
        "name": "Full Port Scan Workflow",
        "description": "Automated port scan"
    }, expect=[200, 201])
    if wf:
        wf_id = wf["id"]
        print(f"  Created workflow: {wf['name']} ({wf_id[:8]}...)")
    req("GET", f"/api/v1/projects/{proj_id}/workflows", expect=200, label="LIST workflows")

if wf_id and proj_id:
    req("GET", f"/api/v1/workflows/{wf_id}?project_id={proj_id}", expect=200, label="GET workflow")
    req("GET", f"/api/v1/workflows/{wf_id}/steps?project_id={proj_id}", expect=200, label="LIST workflow steps")
    req("POST", f"/api/v1/workflows/{wf_id}/steps?project_id={proj_id}", {"plugin": "ping", "name": "Ping juice-shop", "plugin_config": {"hostname": "172.18.0.4"}}, expect=[200, 201], label="ADD workflow step")
    req("POST", f"/api/v1/workflows/{wf_id}/activate?project_id={proj_id}", expect=[200, 201], label="ACTIVATE workflow")

# ── SCHEDULES ──
print("\n── SCHEDULES ──")
sched_id = None
if proj_id and wf_id:
    sched = req("POST", f"/api/v1/projects/{proj_id}/schedules", {
        "workflow_id": wf_id,
        "frequency": "daily",
        "cron_expression": "0 2 * * *"
    }, expect=[200, 201])
    if sched:
        sched_id = sched["id"]
        print(f"  Created schedule: {sched_id[:8]}...")
    req("GET", f"/api/v1/projects/{proj_id}/schedules", expect=200, label="LIST schedules")

if sched_id and proj_id:
    req("GET", f"/api/v1/schedules/{sched_id}?project_id={proj_id}", expect=200, label="GET schedule")
    req("POST", f"/api/v1/schedules/{sched_id}/pause?project_id={proj_id}", expect=[200, 201], label="PAUSE schedule")
    req("POST", f"/api/v1/schedules/{sched_id}/resume?project_id={proj_id}", expect=[200, 201], label="RESUME schedule")
    req("DELETE", f"/api/v1/schedules/{sched_id}?project_id={proj_id}", expect=[200, 204], label="DELETE schedule")

# ── INTELLIGENCE (AI) ──
print("\n── INTELLIGENCE (AI) ──")
if proj_id:
    req("GET", f"/api/v1/projects/{proj_id}/intelligence/crown-jewels", expect=200, label="CROWN jewels")
    req("GET", f"/api/v1/projects/{proj_id}/intelligence/historical", expect=200, label="HISTORICAL intel")
    req("GET", f"/api/v1/projects/{proj_id}/intelligence/executive", expect=200, label="EXECUTIVE summary")

    if len(node_ids) >= 1:
        req("GET", f"/api/v1/projects/{proj_id}/intelligence/impact?node_id={node_ids[0]}", expect=200, label="IMPACT analysis")
    else:
        print("  SKIP impact (no graph nodes)")
    req("GET", f"/api/v1/projects/{proj_id}/intelligence/lateral-movement", expect=200, label="LATERAL movement")

    if len(node_ids) >= 2:
        req("GET", f"/api/v1/projects/{proj_id}/intelligence/attack-paths?from_node_id={node_ids[0]}&to_node_id={node_ids[1]}", expect=200, label="ATTACK paths")
        req("GET", f"/api/v1/projects/{proj_id}/intelligence/reachable?from_node_id={node_ids[0]}", expect=200, label="REACHABLE assets")
        req("GET", f"/api/v1/projects/{proj_id}/intelligence/multiple-paths?from_node_id={node_ids[0]}&to_node_id={node_ids[1]}", expect=200, label="MULTIPLE paths")
    else:
        print("  SKIP attack-paths/reachable/multiple-paths (need >=2 graph nodes)")

    # Planner
    req("POST", f"/api/v1/ai/planner/suggest?project_id={proj_id}", expect=[200, 201], label="PLANNER suggest")
    req("GET", f"/api/v1/ai/planner/suggestions?project_id={proj_id}", expect=200, label="PLANNER list suggestions")

    # Analyzer
    req("POST", f"/api/v1/ai/analyzer/correlate?project_id={proj_id}", expect=[200, 201], label="ANALYZER correlate")

    # Reporter
    req("GET", f"/api/v1/ai/reporter/executive-summary?project_id={proj_id}", expect=200, label="REPORTER exec summary")

    # Prompts
    prompts = req("GET", "/api/v1/ai/prompts", expect=200, label="LIST prompt templates")
    if prompts:
        print(f"  Prompt templates: {len(prompts)}")

# ── EVIDENCE ──
print("\n── EVIDENCE ──")
if proj_id:
    req("GET", f"/api/v1/projects/{proj_id}/findings", expect=200, label="LIST findings for evidence check")

# ── HEALTH & METRICS ──
print("\n── HEALTH & METRICS ──")
req("GET", "/api/v1/health", expect=200, label="HEALTH check")
req("GET", "/api/v1/metrics", expect=200, label="METRICS endpoint")

# ── INVITATIONS ──
print("\n── INVITATIONS ──")
if org_id:
    inv = req("POST", f"/api/v1/organizations/{org_id}/invitations", {
        "email": "charlie@example.com",
        "role": "member",
        "project_id": proj_id
    }, expect=[200, 201])
    if inv:
        print(f"  Invitation sent to {inv.get('email','?')}")
    req("GET", f"/api/v1/organizations/{org_id}/invitations", expect=200, label="LIST invitations")

# ── CLEANUP: Delete targets, projects ──
print("\n── CLEANUP ──")
if sched_id and proj_id:
    req("DELETE", f"/api/v1/schedules/{sched_id}?project_id={proj_id}", expect=[200, 204, 404], label="DELETE schedule (cleanup)")

# ═══════════════════════════════════════════════════
# RESULTS
# ═══════════════════════════════════════════════════
print("\n" + "=" * 70)
print(f"RESULTS: {passed} PASSED / {failed} FAILED / {passed+failed} TOTAL")
print("=" * 70)

if failed > 0:
    print("\nFAILED TESTS:")
    for status, label, code, detail in results:
        if status == "FAIL":
            print(f"  ✗ [{code}] {label}: {detail}")

print("\nFULL LOG:")
for status, label, code, detail in results:
    mark = "✓" if status == "OK" else "✗"
    print(f"  {mark} [{code}] {label}")

print(f"\nDone at {datetime.now().isoformat()}")
