"""
Plugin registry and workflow template endpoints (Milestone 5).

Exposes plugin discovery, health checks, compatibility validation,
workflow template listing, and workflow recommendation endpoints.

Route ordering: all static/prefix routes are registered BEFORE the
parameterized ``/{plugin_name}`` route to prevent FastAPI from capturing
them as path parameters (e.g. ``GET /plugins/health`` must not match
``{plugin_name}="health"``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.v1.deps import get_current_user, get_plugin_registry
from app.api.v1.schemas.plugins import (
    PluginCapabilityResponse,
    PluginHealthCheckResponse,
    PluginListResponse,
    PluginMetadataResponse,
    PluginResponse,
    WorkflowCompatibilityResponse,
    WorkflowTemplateListResponse,
    WorkflowTemplateResponse,
)
from app.domain.builtin_templates import list_builtin_templates
from app.domain.entities import User
from app.plugins.base import PluginCategory
from app.plugins.registry import PluginRegistry

router = APIRouter(tags=["plugins"])


# --- Helpers ------------------------------------------------------------------

def _plugin_to_response(p: object) -> PluginResponse:
    meta = p.metadata()
    return PluginResponse(
        name=p.name(),
        description=p.description(),
        category=meta.category.value,
        version=meta.version,
        tags=sorted(meta.tags),
    )


# --- Plugin List --------------------------------------------------------------

@router.get(
    "/plugins",
    response_model=PluginListResponse,
    summary="List all registered plugins",
)
async def list_plugins(
    registry: PluginRegistry = Depends(get_plugin_registry),
    _user: User = Depends(get_current_user),
) -> PluginListResponse:
    plugins = registry.list()
    return PluginListResponse(items=[_plugin_to_response(p) for p in plugins])


# --- Static / prefix routes (MUST precede /{plugin_name}) --------------------

@router.get(
    "/plugins/health",
    response_model=PluginHealthCheckResponse,
    summary="Check health of all plugins",
)
async def check_plugin_health(
    registry: PluginRegistry = Depends(get_plugin_registry),
    _user: User = Depends(get_current_user),
) -> PluginHealthCheckResponse:
    health = registry.check_health()
    healthy = sorted([n for n, h in health.items() if h])
    unhealthy = sorted([n for n, h in health.items() if not h])
    return PluginHealthCheckResponse(
        healthy=healthy,
        unhealthy=unhealthy,
        total=len(health),
    )


@router.get(
    "/plugins/compatible/{upstream_name}",
    response_model=PluginListResponse,
    summary="Find plugins compatible with upstream output",
)
async def find_compatible_plugins(
    upstream_name: str,
    registry: PluginRegistry = Depends(get_plugin_registry),
    _user: User = Depends(get_current_user),
) -> PluginListResponse:
    plugins = registry.find_compatible(upstream_name)
    return PluginListResponse(items=[_plugin_to_response(p) for p in plugins])


@router.get(
    "/plugins/compatibility/{upstream_name}/{downstream_name}",
    response_model=WorkflowCompatibilityResponse,
    summary="Check if downstream can consume upstream output",
)
async def check_compatibility(
    upstream_name: str,
    downstream_name: str,
    registry: PluginRegistry = Depends(get_plugin_registry),
    _user: User = Depends(get_current_user),
) -> WorkflowCompatibilityResponse:
    is_compatible, reason = registry.validate_compatibility(
        upstream_name, downstream_name
    )
    return WorkflowCompatibilityResponse(
        upstream=upstream_name,
        downstream=downstream_name,
        is_compatible=is_compatible,
        reason=reason,
    )


@router.get(
    "/plugins/category/{category}",
    response_model=PluginListResponse,
    summary="List plugins by category",
)
async def list_plugins_by_category(
    category: PluginCategory,
    registry: PluginRegistry = Depends(get_plugin_registry),
    _user: User = Depends(get_current_user),
) -> PluginListResponse:
    plugins = registry.list_by_category(category)
    return PluginListResponse(items=[_plugin_to_response(p) for p in plugins])


@router.get(
    "/plugins/tag/{tag}",
    response_model=PluginListResponse,
    summary="List plugins by tag",
)
async def list_plugins_by_tag(
    tag: str,
    registry: PluginRegistry = Depends(get_plugin_registry),
    _user: User = Depends(get_current_user),
) -> PluginListResponse:
    plugins = registry.list_by_tag(tag)
    return PluginListResponse(items=[_plugin_to_response(p) for p in plugins])


# --- Dynamic plugin routes (AFTER all static/prefix routes) -------------------

@router.get(
    "/plugins/{plugin_name}",
    response_model=PluginResponse,
    summary="Get plugin details",
)
async def get_plugin(
    plugin_name: str,
    registry: PluginRegistry = Depends(get_plugin_registry),
    _user: User = Depends(get_current_user),
) -> PluginResponse:
    plugin = registry.get(plugin_name)
    return _plugin_to_response(plugin)


@router.get(
    "/plugins/{plugin_name}/capability",
    response_model=PluginCapabilityResponse,
    summary="Get plugin capability declaration",
)
async def get_plugin_capability(
    plugin_name: str,
    registry: PluginRegistry = Depends(get_plugin_registry),
    _user: User = Depends(get_current_user),
) -> PluginCapabilityResponse:
    cap = registry.get_capability(plugin_name)
    return PluginCapabilityResponse(
        input_asset_types=sorted(cap.input_asset_types),
        output_asset_types=sorted(cap.output_asset_types),
        produces_findings=cap.produces_findings,
        requires_host=cap.requires_host,
        requires_open_ports=cap.requires_open_ports,
        max_targets=cap.max_targets,
    )


@router.get(
    "/plugins/{plugin_name}/metadata",
    response_model=PluginMetadataResponse,
    summary="Get plugin metadata (version, binaries, etc.)",
)
async def get_plugin_metadata(
    plugin_name: str,
    registry: PluginRegistry = Depends(get_plugin_registry),
    _user: User = Depends(get_current_user),
) -> PluginMetadataResponse:
    meta = registry.get_metadata(plugin_name)
    return PluginMetadataResponse(
        version=meta.version,
        author=meta.author,
        category=meta.category.value,
        tags=sorted(meta.tags),
        required_binaries=sorted(meta.required_binaries),
        description_long=meta.description_long,
        timeout_default_seconds=meta.timeout_default_seconds,
        timeout_max_seconds=meta.timeout_max_seconds,
    )


# --- Workflow Templates -------------------------------------------------------

@router.get(
    "/workflow-templates",
    response_model=WorkflowTemplateListResponse,
    summary="List all built-in workflow templates",
)
async def list_workflow_templates(
    _user: User = Depends(get_current_user),
) -> WorkflowTemplateListResponse:
    templates = list_builtin_templates()
    items = [
        WorkflowTemplateResponse(
            id=t.id,
            name=t.name,
            description=t.description,
            version=t.version,
            tags=sorted(t.tags),
            category=t.category,
            target_types=sorted(t.target_types),
            steps=[
                {
                    "id": s.id,
                    "plugin": s.plugin,
                    "name": s.name,
                    "depends_on": s.depends_on,
                }
                for s in t.get_enabled_steps()
            ],
        )
        for t in templates
    ]
    return WorkflowTemplateListResponse(items=items)


@router.get(
    "/workflow-templates/{template_id}",
    response_model=WorkflowTemplateResponse,
    summary="Get a workflow template by id",
)
async def get_workflow_template(
    template_id: str,
    _user: User = Depends(get_current_user),
) -> WorkflowTemplateResponse:
    from app.domain.builtin_templates import get_builtin_template

    template = get_builtin_template(template_id)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Template '{template_id}' not found",
        )
    return WorkflowTemplateResponse(
        id=template.id,
        name=template.name,
        description=template.description,
        version=template.version,
        tags=sorted(template.tags),
        category=template.category,
        target_types=sorted(template.target_types),
        steps=[
            {
                "id": s.id,
                "plugin": s.plugin,
                "name": s.name,
                "depends_on": s.depends_on,
            }
            for s in template.get_enabled_steps()
        ],
    )
