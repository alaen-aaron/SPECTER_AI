"""
Unit tests for the built-in plugins (Milestone 3).

Echo and Ping tests run the real subprocess (ping is available in this
environment and is fast/safe against loopback). Nmap tests focus on
config validation and command construction — the security-critical
part — with only a light real-execution smoke test, since nmap scans
take longer and aren't the point of these tests.
"""

from __future__ import annotations

import pytest

from app.domain.exceptions import InvalidPluginConfigError
from app.plugins.echo_plugin import EchoPlugin
from app.plugins.nmap_plugin import NmapPlugin
from app.plugins.ping_plugin import PingPlugin

# --- Echo -------------------------------------------------------------------


def test_echo_returns_expected_greeting() -> None:
    plugin = EchoPlugin()
    result = plugin.execute({}, timeout_seconds=5)
    assert result.success is True
    assert result.stdout == "Hello from SPECTER"
    assert result.exit_code == 0


def test_echo_accepts_any_config() -> None:
    plugin = EchoPlugin()
    plugin.validate_config({"anything": "goes", "ignored": 123})  # must not raise


def test_echo_metadata_names_which_plugin() -> None:
    plugin = EchoPlugin()
    assert plugin.name() == "echo"


# --- Ping ---------------------------------------------------------------------


def test_ping_validate_config_requires_hostname() -> None:
    plugin = PingPlugin()
    with pytest.raises(InvalidPluginConfigError):
        plugin.validate_config({})


def test_ping_validate_config_rejects_shell_metacharacters() -> None:
    plugin = PingPlugin()
    with pytest.raises(InvalidPluginConfigError):
        plugin.validate_config({"hostname": "127.0.0.1; rm -rf /"})


def test_ping_validate_config_rejects_command_substitution() -> None:
    plugin = PingPlugin()
    with pytest.raises(InvalidPluginConfigError):
        plugin.validate_config({"hostname": "$(whoami)"})


def test_ping_validate_config_accepts_valid_ip() -> None:
    plugin = PingPlugin()
    plugin.validate_config({"hostname": "127.0.0.1"})  # must not raise


def test_ping_validate_config_accepts_valid_domain() -> None:
    plugin = PingPlugin()
    plugin.validate_config({"hostname": "example.com"})  # must not raise


def test_ping_execute_against_loopback_succeeds() -> None:
    plugin = PingPlugin()
    result = plugin.execute({"hostname": "127.0.0.1"}, timeout_seconds=10)
    assert result.success is True
    assert result.exit_code == 0
    assert "127.0.0.1" in result.stdout


def test_ping_command_never_uses_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asserts the actual `subprocess.run` call site never passes `shell=True`."""
    import subprocess

    captured: dict[str, object] = {}
    real_run = subprocess.run

    def _spy_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["shell"] = kwargs.get("shell")
        captured["args"] = args[0] if args else kwargs.get("args")
        return real_run(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess, "run", _spy_run)
    plugin = PingPlugin()
    plugin.execute({"hostname": "127.0.0.1"}, timeout_seconds=10)

    assert captured["shell"] is False
    assert isinstance(captured["args"], list)


# --- Nmap ---------------------------------------------------------------------


def test_nmap_validate_config_requires_target() -> None:
    plugin = NmapPlugin()
    with pytest.raises(InvalidPluginConfigError):
        plugin.validate_config({})


def test_nmap_validate_config_accepts_valid_target_and_ports() -> None:
    plugin = NmapPlugin()
    plugin.validate_config({"target": "10.0.0.5", "ports": "1-1000"})  # must not raise


def test_nmap_validate_config_accepts_cidr_target() -> None:
    plugin = NmapPlugin()
    plugin.validate_config({"target": "10.0.0.0/24", "ports": "80"})  # must not raise


def test_nmap_validate_config_rejects_malformed_ports() -> None:
    plugin = NmapPlugin()
    with pytest.raises(InvalidPluginConfigError):
        plugin.validate_config({"target": "10.0.0.5", "ports": "22; rm -rf /"})


@pytest.mark.parametrize(
    "dangerous_argument",
    [
        "-oN",  # write named output file
        "-oX",  # write XML output file
        "-oG",  # write grepable output file
        "-oA",  # write all formats
        "--script",  # arbitrary NSE script execution
        "-iL",  # read targets from a file
        "-iR",  # random target selection
        "--script=whatever",
    ],
)
def test_nmap_rejects_dangerous_arguments(dangerous_argument: str) -> None:
    plugin = NmapPlugin()
    with pytest.raises(InvalidPluginConfigError):
        plugin.validate_config(
            {"target": "10.0.0.5", "ports": "80", "arguments": [dangerous_argument]}
        )


def test_nmap_accepts_allow_listed_arguments() -> None:
    plugin = NmapPlugin()
    plugin.validate_config(
        {"target": "10.0.0.5", "ports": "80", "arguments": ["-sV", "-Pn", "-T4"]}
    )  # must not raise


def test_nmap_rejects_non_string_arguments() -> None:
    plugin = NmapPlugin()
    with pytest.raises(InvalidPluginConfigError):
        plugin.validate_config({"target": "10.0.0.5", "ports": "80", "arguments": [123]})


def test_nmap_command_never_uses_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    captured: dict[str, object] = {}
    real_run = subprocess.run

    def _spy_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["shell"] = kwargs.get("shell")
        captured["args"] = args[0] if args else kwargs.get("args")
        return real_run(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess, "run", _spy_run)
    plugin = NmapPlugin()
    plugin.execute({"target": "127.0.0.1", "ports": "22", "arguments": ["-Pn"]}, timeout_seconds=15)

    assert captured["shell"] is False
    assert isinstance(captured["args"], list)


def test_nmap_execute_against_loopback_succeeds() -> None:
    plugin = NmapPlugin()
    result = plugin.execute(
        {"target": "127.0.0.1", "ports": "22", "arguments": ["-Pn"]}, timeout_seconds=15
    )
    assert result.exit_code == 0
    assert "127.0.0.1" in result.stdout or "localhost" in result.stdout
