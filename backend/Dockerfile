# syntax=docker/dockerfile:1
# =============================================================================
# SPECTER_AI — api / worker / beat image  (M5.5 hardening)
#
# Multi-stage build:
#   Stage 1 (go-builder): compile all Go-based security tools from source
#   Stage 2 (runtime):    Python 3.12 + system deps + tool binaries + app
#
# All 19 plugin binaries are available on PATH in the final image:
#   echo, ping, nmap, subfinder, httpx, dnsx, naabu, katana,
#   whatweb, sslscan, gobuster, nuclei, nikto, sqlmap, dalfox,
#   wpscan, ffuf, trufflehog, gitleaks
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1 — Compile Go-based security tools
# ---------------------------------------------------------------------------
FROM golang:1.24-bookworm AS go-builder

# Allow Go to auto-download newer toolchains when a module requires them
ENV GOTOOLCHAIN=auto

# naabu requires libpcap-dev for packet capture compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpcap-dev \
    && rm -rf /var/lib/apt/lists/*

# Pin tool versions for reproducibility.  Bump these ARGs when upgrading.
ARG SUBFINDER_VERSION=v2.6.7
ARG HTTPX_VERSION=v1.6.10
ARG DNSX_VERSION=v1.2.1
ARG NAABU_VERSION=v2.3.4
ARG KATANA_VERSION=v1.1.1
ARG NUCLEI_VERSION=v3.3.7
ARG GOBUSTER_VERSION=v3.6.0
ARG DALFOX_VERSION=v2.13.0
ARG FFUF_VERSION=v2.1.0
ARG GITLEAKS_VERSION=v8.30.1
ARG TRUFFLEHOG_VERSION=v3.95.9

# Build each tool separately for better cache isolation and error reporting.
RUN go install -v github.com/projectdiscovery/subfinder/v2/cmd/subfinder@${SUBFINDER_VERSION}
RUN go install -v github.com/projectdiscovery/httpx/cmd/httpx@${HTTPX_VERSION}
RUN go install -v github.com/projectdiscovery/dnsx/cmd/dnsx@${DNSX_VERSION}
RUN go install -v github.com/projectdiscovery/naabu/v2/cmd/naabu@${NAABU_VERSION}
RUN go install -v github.com/projectdiscovery/katana/cmd/katana@${KATANA_VERSION}
RUN go install -v github.com/projectdiscovery/nuclei/v3/cmd/nuclei@${NUCLEI_VERSION}
RUN go install -v github.com/OJ/gobuster/v3@${GOBUSTER_VERSION}
RUN go install -v github.com/hahwul/dalfox/v2@${DALFOX_VERSION}
RUN go install -v github.com/ffuf/ffuf/v2@${FFUF_VERSION}
RUN go install -v github.com/zricethezav/gitleaks/v8@${GITLEAKS_VERSION}
# trufflehog can't be go-installed (replace directives in go.mod); download prebuilt binary
RUN curl -sSfL "https://github.com/trufflesecurity/trufflehog/releases/download/${TRUFFLEHOG_VERSION}/trufflehog_${TRUFFLEHOG_VERSION#v}_linux_amd64.tar.gz" \
    | tar xz -C /tmp trufflehog \
    && mv /tmp/trufflehog /go/bin/trufflehog

# ---------------------------------------------------------------------------
# Stage 2 — Runtime image
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# ── System packages ─────────────────────────────────────────────────────────
# build-essential: needed for some pip packages (asyncpg, etc.)
# curl:            healthcheck
# iputils-ping:    ping plugin
# nmap:            nmap plugin
# sslscan:         sslscan plugin (Debian package)
# ruby + dev:      whatweb, wpscan
# perl:            nikto
# git:             cloning nikto + whatweb repos
# ca-certificates: HTTPS for tool downloads
# procps:          nikto /proc checks
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        git \
        iputils-ping \
        libcurl4-openssl-dev \
        libxml2-dev \
        libxslt1-dev \
        nmap \
        perl \
        procps \
        ruby \
        ruby-dev \
        sslscan \
        wget \
    && rm -rf /var/lib/apt/lists/*

# ── Copy Go binaries from builder stage ─────────────────────────────────────
COPY --from=go-builder /go/bin/subfinder  /usr/local/bin/subfinder
COPY --from=go-builder /go/bin/httpx      /usr/local/bin/httpx
COPY --from=go-builder /go/bin/dnsx       /usr/local/bin/dnsx
COPY --from=go-builder /go/bin/naabu      /usr/local/bin/naabu
COPY --from=go-builder /go/bin/katana     /usr/local/bin/katana
COPY --from=go-builder /go/bin/nuclei     /usr/local/bin/nuclei
COPY --from=go-builder /go/bin/gobuster   /usr/local/bin/gobuster
COPY --from=go-builder /go/bin/dalfox     /usr/local/bin/dalfox
COPY --from=go-builder /go/bin/ffuf      /usr/local/bin/ffuf
COPY --from=go-builder /go/bin/gitleaks   /usr/local/bin/gitleaks
COPY --from=go-builder /go/bin/trufflehog /usr/local/bin/trufflehog

# ── WhatWeb (Ruby) ──────────────────────────────────────────────────────────
ARG WHATWEB_VERSION=0.5.5
RUN git clone --depth 1 --branch v${WHATWEB_VERSION} \
        https://github.com/urbanadventurer/WhatWeb.git /opt/whatweb \
    && ln -s /opt/whatweb/whatweb /usr/local/bin/whatweb \
    && chmod +x /opt/whatweb/whatweb

# ── Nikto (Perl) ────────────────────────────────────────────────────────────
RUN git clone --depth 1 https://github.com/sullo/nikto.git /opt/nikto \
    && ln -s /opt/nikto/program/nikto.pl /usr/local/bin/nikto \
    && chmod +x /opt/nikto/program/nikto.pl

# ── sqlmap (Python) ─────────────────────────────────────────────────────────
ARG SQLMAP_VERSION=1.8
RUN pip install --no-cache-dir sqlmap==${SQLMAP_VERSION} \
    && ln -s $(python -c "import shutil; print(shutil.which('sqlmap'))" 2>/dev/null \
              || echo /usr/local/bin/sqlmap) /usr/local/bin/sqlmap \
    || true
# Fallback: ensure sqlmap is on PATH even if the symlink trick fails
RUN if ! command -v sqlmap >/dev/null 2>&1; then \
        git clone --depth 1 --branch sqlmap-${SQLMAP_VERSION} \
            https://github.com/sqlmapproject/sqlmap.git /opt/sqlmap \
        && ln -s /opt/sqlmap/sqlmap.py /usr/local/bin/sqlmap \
        && chmod +x /opt/sqlmap/sqlmap.py; \
    fi

# ── WPScan (Ruby gem) ──────────────────────────────────────────────────────
RUN gem install wpscan --no-document

# ── Minimal wordlist for gobuster / ffuf ────────────────────────────────────
RUN mkdir -p /usr/share/wordlists && \
    printf 'admin\napi\nbackup\nconfig\ndashboard\ndb\ndev\ndocs\nfavicon.ico\n' > \
        /usr/share/wordlists/common.txt && \
    printf 'admin\napi\nbackup\nconfig\ndashboard\nimages\njs\nlogin\n' >> \
        /usr/share/wordlists/common.txt && \
    printf 'css\nfonts\nimg\nlib\nmedia\nprivate\npublic\nstatic\n' >> \
        /usr/share/wordlists/common.txt && \
    printf 'test\nuploads\nuser\nusers\nweb\nwp-admin\nwp-content\nwp-includes\n' >> \
        /usr/share/wordlists/common.txt && \
    printf 'xmlrpc.php\nreadme.html\nlicense.txt\n.htaccess\nweb.config\n' >> \
        /usr/share/wordlists/common.txt

# ── Python application ──────────────────────────────────────────────────────
COPY pyproject.toml README.md ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
COPY tests ./tests

RUN pip install --upgrade pip && pip install -e ".[dev]"

# The Python `httpx` package ships a console-script also named `httpx`, which
# overwrites the ProjectDiscovery Go binary installed above. Re-copy the Go
# binary AFTER pip install so `httpx` resolves to ProjectDiscovery's tool
# (required by the httpx plugin); the Python package stays importable as a lib.
COPY --from=go-builder /go/bin/httpx /usr/local/bin/httpx

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
