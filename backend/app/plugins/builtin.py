"""
Importing this module registers every built-in plugin onto
`app.plugins.registry.registry`. Call `import app.plugins.builtin`
once at application/worker startup (see `app/main.py` and the Celery
app module) — nothing else needs to know these plugins exist.

Adding a new built-in plugin means: write the plugin module, then add
one line here.
"""

from __future__ import annotations

from app.plugins.dalfox_plugin import DalfoxPlugin
from app.plugins.dnsx_plugin import DnsxPlugin
from app.plugins.echo_plugin import EchoPlugin
from app.plugins.ffuf_plugin import FfufPlugin
from app.plugins.gitleaks_plugin import GitleaksPlugin
from app.plugins.gobuster_plugin import GobusterPlugin
from app.plugins.httpx_plugin import HttpxPlugin
from app.plugins.katana_plugin import KatanaPlugin
from app.plugins.naabu_plugin import NaabuPlugin
from app.plugins.nikto_plugin import NiktoPlugin
from app.plugins.nmap_plugin import NmapPlugin
from app.plugins.nuclei_plugin import NucleiPlugin
from app.plugins.ping_plugin import PingPlugin
from app.plugins.registry import registry
from app.plugins.sqlmap_plugin import SqlmapPlugin
from app.plugins.sslscan_plugin import SslscanPlugin
from app.plugins.subfinder_plugin import SubfinderPlugin
from app.plugins.trufflehog_plugin import TrufflehogPlugin
from app.plugins.whatweb_plugin import WhatwebPlugin
from app.plugins.wpscan_plugin import WpscanPlugin

# Core
registry.register(EchoPlugin())
registry.register(PingPlugin())
registry.register(NmapPlugin())

# Reconnaissance / Discovery
registry.register(SubfinderPlugin())
registry.register(HttpxPlugin())
registry.register(DnsxPlugin())
registry.register(NaabuPlugin())
registry.register(KatanaPlugin())

# Information Gathering / Scanning
registry.register(WhatwebPlugin())
registry.register(SslscanPlugin())

# Enumeration
registry.register(GobusterPlugin())

# Vulnerability Scanning
registry.register(NucleiPlugin())
registry.register(NiktoPlugin())
registry.register(SqlmapPlugin())
registry.register(DalfoxPlugin())
registry.register(WpscanPlugin())
registry.register(FfufPlugin())

# Secret/Credential Scanning
registry.register(TrufflehogPlugin())
registry.register(GitleaksPlugin())
