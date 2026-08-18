import sys, io, json, requests
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = "http://localhost:9002"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJiNGVlMTE3Yy03YzFhLTQ2YzgtYTQ2My0wNDlmYjM5YWM2ZTkiLCJpYXQiOjE3ODcwNjUwNDYsImV4cCI6MTc4NzA2NTk0Nn0.01XTDoi2RxDRBH8O4OPCBWU6kKmIG1FlVqhxkKRB8zg"
H = {"Authorization": f"Bearer {TOKEN}"}

def show(method, path, label=""):
    r = requests.request(method, f"{BASE}{path}", headers=H, timeout=30)
    print(f"[{r.status_code}] {method} {path} {label}")
    if r.status_code == 200:
        print("   ", json.dumps(r.json(), default=str)[:600])
    else:
        print("   ", r.text[:300])

show("GET", "/api/v1/auth/me")
show("GET", "/api/v1/organizations")
