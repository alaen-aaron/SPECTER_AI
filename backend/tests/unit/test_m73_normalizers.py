"""
M7.3 Phase 1 — web-tool normalizer tests (A–J).

Uses representative REAL tool output shapes:
- httpx `-json` JSONL (ProjectDiscovery httpx v1.x field names)
- whatweb `--log-json` document (array of per-target result objects)
- nuclei `-jsonl` entries with matched-at URLs

No Docker, no network — pure parsing tests.
"""

from __future__ import annotations

# Importing the package registers all built-ins.
import app.plugins.normalizers  # noqa: F401
from app.plugins.normalizer_registry import normalizer_registry
from app.plugins.normalizers.httpx_normalizer import HttpxNormalizer
from app.plugins.normalizers.nuclei_normalizer import NucleiNormalizer
from app.plugins.normalizers.whatweb_normalizer import WhatwebNormalizer

# --------------------------------------------------------------------------- #
# HTTPX (cases A–D)
# --------------------------------------------------------------------------- #

_HTTPX_JUICE_LINE = (
    '{"timestamp":"2026-08-22T15:53:20.123Z","url":"http://172.18.0.10:3000",'
    '"input":"172.18.0.10:3000","status_code":200,"failed":false,"len":7131,'
    '"title":"Juice Shop","body":"...","webserver":"","cdn":false,'
    '"tech":["HSTS","X-Powered-By:Express"],"host":"172.18.0.10",'
    '"port":"3000","scheme":"http","path":"/","method":"GET"}'
)
_HTTPX_DEFAULT_PORT_LINE = (
    '{"url":"https://example.com","status_code":200,"failed":false,'
    '"title":"Example Domain","webserver":"nginx","tech":["Nginx:1.24.0"],'
    '"cdn":true}'
)


def test_a_httpx_valid_single_record():
    payload = HttpxNormalizer().normalize(
        _HTTPX_JUICE_LINE, "", {"target": "172.18.0.10"}
    )
    assert payload["result_count"] == 1
    assert payload["skipped_lines"] == 0
    rec = payload["results"][0]
    assert rec["url"] == "http://172.18.0.10:3000"
    assert rec["status_code"] == 200
    assert rec["title"] == "Juice Shop"
    assert rec["scheme"] == "http"
    assert rec["port"] == 3000
    assert rec["cdn"] is False


def test_b_httpx_multiple_records():
    stdout = "\n".join([_HTTPX_JUICE_LINE, _HTTPX_DEFAULT_PORT_LINE])
    payload = HttpxNormalizer().normalize(stdout, "", {})
    assert payload["result_count"] == 2

    by_host = {r["host"]: r for r in payload["results"]}
    assert set(by_host) == {"172.18.0.10", "example.com"}

    nginx = next(
        t for t in by_host["example.com"]["technologies"] if t["name"] == "nginx"
    )
    assert nginx["version"] == "1.24.0"


def test_c_httpx_malformed_lines_skipped_and_counted():
    stdout = (
        "not json at all\n"
        "{broken json\n"
        + _HTTPX_JUICE_LINE
        + "\n"
        + '["a bare list"]\n'
        + '{"failed":true,"url":"http://10.255.255.1"}\n'
    )
    payload = HttpxNormalizer().normalize(stdout, "", {})
    # 1 good record; 2 malformed lines + 1 non-object JSON value
    # + 1 failed probe are all skipped.
    assert payload["result_count"] == 1
    assert payload["skipped_lines"] == 4


def test_d_httpx_host_port_scheme_derived_from_url_when_fields_absent():
    payload = HttpxNormalizer().normalize(_HTTPX_DEFAULT_PORT_LINE, "", {})
    rec = payload["results"][0]
    # No host/port/scheme fields in the record -> derived from the URL;
    # implicit https default port materializes as int 443.
    assert rec["scheme"] == "https"
    assert rec["host"] == "example.com"
    assert rec["port"] == 443

    # Tech name normalization: slugified lowercase, version split only
    # when the right side looks version-ish.
    names = {t["name"]: t["version"] for t in rec["technologies"]}
    assert names["nginx"] == "1.24.0"

    juice = HttpxNormalizer().normalize(_HTTPX_JUICE_LINE, "", {})
    juice_names = {t["name"] for t in juice["results"][0]["technologies"]}
    assert juice_names == {"hsts", "x-powered-by"}


# --------------------------------------------------------------------------- #
# WhatWeb (cases E–G)
# --------------------------------------------------------------------------- #

_WHATWEB_DOC = """WhatWeb report for authorized target
[
  {
    "target": "http://172.18.0.10:3000",
    "http_status": 200,
    "request_config": {"verify_peer": true},
    "plugins": {
      "HTTPServer": {"string": ["Express"], "version": ["4.x"]},
      "X-Powered-By": {"string": ["Express"]},
      "Juice Shop": {},
      "Bootstrap": {"version": ["4.5.0"]},
      "AngularJS": {"version": ["1.8.x"]}
    }
  }
]
"""


def test_e_whatweb_valid_log_json_document():
    payload = WhatwebNormalizer().normalize(_WHATWEB_DOC, "", {"target": ""})
    assert payload["result_count"] == 1
    rec = payload["results"][0]
    assert rec["url"] == "http://172.18.0.10:3000"
    assert rec["scheme"] == "http"
    assert rec["host"] == "172.18.0.10"
    assert rec["port"] == 3000
    assert rec["http_status"] == 200
    # Web server fingerprint surfaced from HTTPServer plugin string.
    assert rec["server"] == "Express"
    assert rec["technology_count"] >= 4


def test_f_whatweb_technology_extraction_and_normalization():
    payload = WhatwebNormalizer().normalize(_WHATWEB_DOC, "", {})
    techs = {t["raw_name"]: t for t in payload["results"][0]["technologies"]}
    assert techs["Juice Shop"]["name"] == "juice-shop"
    assert techs["AngularJS"]["versions"] == ["1.8.x"]
    assert techs["Bootstrap"]["versions"] == ["4.5.0"]
    assert techs["X-Powered-By"]["name"] == "x-powered-by"
    assert techs["HTTPServer"]["versions"] == ["4.x"]


def test_g_whatweb_malformed_entries_skipped_not_fatal():
    noisy = (
        "[banner text that is not json]\n"
        '{"target":"http://ok-host:8080","http_status":"NaN","plugins":{"Nmap":{}}}\n'
        + _WHATWEB_DOC
        + "\n[truncated garbage\n"
    )
    payload = WhatwebNormalizer().normalize(noisy, "", {})
    # The dict entry decodes (bad http_status coerced to None); the JSON
    # array decodes; banner/garbage fragments are ignored entirely.
    hosts = [r["host"] for r in payload["results"]]
    assert "ok-host" in hosts
    assert "172.18.0.10" in hosts
    bad = next(r for r in payload["results"] if r["host"] == "ok-host")
    assert bad["http_status"] is None

    totally_broken = "<html>not json</html>"
    empty = WhatwebNormalizer().normalize(totally_broken, "", {})
    assert empty["result_count"] == 0
    assert empty["results"] == []


# --------------------------------------------------------------------------- #
# Nuclei (case H) — additive matched_url, behavior preserved
# --------------------------------------------------------------------------- #


def test_h_nuclei_matched_at_promoted_to_matched_url():
    line = (
        '{"matched-at":"http://172.18.0.10:3000/metrics",'
        '"template-id":"prometheus-metrics",'
        '"info":{"name":"Prometheus Metrics - Detect","severity":"medium",'
        '"description":"Prometheus metrics page was detected.",'
        '"classification":{"cve-id":""}},"type":"http"}'
    )
    payload = NucleiNormalizer().normalize(line, "", {"target": ""})
    vuln = payload["vulnerabilities"][0]
    # New structured field present...
    assert vuln["matched_url"] == "http://172.18.0.10:3000/metrics"
    # ...while every pre-existing field/key is unchanged.
    assert vuln["matched_at"] == "http://172.18.0.10:3000/metrics"
    assert vuln["template_id"] == "prometheus-metrics"
    assert vuln["title"] == "Prometheus Metrics - Detect"
    assert vuln["severity"] == "medium"
    assert payload["target"] == "http://172.18.0.10:3000/metrics"


# --------------------------------------------------------------------------- #
# Registry (case I)
# --------------------------------------------------------------------------- #


def test_i_registry_returns_new_normalizers():
    httpx_norm = normalizer_registry.get("httpx")
    whatweb_norm = normalizer_registry.get("whatweb")
    assert isinstance(httpx_norm, HttpxNormalizer)
    assert isinstance(whatweb_norm, WhatwebNormalizer)
    # Pre-existing registrations untouched.
    assert normalizer_registry.get("nmap") is not None
    assert normalizer_registry.get("ping") is not None
    assert normalizer_registry.get("nuclei") is not None
