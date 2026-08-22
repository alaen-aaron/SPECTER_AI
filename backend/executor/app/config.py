"""
Executor service configuration.

Environment-derived settings for the isolated plugin executor. The
executor is the ONLY SPECTER_AI service that talks to the Docker daemon
(owns the docker socket); the API, worker and beat never do.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class ExecutorSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    APP_NAME: str = "SPECTER_EXECUTOR"
    LOG_LEVEL: str = "INFO"

    # Hardened image that plugins run inside (built from Dockerfile.plugins).
    PLUGIN_IMAGE: str = "specter-plugins:local"

    # Default resource limits applied to every ephemeral plugin container.
    DEFAULT_CPU_LIMIT: float = 1.0
    DEFAULT_MEMORY_LIMIT: str = "512m"
    DEFAULT_TIMEOUT_SECONDS: int = 120
    MAX_TIMEOUT_SECONDS: int = 600

    # "target-only": plugin container egress is restricted (iptables, installed
    # into the container's network namespace per task) to the declared target
    # addresses and nothing else. "none": no egress restriction — DEV ONLY,
    # never use in a real deployment.
    NETWORK_POLICY: str = "target-only"

    # Writable scratch inside the read-only-rootfs plugin container.
    TMP_TMPFS_SIZE: str = "256m"
    OUTPUT_TMPFS_SIZE: str = "256m"

    # Container cleanup grace after the command completes.
    CLEANUP_GRACE_SECONDS: int = 15


@lru_cache(maxsize=1)
def get_settings() -> ExecutorSettings:
    return ExecutorSettings()