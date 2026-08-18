"""Live security-pipeline validation — nmap, whatweb, httpx, nuclei via SPECTER_AI API."""
import sys, io, json, time, requests
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = "http://localhost:9002"
REFRESH = "qTZRQJB6zNyZtdqtqICM8T7kyBw0Wcpw872wL6JJFSI"
JUICE = "172.18.0.9"
JUICE_URL = "http://172.18.0.9:3000"

def api(method, path, body=None, ok=(200,), headers=None, timeout=120):
    h = {"Authorization": f"Bearer {TOKEN}"}
    if headers:
        h.update(headers)
    r = requests.request(method, f"{BASE}{path}", headers=h, json=body, timeout=timeout)
    if r.status_code not in (ok if isinstance(ok, tuple) else (ok,)):
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
    try:
        return r.json()
    except Exception:
        return None

# refresh
r = requests.post(f"{BASE}/api/v1/auth/refresh", json={"refresh_token": REFRESH}, timeout=30)
r.raise_for_status()
TOKEN = r.json()["access_token"]
print(f"refreshed token, new refresh={r.json()['refresh_token']}")

proj_id = "a4e621ef-72c3-4f03-9922-ef843a3d19f3"
target_id = "7abfeaa1-2f42-4b7a-bca8-f78685a1bbcd"

def launch_and_poll(name, plugin, config, timeout_s=240):
    scan = api("POST", f"/api/v1/projects/{proj_id}/scans", {
        "plugin": plugin, "target_ids": [target_id], "plugin_config": config,
    }, ok=(200, 201))
    sid = scan["id"]
    print(f"[{name}] launched {plugin} scan {sid} status={scan.get('status')}")
    terminal = {"completed", "failed", "cancelled", "error"}
    for _ in range(timeout_s // 3):
        time.sleep(3)
        d = api("GET", f"/api/v1/scans/{sid}")
        if d.get("status") in terminal:
            print(f"[{name}] final status={d.get('status')} exit_code={d.get('exit_code')} "
                  f"error={d.get('error_message')}")
            return d
    print(f"[{name}] TIMEOUT waiting")
    return api("GET", f"/api/v1/scans/{sid}")

# nmap (fresh run on fixed worker)
launch_and_poll("nmap", "nmap", {"target": JUICE, "ports": "3000", "arguments": ["-sV", "-T4", "-Pn"]}, 120)

# whatweb
launch_and_poll("whatweb", "whatweb", {"target": JUICE_URL}, 180)

# httpx (Go binary now)
launch_and_poll("httpx", "httpx", {"target": JUICE_URL}, 120)

# nuclei (jsonl fix + new normalizer)
launch_and_poll("nuclei", "nuclei", {"target": JUICE_URL, "severity": "medium,high,critical"}, 180)

# Verify pipeline outputs via API
print("\n=== ASSETS ===")
print(json.dumps(api("GET", f"/api/v1/projects/{proj_id}/assets"), default=str)[:3000])
print("\n=== FINDINGS ===")
print(json.dumps(api("GET", f"/api/v1/projects/{proj_id}/findings"), default=str)[:3000])
print("\n=== GRAPH SUMMARY ===")
print(json.dumps(api("GET", f"/api/v1/projects/{proj_id}/graph/summary"), default=str))