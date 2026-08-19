"""
Report API route binding regression tests.

The report endpoints keyed by `{report_id}` or `{version_id}` previously
leaked the project context into required QUERY parameters (e.g.
`?project_id=...` or `?report_id=...`) because their permission
dependencies bound a path parameter that does not exist on the route.
These tests assert the routes resolve project context from the entity in
the path and therefore expose NO phantom required query params.
"""

from __future__ import annotations

from app.main import create_app

# Routes that must NOT require a `project_id` query param
_REPORT_ID_ROUTES = {
    ("POST", "/api/v1/reports/{report_id}/versions"),
    ("POST", "/api/v1/reports/{report_id}/finalize"),
    ("GET", "/api/v1/reports/{report_id}/pdf"),
}

# Routes that must NOT require a `report_id` query param
_REPORT_VERSION_ID_ROUTES = {
    ("GET", "/api/v1/report-versions/{version_id}"),
    ("GET", "/api/v1/report-versions/{version_id}/download"),
    ("GET", "/api/v1/report-versions/{version_id_a}/diff/{version_id_b}"),
}

# Route that must NOT require a `project_id` query param (global template list)
_TEMPLATE_ROUTE = ("GET", "/api/v1/report-templates")

# Optional query params that are genuinely part of the API contract
_ALLOWED_OPTIONAL = {"template", "redacted"}


def _required_query_params(app, method, path) -> list[str]:
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return [
                p.name
                for p in route.dependant.query_params
                if p.required and p.name not in _ALLOWED_OPTIONAL
            ]
    raise AssertionError(f"route not found: {method} {path}")


def _test_no_phantom_query_params(method: str, path: str, forbidden: str) -> None:
    app = create_app()
    required = _required_query_params(app, method, path)
    assert forbidden not in required, f"{method} {path} requires phantom query param '{forbidden}'"


def test_report_id_routes_do_not_require_project_id() -> None:
    for method, path in _REPORT_ID_ROUTES:
        _test_no_phantom_query_params(method, path, "project_id")


def test_report_id_routes_do_not_require_report_id() -> None:
    for method, path in _REPORT_ID_ROUTES:
        _test_no_phantom_query_params(method, path, "report_id")


def test_version_id_routes_do_not_require_report_id() -> None:
    for method, path in _REPORT_VERSION_ID_ROUTES:
        _test_no_phantom_query_params(method, path, "report_id")


def test_version_id_routes_do_not_require_project_id() -> None:
    for method, path in _REPORT_VERSION_ID_ROUTES:
        _test_no_phantom_query_params(method, path, "project_id")


def test_report_templates_route_requires_no_project_id() -> None:
    _test_no_phantom_query_params(*_TEMPLATE_ROUTE, "project_id")


def test_report_templates_route_requires_no_report_id() -> None:
    _test_no_phantom_query_params(*_TEMPLATE_ROUTE, "report_id")


def test_diff_route_requires_no_phantom_query_params() -> None:
    app = create_app()
    path = "/api/v1/report-versions/{version_id_a}/diff/{version_id_b}"
    for route in app.routes:
        if getattr(route, "path", None) == path and "GET" in route.methods:
            required = [
                p.name
                for p in route.dependant.query_params
                if p.required and p.name not in _ALLOWED_OPTIONAL
            ]
            assert required == [], f"diff route requires phantom query params: {required}"
            return
    raise AssertionError("route not found")


def test_version_generation_still_allows_optional_template_and_redacted() -> None:
    """The optional template/redacted params are preserved as query params."""
    app = create_app()
    path = "/api/v1/reports/{report_id}/versions"
    for route in app.routes:
        if getattr(route, "path", None) == path and "POST" in route.methods:
            names = {p.name for p in route.dependant.query_params}
            assert "template" in names
            assert "redacted" in names
            return
    raise AssertionError("route not found")