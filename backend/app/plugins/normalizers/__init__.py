"""
Built-in tool output normalizers (Milestone 4A).

Importing this module registers every built-in normalizer onto
`app.plugins.normalizer_registry.normalizer_registry`. Call once at
application/worker startup alongside `app.plugins.builtin`.
"""

from __future__ import annotations

from app.plugins.normalizer_registry import normalizer_registry
from app.plugins.normalizers.nmap_normalizer import NmapNormalizer
from app.plugins.normalizers.ping_normalizer import PingNormalizer

normalizer_registry.register(PingNormalizer())
normalizer_registry.register(NmapNormalizer())
