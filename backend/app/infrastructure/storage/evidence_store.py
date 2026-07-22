"""
Content-addressed evidence store (SRS §2.9/§9.2).

Stores evidence files named by their SHA-256 hash, making them
inherently deduplicated and immutable. DB stores the pointer (hash
path) and metadata, never the bytes themselves.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from app.domain.entities import Evidence
from app.domain.value_objects import EvidenceType


def _extension_for_type(evidence_type: str) -> str:
    mapping = {
        "screenshot": "png",
        "raw_log": "log",
        "session_recording": "webm",
        "request_response": "json",
    }
    return mapping.get(evidence_type, "bin")


class EvidenceStore:
    def __init__(self, base_dir: str) -> None:
        self._base_dir = Path(base_dir)

    def _content_hash(self, content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def _store_path(
        self, finding_id: UUID, content_hash: str, ext: str
    ) -> Path:
        prefix = str(finding_id)[:2]
        directory = self._base_dir / prefix
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{content_hash}.{ext}"

    def store(
        self,
        finding_id: UUID,
        content: bytes,
        evidence_type: str,
        filename: str | None,
        collected_by: UUID,
    ) -> Evidence:
        content_hash = self._content_hash(content)
        ext = _extension_for_type(evidence_type)
        path = self._store_path(finding_id, content_hash, ext)
        path.write_bytes(content)

        now = datetime.now(UTC)
        return Evidence(
            id=uuid4(),
            finding_id=finding_id,
            evidence_type=EvidenceType(evidence_type),
            storage_pointer=str(path),
            content_hash=content_hash,
            collected_by=collected_by,
            collected_at=now,
            filename=filename,
            file_size=len(content),
            created_at=now,
        )

    def retrieve(self, content_hash: str, finding_id: UUID) -> bytes | None:
        path = self.get_path(content_hash, finding_id)
        if path is None or not path.is_file():
            return None
        return path.read_bytes()

    def get_path(self, content_hash: str, finding_id: UUID) -> Path | None:
        prefix = str(finding_id)[:2]
        directory = self._base_dir / prefix
        if not directory.is_dir():
            return None
        for candidate in directory.glob(f"{content_hash}.*"):
            if candidate.is_file():
                return candidate
        return None
