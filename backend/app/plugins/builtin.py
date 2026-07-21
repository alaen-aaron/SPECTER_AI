"""
Importing this module registers every built-in plugin onto
`app.plugins.registry.registry`. Call `import app.plugins.builtin`
once at application/worker startup (see `app/main.py` and the Celery
app module) — nothing else needs to know these three plugins exist.

Adding a new built-in plugin means: write the plugin module, then add
one line here.
"""

from __future__ import annotations

from app.plugins.echo_plugin import EchoPlugin
from app.plugins.nmap_plugin import NmapPlugin
from app.plugins.ping_plugin import PingPlugin
from app.plugins.registry import registry

registry.register(EchoPlugin())
registry.register(PingPlugin())
registry.register(NmapPlugin())
