"""
Born-digital PDF fast path (~150ms per the build spec): PyMuPDF text layer
+ simple layout heuristics to classify document type and pull the fields
the rulepacks actually need. Checked first, before OCR/VLM, since most
merchant-submitted PDFs (invoices, delivery confirmations exported from an
OMS) have a real text layer.
"""

from __future__ import annotations

import re

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

# -- Status assertions, for the semantic contradiction layer ---------------
#
# The three patterns above pull amounts, dates and tracking ids: all
# numeric or identifier-shaped, none of them a *statement* about what
# happened. That is why `arbiter.evidence.semantic` reported
# NOT_APPLICABLE on every real case -- a text cross-encoder had no text to
# compare, so the fourth mandatory layer never ran even though it was
# fully implemented and the model loaded fine.
#
# These capture the one sentence in which a document asserts a delivery or
# refund status, which is precisely the claim two parties contradict each
# other about ("delivered and signed for" vs "never arrived"). This is NOT
# a raw-text escape hatch and must never become one (CLAUDE.md invariant
# #3): the value is a NAMED, LENGTH-CAPPED field selected by a fixed term
# list, exactly like `amount` or `tracking_number`, not the page contents.
# `ExtractedField.value` has always been allowed to be a string -- the VLM
# path already emits `delivery_status` this way.
_MAX_STATUS_CHARS = 300
_MIN_STATUS_CHARS = 10

_DELIVERY_TERMS = (
    "delivered", "delivery", "arrived", "returned to sender", "signed for",
    "in transit", "undeliverable", "left with", "handed to",
)
_REFUND_TERMS = (
    "refund", "refunded", "credit issued", "credited", "credit note",
    "money back", "reimbursed",
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n")

# Lines that name a field rather than assert anything. A letterhead
# ("ACME LOGISTICS -- PROOF OF DELIVERY") and a mail header
# ("Subject: order still not delivered") both contain delivery terms, and
# both are far weaker evidence of what happened than the sentence in the
# body -- an NLI cross-encoder given a title compares typography, not
# claims.
_HEADER_PREFIXES = ("subject:", "re:", "to:", "from:", "date:", "cc:", "bcc:", "ref:")
_MIN_PROSE_WORDS = 5


def _status_sentence(text: str, terms: tuple[str, ...]) -> str | None:
    """The best bounded sentence asserting one of `terms`, or None.

    Bounded on both ends deliberately: too short and it carries no claim to
    classify, too long and it is a paragraph rather than an assertion --
    and an unbounded value here would be the raw-text leak this module
    exists to prevent.

    Prose is preferred over headers, but a header is still returned when it
    is all the document offers: a delivery claim stated only in a title is
    weak evidence, and silently discarding it would put the layer back in
    the state this whole change exists to end -- reporting NOT_APPLICABLE
    when there was in fact something to compare.
    """
    candidates = []
    for raw in _SENTENCE_SPLIT.split(text):
        sentence = " ".join(raw.split())
        if not (_MIN_STATUS_CHARS <= len(sentence) <= _MAX_STATUS_CHARS):
            continue
        if any(term in sentence.lower() for term in terms):
            candidates.append(sentence)

    if not candidates:
        return None

    def is_prose(sentence: str) -> bool:
        return (
            not sentence.isupper()
            and len(sentence.split()) >= _MIN_PROSE_WORDS
            and not sentence.lower().startswith(_HEADER_PREFIXES)
        )

    return next((s for s in candidates if is_prose(s)), candidates[0])


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
    # At most one status assertion per subject per document: a second
    # sentence about the same subject is the same claim restated, and
    # feeding both to the cross-encoder would have a document contradict
    # itself on its own phrasing.
    seen_status: set[str] = set()
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

        for field_name, terms in (("delivery_status", _DELIVERY_TERMS),
                                  ("refund_status", _REFUND_TERMS)):
            if field_name in seen_status:
                continue
            sentence = _status_sentence(text, terms)
            if sentence is None:
                continue
            seen_status.add(field_name)
            # `search_for` matches on the page's own spacing; the sentence
            # has been whitespace-normalised, so probe with a short prefix
            # and accept a missing bbox rather than dropping the field.
            # Invariant #12 requires a source_ref, not a bbox specifically.
            rects = page.search_for(sentence[:40])
            bbox = tuple(rects[0]) if rects else None
            start = text.find(sentence[:40])
            fields.append(ExtractedField(
                field_name=field_name, value=sentence,
                confidence=_field_confidence(0.75),
                source_ref=SourceRef(
                    artifact_id=artifact_id, page=page_idx, bbox=bbox,
                    char_span=(start, start + len(sentence)) if start >= 0 else None,
                ),
            ))

    joined = "\n".join(full_text)
    doc.close()

    if len(joined.strip()) < 20:
        return None  # too little text -- likely scanned; fall through to OCR

    doc_type = classify_document_type(joined)
    return ExtractionResult(artifact_id=artifact_id, document_type=doc_type, fields=fields, extraction_method="native")
