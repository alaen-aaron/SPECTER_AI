# syntax=docker/dockerfile:1
# =============================================================================
# SPECTER_AI — executor service image (M7.1)
#
# The ONLY service that owns the Docker socket. Runs plugin commands in
# hardened ephemeral containers via the Docker SDK. Requires at runtime:
#   * /var/run/docker.sock  (docker socket)
#   * NET_ADMIN/NET_RAW/SYS_ADMIN capabilities + pid:host  (for nsenter-based
#     per-container network policy enforcement)
#   * the `specter-plugins:local` image built from ../Dockerfile.plugins
# =============================================================================

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /srv/executor

# nsenter (util-linux) enters the plugin container's network namespace;
# iptables installs the target-only egress rules into it.
RUN apt-get update && apt-get install -y --no-install-recommends \
        iptables \
        util-linux \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]