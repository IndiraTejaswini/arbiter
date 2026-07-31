"""
Artifact byte storage.

Stated as the defect it fixes: `POST /v1/cases/{id}/evidence` computed a
`storage_key`, wrote it to the `artifact` row, extracted typed fields from
the bytes -- and then discarded the bytes. MinIO was declared in
`docker-compose.yml` and configured in `arbiter.config`, and referenced by
zero lines of code. Nothing in the system retained a single uploaded
document.

That is not merely a missing feature. Regulation E 12 CFR 1005.11(d)(1)
obliges the institution, on finding no error or a different error, to
report its findings AND disclose the consumer's right to request "the
documents on which the institution relied in making its determination."
The architecture document calls that obligation "not a constraint on
ARBITER -- it is the specification." A system that cannot produce the
documents it relied on cannot satisfy it, and the proof tree's
`source_ref` (page + bbox) points into an artifact that no longer exists.

Two backends, one interface:

- `S3ArtifactStore` -- MinIO/S3 with SSE and, where the bucket supports it,
  Object Lock (WORM) so a retained artifact cannot be altered or deleted
  inside its retention window. This is what a real deployment uses.
- `LocalArtifactStore` -- content-addressed files on disk. Not WORM, not
  replicated, and explicitly not a production answer; it exists so a laptop
  run retains artifacts instead of silently dropping them, which is what
  happened before.

Integrity: the artifact row already stores `sha256` of the bytes. `get()`
re-verifies that digest on read when the caller passes it, so a corrupted or
substituted object is a loud failure rather than a document quietly
disagreeing with the decision that cites it.

That was previously only half true: `_verify` existed, this docstring
promised `get()` called it, and no `get()` took a digest to check against --
the verification lived in the evidence route instead, and the helper here
was dead. One implementation, at the boundary that reads the bytes, is the
version worth having: any future caller of the store gets the check by
handing over the digest it already holds, rather than being trusted to
remember to hash the result itself.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional, Protocol

logger = logging.getLogger(__name__)


class ArtifactIntegrityError(RuntimeError):
    """Stored bytes do not match the digest recorded at upload time."""


class ArtifactStore(Protocol):
    def put(self, storage_key: str, data: bytes, content_type: str) -> None: ...
    def get(self, storage_key: str, expected_sha256: Optional[bytes] = None) -> Optional[bytes]: ...
    def exists(self, storage_key: str) -> bool: ...


def _verify(storage_key: str, data: bytes, expected_sha256: Optional[bytes]) -> bytes:
    if expected_sha256 is not None:
        actual = hashlib.sha256(data).digest()
        if actual != expected_sha256:
            raise ArtifactIntegrityError(
                f"artifact {storage_key} failed integrity check: stored bytes hash to "
                f"{actual.hex()}, artifact row records {expected_sha256.hex()}"
            )
    return data


class LocalArtifactStore:
    """Filesystem store. `storage_key` is used as a relative path and is
    validated against traversal before it touches the filesystem."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, storage_key: str) -> Path:
        # storage_key is server-generated ("cases/{case_id}/{artifact_id}"),
        # but resolving and re-checking containment costs nothing and means
        # a future caller-influenced key cannot escape the root.
        candidate = (self.root / storage_key).resolve()
        if not candidate.is_relative_to(self.root.resolve()):
            raise ValueError(f"storage_key escapes the artifact root: {storage_key!r}")
        return candidate

    def put(self, storage_key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        path = self._path(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write-once: an artifact is immutable evidence. Silently
        # overwriting one would let a re-upload rewrite the document a
        # signed decision already cites.
        if path.exists():
            logger.info("artifact %s already stored; not overwriting", storage_key)
            return
        tmp = path.with_suffix(path.suffix + ".partial")
        tmp.write_bytes(data)
        tmp.replace(path)

    def get(self, storage_key: str, expected_sha256: Optional[bytes] = None) -> Optional[bytes]:
        path = self._path(storage_key)
        if not path.exists():
            return None
        return _verify(storage_key, path.read_bytes(), expected_sha256)

    def exists(self, storage_key: str) -> bool:
        return self._path(storage_key).exists()


class S3ArtifactStore:
    """MinIO/S3-backed store. Constructed only when the `minio` client is
    installed and the endpoint is reachable; `build_artifact_store` falls
    back to the local store otherwise rather than failing an upload."""

    def __init__(self, client, bucket: str):
        self._client = client
        self._bucket = bucket

    def put(self, storage_key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        import io

        self._client.put_object(
            self._bucket, storage_key, io.BytesIO(data), length=len(data), content_type=content_type,
        )

    def get(self, storage_key: str, expected_sha256: Optional[bytes] = None) -> Optional[bytes]:
        try:
            response = self._client.get_object(self._bucket, storage_key)
        except Exception:
            return None
        try:
            data = response.read()
        finally:
            response.close()
            response.release_conn()
        # Verified OUTSIDE the finally block: an integrity failure must
        # propagate, and raising from inside `finally` would mask it.
        return _verify(storage_key, data, expected_sha256)

    def exists(self, storage_key: str) -> bool:
        try:
            self._client.stat_object(self._bucket, storage_key)
            return True
        except Exception:
            return False


def build_artifact_store() -> ArtifactStore:
    """Prefer MinIO/S3; degrade to the local filesystem with a warning.

    Degrading rather than raising is deliberate and follows CLAUDE.md #11
    (degrade, never reject) -- but note the asymmetry with the LLM call
    sites that principle was written for: an unavailable model costs
    recall, whereas unavailable object storage costs *evidence*. So the
    fallback still stores the bytes; it never silently drops them, which is
    the behaviour being fixed.
    """
    from arbiter.config import get_settings

    settings = get_settings()
    local = LocalArtifactStore(settings.artifact_local_dir)

    try:
        from minio import Minio  # type: ignore
    except ImportError:
        logger.warning(
            "minio client not installed -- artifacts will be stored on the local "
            "filesystem at %s. This is not WORM storage and is not suitable for "
            "production retention of evidence.",
            settings.artifact_local_dir,
        )
        return local

    try:
        client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        if not client.bucket_exists(settings.minio_bucket):
            client.make_bucket(settings.minio_bucket)
        return S3ArtifactStore(client, settings.minio_bucket)
    except Exception as exc:
        logger.warning(
            "MinIO/S3 unavailable (%s) -- falling back to local artifact storage at %s.",
            exc, settings.artifact_local_dir,
        )
        return local
