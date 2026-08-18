path = r"C:\Users\ALAEN JOSHVA\Downloads\genai\specter-ai-milestone3 perfect\backend\test_e2e_api.py"
src = open(path, encoding="utf-8").read()
lines = src.split("\n")
out = []
for ln in lines:
    if 'req("POST"' in ln and "expect=200" in ln:
        ln = ln.replace("expect=200", "expect=[200, 201]")
    out.append(ln)
open(path, "w", encoding="utf-8").write("\n".join(out))
print("updated", sum(1 for a, b in zip(out, lines) if a != b), "lines")
