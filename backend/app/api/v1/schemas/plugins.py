"""
Plugin and workflow template API schemas (Milestone 5).

Pydantic models for plugin discovery, health checks, compatibility,
and workflow template responses.
"""

from __future__ import annotations

from pydantic import BaseModel


class PluginResponse(BaseModel):
    """Plugin listing item."""

    name: str
    description: str
    category: str
    version: str
    tags: list[str]


class PluginListResponse(BaseModel):
    """List of plugins."""

    items: list[PluginResponse]


class PluginCapabilityResponse(BaseModel):
    """Plugin capability declaration."""

    input_asset_types: list[str]
    output_asset_types: list[str]
    produces_findings: bool
    requires_host: bool
    requires_open_ports: bool
    max_targets: int | None


class PluginMetadataResponse(BaseModel):
    """Extended plugin metadata."""

    version: str
    author: str
    category: str
    tags: list[str]
    required_binaries: list[str]
    description_long: str
    timeout_default_seconds: int
    timeout_max_seconds: int


class PluginHealthCheckResponse(BaseModel):
    """Health status of all plugins."""

    healthy: list[str]
    unhealthy: list[str]
    total: int


class WorkflowCompatibilityResponse(BaseModel):
    """Compatibility check between two plugins."""

    upstream: str
    downstream: str
    is_compatible: bool
    reason: str


class WorkflowTemplateStepResponse(BaseModel):
    """A step in a workflow template."""

    id: str
    plugin: str
    name: str
    depends_on: list[str]


class WorkflowTemplateResponse(BaseModel):
    """Workflow template details."""

    id: str
    name: str
    description: str
    version: str
    tags: list[str]
    category: str
    target_types: list[str]
    steps: list[WorkflowTemplateStepResponse]


class WorkflowTemplateListResponse(BaseModel):
    """List of workflow templates."""

    items: list[WorkflowTemplateResponse]
