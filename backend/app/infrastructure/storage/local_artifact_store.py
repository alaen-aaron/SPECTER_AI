"""
Local filesystem artifact store (Milestone 3).

Per this milestone's spec: "Database stores metadata. Artifacts stored
on disk." One directory per scan, under `SCAN_ARTIFACTS_DIR`. This is
intentionally simple local storage, not the SRS's longer-term MinIO/S3
evidence store (SRS §2.9/§16.4) — that remains the target for a later
hardening milestone once multi-worker/multi-node deployment makes a
shared object store necessary; a single-node Docker Compose deployment
(this project's default per SRS §12.1) has no such requirement yet.

Filenames are fully engine-controlled (`stdout.log`, `stderr.log`) —
nothing from plugin config or user input ever contributes to a path,
which is what makes path traversal via a crafted target/config value
structurally impossible here, not just filtered.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID


class LocalArtifactStore:
    def __init__(self, base_dir: str) -> None:
        self._base_dir = Path(base_dir)

    def scan_directory(self, scan_id: UUID) -> Path:
        directory = self._base_dir / str(scan_id)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def write_logs(self, scan_id: UUID, stdout: str, stderr: str) -> str:
        """Writes stdout/stderr to fixed, engine-controlled filenames and
        returns the scan's log directory path (stored as `logs_path`)."""
        directory = self.scan_directory(scan_id)
        (directory / "stdout.log").write_text(stdout, encoding="utf-8")
        (directory / "stderr.log").write_text(stderr, encoding="utf-8")
        return str(directory)

    def artifacts_directory_if_any(self, scan_id: UUID) -> str | None:
        """
        Returns the scan's artifacts directory path if it exists and is
        non-empty, else None — a scan with no plugin-produced artifacts
        (e.g. `echo`, `ping`) should have `artifacts_path = NULL`, not a
        pointer to an empty directory.
        """
        directory = self._base_dir / str(scan_id) / "artifacts"
        if directory.is_dir() and any(directory.iterdir()):
            return str(directory)
        return None

    def artifacts_directory(self, scan_id: UUID) -> Path:
        """Directory a plugin may write named artifact files into. Created
        on demand; callers decide whether anything was actually written."""
        directory = self._base_dir / str(scan_id) / "artifacts"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def read_log_file(self, scan_id: UUID, filename: str) -> str | None:
        """Reads back a previously-written log file, or None if absent.
        `filename` is always one of the fixed names this class itself
        writes (`stdout.log`/`stderr.log`) — never derived from request input."""
        if filename not in {"stdout.log", "stderr.log"}:
            return None
        path = self._base_dir / str(scan_id) / filename
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")


def ensure_base_directory_exists(base_dir: str) -> None:
    os.makedirs(base_dir, exist_ok=True)
