import sys, io, requests
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = "http://localhost:9002"
REFRESH = "xb_-QV62a7K32U8EZU5fG4HMaR5FDorjQ0vKnDe9InY"
r = requests.post(f"{BASE}/api/v1/auth/refresh", json={"refresh_token": REFRESH}, timeout=30)
r.raise_for_status()
print("new refresh:", r.json()["refresh_token"])
TOKEN = r.json()["access_token"]
vid = "a5ee0054-0647-4102-8659-a1c15edb527f"
rid = "61878abe-d04d-4b0a-8352-1dfefb02e770"
r = requests.get(f"{BASE}/api/v1/report-versions/{vid}/download", headers={"Authorization": f"Bearer {TOKEN}"}, params={"report_id": rid}, timeout=30)
print(f"download -> status={r.status_code} type={r.headers.get('content-type')} bytes={len(r.content)}")
print(r.text[:2500])