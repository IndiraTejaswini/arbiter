"""
Step 1-3 of the ingest quarantine: size cap, magic-byte sniffing, allowlist.
Never trusts Content-Type or the filename extension -- CLAUDE.md's DB
comment on `artifact.mime_type` ("SNIFFED. Never from Content-Type.") is
enforced right here, the only place a mime type is ever assigned.
"""

from __future__ import annotations

from arbiter.config import get_settings

from .schemas import ScanResult

_ALLOWED_MIME_TYPES = {"application/pdf", "image/png", "image/jpeg"}

# Magic-byte signatures, checked directly -- no dependency on python-magic
# being correctly installed/available cross-platform is required for this
# minimal, security-relevant check; python-magic (if present) is used for a
# stronger check afterward in forensics.py, but the allowlist gate itself
# must not silently no-op if that optional dependency is missing.
_SIGNATURES: dict[bytes, str] = {
    b"%PDF-": "application/pdf",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
}


def sniff_mime_type(head: bytes) -> str | None:
    for sig, mime in _SIGNATURES.items():
        if head.startswith(sig):
            return mime
    return None


def scan_artifact(artifact_id: str, data: bytes) -> ScanResult:
    settings = get_settings()
    size = len(data)

    if size > settings.max_artifact_bytes:
        return ScanResult(artifact_id=artifact_id, accepted=False,
                           reason=f"artifact exceeds {settings.max_artifact_bytes} bytes", byte_size=size)
    if size == 0:
        return ScanResult(artifact_id=artifact_id, accepted=False, reason="empty artifact", byte_size=0)

    mime = sniff_mime_type(data[:16])
    if mime is None:
        return ScanResult(artifact_id=artifact_id, accepted=False,
                           reason="magic bytes did not match any allowed type", byte_size=size)
    if mime not in _ALLOWED_MIME_TYPES:
        return ScanResult(artifact_id=artifact_id, accepted=False,
                           reason=f"sniffed type {mime} not in allowlist", sniffed_mime_type=mime, byte_size=size)

    # Malware scan (ClamAV in production) -- stubbed in dev per the build
    # spec's own Phase-1 scope ("ClamAV; stub in dev"). Documented here
    # rather than silently omitted: this is the one boundary check this
    # build does NOT perform for real.
    return ScanResult(artifact_id=artifact_id, accepted=True, reason="ok", sniffed_mime_type=mime, byte_size=size)
