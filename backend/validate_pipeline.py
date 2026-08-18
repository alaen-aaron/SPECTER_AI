"""Live security-pipeline validation: nmap against real Juice Shop via SPECTER_AI API."""
import sys, io, json, time, requests
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = "http://localhost:9002"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJiNGVlMTE3Yy03YzFhLTQ2YzgtYTQ2My0wNDlmYjM5YWM2ZTkiLCJpYXQiOjE3ODcwNjUwNDYsImV4cCI6MTc4NzA2NTk0Nn0.01XTDoi2RxDRBH8O4OPCBWU6kKmIG1FlVqhxkKRB8zg"
H = {"Authorization": f"Bearer {TOKEN}"}
JUICE_IP = "172.18.0.9"

def api(method, path, body=None, ok=(200,)):
    r = requests.request(method, f"{BASE}{path}", headers=H, json=body, timeout=60)
    if r.status_code not in (ok if isinstance(ok, tuple) else (ok,)):
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
    try:
        return r.json()
    except Exception:
        return None

# 1. Find latest org + project (existing test project)
orgs = api("GET", "/api/v1/organizations")
orgs.sort(key=lambda o: o["created_at"])
org_id = orgs[-1]["id"]
print(f"Using org: {org_id} ({orgs[-1]['name']})")

projects = api("GET", f"/api/v1/organizations/{org_id}/projects")
projects.sort(key=lambda p: p.get("created_at") or "")
proj = projects[-1]
proj_id = proj["id"]
print(f"Using project: {proj_id} ({proj['name']}) state={proj.get('state','?')}")

# 2. Create target for Juice Shop IP (dedup: reuse existing target with same value)
target_id = None
try:
    targets = api("GET", f"/api/v1/projects/{proj_id}/targets")
    for t in targets.get("items", targets if isinstance(targets, list) else []):
        if t.get("value") == JUICE_IP:
            target_id = t["id"]
            print(f"Reusing existing target: {target_id}")
            break
except Exception:
    pass
if not target_id:
    t = api("POST", f"/api/v1/projects/{proj_id}/targets",
            {"value": JUICE_IP, "target_type": "ip"}, ok=(200, 201))
    target_id = t["id"]
    print(f"Created target: {target_id} ({JUICE_IP})")

# 3. Ensure authorization record covering the IP
auths = api("GET", f"/api/v1/projects/{proj_id}/authorization")
records = auths if isinstance(auths, list) else auths.get("items", [])
has_auth = any(JUICE_IP in (r.get("allowed_targets") or []) for r in records)
if not has_auth:
    api("POST", f"/api/v1/projects/{proj_id}/authorization", {
        "client_name": "OWASP Juice Shop",
        "document_reference": "https://owasp.org/www-project-juice-shop/",
        "authorized_from": "2026-01-01",
        "authorized_to": "2027-12-31",
        "allowed_targets": [JUICE_IP, "localhost", "127.0.0.1"],
        "scope_notes": "Live pipeline validation",
    }, ok=(200, 201))
    print("Created authorization record")

# 4. Drive project to ACTIVE (DRAFT -> AUTHORIZED -> ACTIVE)
state = proj.get("state")
if state != "active":
    if state in ("draft", None):
        api("PATCH", f"/api/v1/projects/{proj_id}/state", {"state": "authorized"})
        print("Transitioned DRAFT -> AUTHORIZED")
    api("PATCH", f"/api/v1/projects/{proj_id}/state", {"state": "active"})
    print("Transitioned -> ACTIVE")

# 5. Scope Guard check
scope = api("POST", f"/api/v1/projects/{proj_id}/scope-check", {"target_ids": [target_id]})
print(f"Scope check: {scope.get('validated_target_ids', [])}")

# 6. Launch nmap -sV scan, ports 1-10000
scan = api("POST", f"/api/v1/projects/{proj_id}/scans", {
    "plugin": "nmap",
    "target_ids": [target_id],
    "plugin_config": {"target": JUICE_IP, "ports": "1-10000", "arguments": ["-sV", "-T4", "-Pn"]},
}, ok=(200, 201))
scan_id = scan["id"]
print(f"Launched nmap scan: {scan_id} status={scan.get('status')}")

# 7. Poll until terminal
terminal = {"completed", "failed", "cancelled", "error"}
for _ in range(90):
    time.sleep(3)
    d = api("GET", f"/api/v1/scans/{scan_id}")
    st = d.get("status")
    if st in terminal:
        print(f"Scan final status: {st} (after poll)")
        print(json.dumps({k: v for k, v in d.items() if k in ("status", "started_at", "completed_at", "error_message")}, default=str))
        break
else:
    print("TIMEOUT waiting for scan")

print(f"SCAN_ID={scan_id}")
print(f"TARGET_ID={target_id}")
print(f"PROJECT_ID={proj_id}")