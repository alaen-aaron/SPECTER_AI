"""
Gitleaks plugin — secret/credential scanner for git repositories.

Runs `gitleaks detect --source <clone_path>` to scan a git repository
for leaked secrets. This scans a git repo URL, not a live host.
"""

from __future__ import annotations

from typing import Any

from app.domain.exceptions import InvalidPluginConfigError
from app.plugins.base import PluginCapability, PluginCategory, PluginMetadata, PluginResult
from app.plugins.subprocess_base import SubprocessPlugin

_ALLOWED_FLAGS: frozenset[str] = frozenset(
    {
        "--report-format",
        "--no-banner",
        "--verbose",
    }
)


class GitleaksPlugin(SubprocessPlugin):
    """Secret/credential scanner for git repositories using gitleaks."""

    def name(self) -> str:
        return "gitleaks"

    def description(self) -> str:
        return (
            "Scans git repositories for leaked secrets, API keys, "
            "tokens, and other credentials using pattern matching."
        )

    def validate_config(self, config: dict[str, Any]) -> None:
        repo_url = self._validate_required_field(config, "repo_url")

        if not isinstance(repo_url, str) or not repo_url.strip():
            raise InvalidPluginConfigError(
                self.name(), "'repo_url' must be a non-empty string"
            )

        self._validate_flag_list(config, "flags", _ALLOWED_FLAGS)

        branch = config.get("branch")
        if branch is not None and (not isinstance(branch, str) or not branch.strip()):
            raise InvalidPluginConfigError(
                self.name(), "'branch' must be a non-empty string if provided"
            )

    def execute(self, config: dict[str, Any], timeout_seconds: int) -> PluginResult:
        repo_url = str(config["repo_url"])
        branch = config.get("branch", "main")
        flags: list[str] = list(config.get("flags", []))

        command = [
            "gitleaks",
            "detect",
            "--source",
            repo_url,
            "--report-format",
            "json",
            "--no-banner",
            *flags,
        ]

        if branch:
            command.extend(["--branch", str(branch)])

        return self._execute_subprocess(
            command,
            timeout_seconds,
            target=repo_url,
            extra_metadata={"repo_url": repo_url, "branch": branch},
        )

    def capability(self) -> PluginCapability:
        return PluginCapability(
            input_asset_types=frozenset({"url"}),
            output_asset_types=frozenset({"credential"}),
            produces_findings=True,
            requires_host=False,
            requires_open_ports=False,
        )

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            version="1.0.0",
            author="SPECTER Team",
            category=PluginCategory.VULNERABILITY,
            tags=frozenset({"secrets", "credentials", "git"}),
            required_binaries=frozenset({"gitleaks"}),
            description_long="Find and audit secrets in git repositories.",
            timeout_default_seconds=180,
            timeout_max_seconds=600,
        )

    def supports_target_type(self, target_type: str) -> bool:
        return target_type == "url"
