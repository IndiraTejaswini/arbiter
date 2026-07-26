"""
Typed extraction output -- the ONLY thing allowed to cross the quarantine
boundary (C5, CLAUDE.md invariant #3).

Deliberately no `raw_text` field anywhere in this module. That is not an
oversight to fix later; it is the mechanism. Untrusted document text is
read by scan.py/extract_*.py, inside arbiter.ingest, and never leaves this
package as a string an LLM or the referee could be tricked by -- only these
typed, bounded fields do.
"""

from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, Field


class SourceRef(BaseModel):
    artifact_id: str
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    char_span: tuple[int, int] | None = None


class ExtractedField(BaseModel):
    field_name: str
    value: Union[str, float, bool]
    confidence: float = Field(ge=0, le=1)
    source_ref: SourceRef


DocumentType = Literal[
    "delivery_confirmation", "invoice", "receipt", "terms",
    "communication", "refund_record", "unknown",
]
ExtractionMethod = Literal["native", "ocr", "vlm"]


class ExtractionResult(BaseModel):
    artifact_id: str
    document_type: DocumentType
    fields: list[ExtractedField]
    extraction_method: ExtractionMethod
    # No raw_text field. Deliberate. Never add one.


class ScanResult(BaseModel):
    """Result of scan.py's boundary checks, before any content is read."""

    artifact_id: str
    accepted: bool
    reason: str
    sniffed_mime_type: str | None = None
    byte_size: int = 0
