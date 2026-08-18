import requests

access = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJiNGVlMTE3Yy03YzFhLTQ2YzgtYTQ2My0wNDlmYjM5YWM2ZTkiLCJpYXQiOjE3ODcwNjM4NTUsImV4cCI6MTc4NzA2NDc1NX0.BwTG7YJUeEN1PpbyPJ7Rc9D3dcMi2VRpEy75nE5zPrI"
h = {"Authorization": "Bearer " + access}

paths = [
    "/api/v1/plugins/health",
    "/api/v1/organizations",
    "/api/v1/workflow-templates",
    "/api/v1/report-templates",
    "/api/v1/ai/prompts",
    "/api/v1/plugins/category/reconnaissance",
]
for path in paths:
    try:
        r = requests.get("http://localhost:9002" + path, headers=h, timeout=15)
        d = r.json()
        shape = type(d).__name__
        extra = ""
        if isinstance(d, dict):
            keys = list(d.keys())[:5]
            extra = "keys=" + ",".join(keys)
            if "items" in d:
                it = d["items"]
                extra += " | items: type=" + type(it).__name__
                if isinstance(it, list):
                    extra += " len=" + str(len(it))
        elif isinstance(d, list):
            extra = "len=" + str(len(d))
        print(r.status_code, path, "->", shape, extra)
    except Exception as e:
        print("ERR", path, str(e)[:100])
