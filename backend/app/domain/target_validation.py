"""
Target value validation (SRS FR-3.1: "Targets can be IP, CIDR range,
domain, or URL").

Pure functions, zero I/O — validates that a target's string value is
shaped correctly for its declared `TargetType`. This is deliberately
separate from persistence so it can run identically in the API layer
(fail fast on bad input) and in any future plugin/scan validation path.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

from app.domain.exceptions import InvalidTargetValueError
from app.domain.value_objects import TargetType

_DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)"
    r"(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*\.[A-Za-z]{2,63}$"
)


def validate_target_value(value: str, target_type: TargetType) -> None:
    """
    Raise `InvalidTargetValueError` if `value` is not well-formed for
    `target_type`. Returns None (no exception) if valid.
    """
    value = value.strip()

    if target_type is TargetType.IP:
        try:
            ipaddress.ip_address(value)
        except ValueError as exc:
            raise InvalidTargetValueError(value, target_type.value) from exc

    elif target_type is TargetType.CIDR:
        try:
            ipaddress.ip_network(value, strict=False)
        except ValueError as exc:
            raise InvalidTargetValueError(value, target_type.value) from exc

    elif target_type is TargetType.DOMAIN:
        if not _DOMAIN_PATTERN.match(value):
            raise InvalidTargetValueError(value, target_type.value)

    elif target_type is TargetType.URL:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise InvalidTargetValueError(value, target_type.value)

    else:  # pragma: no cover - exhaustive over TargetType
        raise InvalidTargetValueError(value, str(target_type))
