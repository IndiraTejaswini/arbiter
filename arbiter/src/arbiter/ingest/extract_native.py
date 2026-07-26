"""
Born-digital PDF fast path (~150ms per the build spec): PyMuPDF text layer
+ simple layout heuristics to classify document type and pull the fields
the rulepacks actually need. Checked first, before OCR/VLM, since most
merchant-submitted PDFs (invoices, delivery confirmations exported from an
OMS) have a real text layer.
"""

from __future__ import annotations

import re
from datetime import datetime

from .schemas import DocumentType, ExtractedField, ExtractionResult, SourceRef

_DOC_TYPE_KEYWORDS: dict[DocumentType, tuple[str, ...]] = {
    "delivery_confirmation": ("delivered", "proof of delivery", "signed for", "tracking number", "carrier"),
    "invoice": ("invoice", "bill to", "line item", "subtotal", "invoice number"),
    "receipt": ("receipt", "thank you for your purchase", "order confirmation"),
    "terms": ("terms and conditions", "terms of service", "refund policy", "return policy"),
    "communication": ("dear customer", "re:", "subject:", "regards"),
    "refund_record": ("refund", "credit issued", "amount refunded"),
}

_AMOUNT_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)")
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})\b")
_TRACKING_RE = re.compile(r"\b(1Z[0-9A-Z]{16}|\d{12,22})\b")


def classify_document_type(text: str) -> DocumentType:
    lowered = text.lower()
    best: DocumentType = "unknown"
    best_hits = 0
    for doc_type, keywords in _DOC_TYPE_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in lowered)
        if hits > best_hits:
            best_hits = hits
            best = doc_type
    return best


def _field_confidence(layout_conf: float) -> float:
    return max(0.5, min(0.98, layout_conf))


def extract_native(artifact_id: str, pdf_bytes: bytes) -> ExtractionResult | None:
    """Returns None (not a native-text-layer document) if the extracted
    text layer is too sparse to be trustworthy -- the caller should fall
    through to OCR. Never raises on a malformed PDF; that's also a
    fall-through signal, not this module's problem to solve."""
    import fitz  # PyMuPDF

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return None

    full_text = []
    fields: list[ExtractedField] = []
    for page_idx in range(doc.page_count):
        page = doc[page_idx]
        text = page.get_text("text")
        full_text.append(text)

        for match in _AMOUNT_RE.finditer(text):
            rects = page.search_for(match.group(0))
            bbox = tuple(rects[0]) if rects else None
            fields.append(ExtractedField(
                field_name="amount", value=match.group(1).replace(",", ""),
                confidence=_field_confidence(0.9),
                source_ref=SourceRef(artifact_id=artifact_id, page=page_idx, bbox=bbox,
                                      char_span=(match.start(), match.end())),
            ))
        for match in _DATE_RE.finditer(text):
            rects = page.search_for(match.group(0))
            bbox = tuple(rects[0]) if rects else None
            fields.append(ExtractedField(
                field_name="date", value=match.group(1), confidence=_field_confidence(0.85),
                source_ref=SourceRef(artifact_id=artifact_id, page=page_idx, bbox=bbox,
                                      char_span=(match.start(), match.end())),
            ))
        for match in _TRACKING_RE.finditer(text):
            rects = page.search_for(match.group(0))
            bbox = tuple(rects[0]) if rects else None
            fields.append(ExtractedField(
                field_name="tracking_number", value=match.group(1), confidence=_field_confidence(0.8),
                source_ref=SourceRef(artifact_id=artifact_id, page=page_idx, bbox=bbox,
                                      char_span=(match.start(), match.end())),
            ))

    joined = "\n".join(full_text)
    doc.close()

    if len(joined.strip()) < 20:
        return None  # too little text -- likely scanned; fall through to OCR

    doc_type = classify_document_type(joined)
    return ExtractionResult(artifact_id=artifact_id, document_type=doc_type, fields=fields, extraction_method="native")
