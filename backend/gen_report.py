"""Generate + finalize a live report for the Juice Shop validation project."""
import sys, io, json, requests
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = "http://localhost:9002"
REFRESH = "96s93iNcCfG9wGXolxIhzV3V-ZiQ9FHM__vvEKBIYnY"
proj_id = "a4e621ef-72c3-4f03-9922-ef843a3d19f3"

r = requests.post(f"{BASE}/api/v1/auth/refresh", json={"refresh_token": REFRESH}, timeout=30)
r.raise_for_status()
TOKEN = r.json()["access_token"]
print(f"new refresh={r.json()['refresh_token']}")
H = {"Authorization": f"Bearer {TOKEN}"}

def api(method, path, body=None, ok=(200,)):
    resp = requests.request(method, f"{BASE}{path}", headers=H, json=body, timeout=120)
    if resp.status_code not in (ok if isinstance(ok, tuple) else (ok,)):
        raise RuntimeError(f"{method} {path} -> {resp.status_code}: {resp.text[:400]}")
    try:
        return resp.json()
    except Exception:
        return None

rep = api("POST", f"/api/v1/projects/{proj_id}/reports",
          {"title": "OWASP Juice Shop 172.18.0.9 — Live Pipeline Validation"}, ok=(200, 201))
rid = rep["id"]
print(f"created report {rid} state={rep.get('status') or rep.get('state')}")

ver = api("POST", f"/api/v1/reports/{rid}/versions?project_id={proj_id}", {}, ok=(200, 201))
vid = ver["id"]
print(f"generated version {vid} state={ver.get('status') or ver.get('state')}")
print("version preview:", json.dumps({k: v for k, v in ver.items() if k not in ('report_content',)}, default=str)[:1500])

fin = api("POST", f"/api/v1/reports/{rid}/finalize?project_id={proj_id}")
print(f"finalized report state={fin.get('status') or fin.get('state')}")

# download
r = requests.get(f"{BASE}/api/v1/report-versions/{vid}/download", headers=H, timeout=30)
print(f"download -> status={r.status_code} content-type={r.headers.get('content-type')} bytes={len(r.content)}")
if r.headers.get("content-type", "").startswith("text") or "json" in r.headers.get("content-type", ""):
    print(r.content.decode("utf-8", errors="replace")[:1200])

print(f"REPORT_ID={rid}")
print(f"VERSION_ID={vid}")