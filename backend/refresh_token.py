import sys, io, json, requests
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = "http://localhost:9002"
REFRESH = "3A-Pe4GS7iT2bmeDbGQugKeeJztcT8vI9zVLszAqLdE"

r = requests.post(f"{BASE}/api/v1/auth/refresh", json={"refresh_token": REFRESH}, timeout=30)
print("refresh:", r.status_code)
if r.status_code == 200:
    data = r.json()
    print("NEW_ACCESS:", data["access_token"])
    print("NEW_REFRESH:", data.get("refresh_token", ""))
else:
    print(r.text[:400])
