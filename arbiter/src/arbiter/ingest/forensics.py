"""
Forensic signals over an accepted artifact -- feed confidence, never gate
predicates (CLAUDE.md: signals, never proof). None of these functions are
allowed to reject an artifact or flip a predicate on their own; their output
only ever lowers `extract_conf` on the resulting evidence node, and the
finding is recorded in `artifact.forensics` for a human reviewer.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ForensicsReport:
    incremental_update_after_filed: bool = False
    moddate_after_filed_at: bool = False
    producer_suspicious: bool = False
    perceptual_hash: str | None = None
    findings: list[str] = field(default_factory=list)

    @property
    def any_flag(self) -> bool:
        return self.incremental_update_after_filed or self.moddate_after_filed_at or self.producer_suspicious

    def confidence_penalty(self) -> float:
        """A crude, documented scoring rule -- one flag: mild penalty, two+:
        severe. This is a signal for a human, not an automated verdict."""
        n = sum([self.incremental_update_after_filed, self.moddate_after_filed_at, self.producer_suspicious])
        return {0: 0.0, 1: 0.15, 2: 0.4}.get(n, 0.6)

    def to_dict(self) -> dict:
        return {
            "incremental_update_after_filed": self.incremental_update_after_filed,
            "moddate_after_filed_at": self.moddate_after_filed_at,
            "producer_suspicious": self.producer_suspicious,
            "perceptual_hash": self.perceptual_hash,
            "findings": self.findings,
            "confidence_penalty": self.confidence_penalty(),
        }


def count_incremental_updates(data: bytes) -> int:
    """Number of times this PDF was saved after its original creation.

    A PDF incremental update appends a new body, a new cross-reference
    section, a new trailer, and another `%%EOF` marker rather than
    rewriting the file. So `count(%%EOF) - 1` is the number of incremental
    saves, and it is derived from the byte stream itself -- not from
    metadata a forger controls.

    This replaces a check that never worked: `incremental` was initialised
    to False and never reassigned, and the branch that was supposed to set
    it tested `doc.is_dirty`, which is always False on a freshly opened
    document because it means "has unsaved changes in THIS session." PDF
    incremental-update analysis is the first item in the architecture
    document's forensics list and it always reported False.
    """
    return max(0, data.count(b"%%EOF") - 1)


def analyze_pdf(data: bytes, filed_at_unix: float | None) -> ForensicsReport:
    """PDF incremental-update chain + ModDate vs filed_at + producer
    mismatch. Uses PyMuPDF's metadata surface plus direct byte inspection;
    this is a light heuristic check, not a full forensic toolchain."""
    import fitz  # PyMuPDF

    findings: list[str] = []
    incremental = False
    moddate_after = False
    producer_suspicious = False

    revisions = count_incremental_updates(data)

    try:
        doc = fitz.open(stream=data, filetype="pdf")
        metadata = doc.metadata or {}
        producer = (metadata.get("producer") or "").lower()
        if any(tool in producer for tool in ("photoshop", "gimp", "paint", "screenshot")):
            producer_suspicious = True
            findings.append(f"suspicious producer metadata: {producer!r}")

        moddate_raw = metadata.get("modDate", "")
        moddate_ts = _parse_pdf_date(moddate_raw) if moddate_raw else None
        if moddate_ts is not None and filed_at_unix is not None and moddate_ts > filed_at_unix:
            moddate_after = True
            findings.append("ModDate postdates dispute filing")

        if revisions > 0:
            findings.append(
                f"document was saved {revisions} time(s) after creation "
                f"(incremental update chain, {revisions + 1} %%EOF markers)"
            )
            # An incremental update is only a *forensic* signal when it
            # happened after the dispute existed -- a receipt legitimately
            # revised months earlier is not evidence of anything. Absent a
            # filing time we record the revision count without asserting
            # the flag, per this module's "signals, never proof" contract.
            if moddate_after or (filed_at_unix is not None and moddate_ts is None):
                incremental = True
                findings.append(
                    "incremental update chain present and the document's last "
                    "modification cannot be shown to predate the dispute"
                )
        doc.close()
    except Exception as e:  # malformed PDF -- itself a forensic signal
        findings.append(f"failed to parse as PDF: {e}")
        producer_suspicious = True

    return ForensicsReport(
        incremental_update_after_filed=incremental,
        moddate_after_filed_at=moddate_after,
        producer_suspicious=producer_suspicious,
        findings=findings,
    )


def _parse_pdf_date(raw: str) -> float | None:
    """PDF date strings look like D:20260315120000+00'00'."""
    import re
    from datetime import datetime, timezone

    match = re.match(r"D:(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?", raw)
    if not match:
        return None
    y, mo, d, h, mi, s = (int(g) if g else 0 for g in match.groups())
    try:
        return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def perceptual_hash_image(data: bytes) -> str | None:
    """A simple average-hash over the raster, used to flag template reuse
    across cases (the same image resubmitted with edited dates). Not a
    cryptographic hash -- collisions are the point."""
    try:
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(data)).convert("L").resize((8, 8))
        pixels = list(img.getdata())
        avg = sum(pixels) / len(pixels)
        bits = "".join("1" if p > avg else "0" for p in pixels)
        return f"{int(bits, 2):016x}"
    except Exception:
        return None
