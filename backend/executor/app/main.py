"""
SPECTER_AI executor service — FastAPI app.

The ONLY service in the stack that holds the Docker socket. Exposes a
single endpoint that accepts an already-validated plugin command and
runs it in a hardened ephemeral container. API/worker/beat never talk to
Docker directly; they call this service.
"""

from __future__ import annotations

import logging

import docker
import structlog
from fastapi import Depends, FastAPI

from app.config import ExecutorSettings, get_settings
from app.container_runner import ContainerRunner
from app.models import ExecuteRequest, ExecuteResponse, HealthResponse

app = FastAPI(title="SPECTER_AI Executor", version="0.1.0")


def _setup_logging(settings: ExecutorSettings) -> None:
    logging.basicConfig(level=settings.LOG_LEVEL)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
        )
    )


def get_runner(settings: ExecutorSettings = Depends(get_settings)) -> ContainerRunner:
    return ContainerRunner(settings)


@app.on_event("startup")
def _startup() -> None:
    _setup_logging(get_settings())


@app.get("/health", response_model=HealthResponse)
def health(settings: ExecutorSettings = Depends(get_settings)) -> HealthResponse:
    try:
        client = docker.from_env()
        client.ping()
        docker_connected = True
    except Exception:  # noqa: BLE001 - health endpoint reports, never raises
        docker_connected = False

    image_present = False
    if docker_connected:
        try:
            client.images.get(settings.PLUGIN_IMAGE)
            image_present = True
        except Exception:  # noqa: BLE001
            image_present = False

    return HealthResponse(
        status="ok",
        plugin_image=settings.PLUGIN_IMAGE,
        plugin_image_present=image_present,
        docker_connected=docker_connected,
        network_policy=settings.NETWORK_POLICY,
    )


@app.post("/v1/executions", response_model=ExecuteResponse)
def execute(
    request: ExecuteRequest, runner: ContainerRunner = Depends(get_runner)
) -> ExecuteResponse:
    return runner.execute(request)