"""
Target-only network policy enforcement (SRS §7.3).

Each plugin container runs on its own ephemeral Docker bridge network,
and its egress is restricted by installing iptables rules *inside the
container's own network namespace* via `nsenter`. Only the declared
target addresses (plus loopback, Docker's embedded DNS, and established
connection return traffic) are permitted; everything else is dropped.

This is the infrastructure-level enforcement of the Scope Guard: even a
bug in the API/worker-layer allow-list check cannot give the plugin
container unrestricted egress, because the container's OUTPUT chain
defaults to DROP. If the rules cannot be installed, the executor fails
closed (the command is never run with unrestricted network access).

The executor container must run with `pid: host` (so it can see the
plugin container's host PID and `nsenter` into its namespace) and carry
`NET_ADMIN`/`NET_RAW`/`SYS_ADMIN` capabilities. The plugin containers
themselves get none of those.
"""

from __future__ import annotations

import ipaddress
import socket
import subprocess
from dataclasses import dataclass, field

_DOCKER_EMBEDDED_DNS = "127.0.0.11"


class NetworkPolicyError(RuntimeError):
    """Raised when target-only egress cannot be enforced (fail closed)."""


@dataclass(frozen=True, slots=True)
class NetworkPolicy:
    policy: str = "target-only"
    allowed_addresses: tuple[str, ...] = ()
    rules_applied: int = 0
    details: dict[str, object] = field(default_factory=dict)


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _is_cidr(value: str) -> bool:
    try:
        ipaddress.ip_network(value, strict=False)
        return True
    except ValueError:
        return False


def _resolve_host(value: str) -> list[str]:
    """Resolve a hostname to its A-record IPs (fail closed on error)."""
    try:
        infos = socket.getaddrinfo(value, None, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise NetworkPolicyError(
            f"cannot resolve target hostname '{value}' for network policy: {exc}"
        ) from None
    return sorted({info[4][0] for info in infos})


def expand_target_addresses(targets: list[str]) -> list[str]:
    """
    Expand declared targets (IP, CIDR, or domain) into the IP/CIDR
    addresses that the plugin container is allowed to reach.
    """
    allowed: list[str] = []
    for target in targets:
        if _is_ip(target) or _is_cidr(target):
            allowed.append(target)
        else:
            allowed.extend(_resolve_host(target))
    return allowed


def apply_target_only_policy(container_pid: int, allowed_addresses: list[str]) -> NetworkPolicy:
    """
    Install target-only OUTPUT rules into the plugin container's network
    namespace. Raises `NetworkPolicyError` if any rule cannot be applied —
    the caller must then refuse to run the command (fail closed).

    Rule order is load-bearing: accept loopback, established/related
    return traffic, and Docker's embedded DNS first; then the declared
    targets; and finally an unconditional DROP as default-deny.
    """
    rules: list[tuple[str, str]] = [
        ("OUTPUT", "-o lo -j ACCEPT"),
        ("OUTPUT", "-m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT"),
        ("OUTPUT", f"-d {_DOCKER_EMBEDDED_DNS} -p udp --dport 53 -j ACCEPT"),
        ("OUTPUT", f"-d {_DOCKER_EMBEDDED_DNS} -p tcp --dport 53 -j ACCEPT"),
    ]
    for address in allowed_addresses:
        rules.append(("OUTPUT", f"-d {address} -j ACCEPT"))
    rules.append(("OUTPUT", "-j DROP"))

    applied: list[str] = []
    for chain, rule in rules:
        result = subprocess.run(
            ["nsenter", "-t", str(container_pid), "-n", "iptables", "-A", chain, *rule.split()],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            raise NetworkPolicyError(
                f"failed to install network policy rule '{chain} {rule}': {result.stderr.strip()}"
            )
        applied.append(f"{chain} {rule}")

    return NetworkPolicy(
        policy="target-only",
        allowed_addresses=tuple(allowed_addresses),
        rules_applied=len(applied),
        details={"rules": applied},
    )