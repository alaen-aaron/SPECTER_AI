"""
Request/response schemas for the executor API.

The executor accepts an already-validated plugin command (list args, no
shell) plus the resource/network constraints, and returns the captured
stdout/stderr/exit code. It never re-validates plugin configs — that is
the API/worker layer's job (allow-lists, scope). The command is executed
verbatim as list arguments inside the ephemeral container.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ExecuteRequest(BaseModel):
    execution_id: str = Field(min_length=8, max_length=64)
    command: list[str] = Field(min_length=1)
    targets: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=120, ge=1, le=600)
    image: str = "specter-plugins:local"
    cpu_limit: float = Field(default=1.0, gt=0, le=16)
    memory_limit: str = "512m"
    capture_artifacts: bool = False


class ArtifactFile(BaseModel):
    name: str
    data: str


class ExecuteResponse(BaseModel):
    execution_id: str
    status: str = "error"  # completed | failed | timed_out | error
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    error: str | None = None
    container_id: str | None = None
    network_policy: dict[str, object] = Field(default_factory=dict)
    artifacts: list[ArtifactFile] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str = "ok"
    plugin_image: str
    plugin_image_present: bool
    docker_connected: bool
    network_policy: str