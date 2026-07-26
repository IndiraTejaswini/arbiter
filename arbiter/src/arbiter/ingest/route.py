"""
Ingest orchestrator: scan -> forensics -> route to extraction method ->
typed EvidenceNode(s). This is the single call site api.orchestration uses;
everything upstream (scan.py, forensics.py, extract_*.py) is an
implementation detail behind this one function.

Router order per the build spec: born-digital PDF (native, ~150ms, checked
first) -> scanned/image (OCR, ~1.2s/page, if installed) -> low layout
confidence or OCR unavailable (VLM, ~2-4s/page).
"""

from __future__ import annotations

from typing import Optional

from arbiter.evidence.models import EvidenceNode, EvidenceNodeType, ProvenanceTier, SourceRef as EvidenceSourceRef

from . import extract_native, extract_ocr, extract_vlm, forensics, scan
from .schemas import ExtractionResult

_DOC_TYPE_TO_NODE_TYPE = {
    "delivery_confirmation": EvidenceNodeType.DELIVERY_SCAN,
    "invoice": EvidenceNodeType.ORDER,
    "receipt": EvidenceNodeType.ORDER,
    "terms": EvidenceNodeType.TERMS_ACCEPTANCE,
    "communication": EvidenceNodeType.COMMUNICATION,
    "refund_record": EvidenceNodeType.REFUND,
    "unknown": EvidenceNodeType.CLAIM,
}

# A minimal, honestly-partial field -> rulepack-predicate mapping (further
# reason codes/fields are a straightforward extension of this table, not a
# different mechanism). Keeping this here rather than in arbiter.evidence
# keeps the dependency direction right: ingest already knows about
# EvidenceNode (a types-only module), and a types-only module must not need
# to know about ingest.
_PREDICATE_HINTS: dict[str, tuple[str, str]] = {
    # extracted field_name -> (asserts_predicate, node_type doc context)
    "delivered": ("delivery_confirmed", "delivery_confirmation"),
    "signature": ("signature_missing", "delivery_confirmation"),
    "refund_amount": ("refund_issued", "refund_record"),
    "access_granted": ("digital_goods_access_logged", "unknown"),
}


def _extraction_to_node(case_id: str, artifact_id: str, extraction: ExtractionResult,
                         provenance: ProvenanceTier, commitment_id: Optional[str] = None) -> EvidenceNode:
    node_type = _DOC_TYPE_TO_NODE_TYPE.get(extraction.document_type, EvidenceNodeType.CLAIM)
    attrs: dict = {
        "document_type": extraction.document_type,
        "extraction_method": extraction.extraction_method,
        "extracted_fields": [f.model_dump() for f in extraction.fields],
    }

    # Best-effort predicate tagging from recognised field names -- see
    # _PREDICATE_HINTS. Anything not recognised stays as inert data on the
    # node (visible to a human reviewer, never wired into a rule) rather
    # than being dropped.
    for field in extraction.fields:
        hint = _PREDICATE_HINTS.get(field.field_name)
        if hint:
            predicate, _ = hint
            attrs["asserts_predicate"] = predicate
            attrs["predicate_value"] = str(field.value).strip().lower() in ("true", "yes", "1", "delivered")
            break

    conf = min((f.confidence for f in extraction.fields), default=0.7)
    source_ref = None
    if extraction.fields:
        sr = extraction.fields[0].source_ref
        source_ref = EvidenceSourceRef(artifact_id=sr.artifact_id, page=sr.page, bbox=sr.bbox, char_span=sr.char_span)

    return EvidenceNode(
        case_id=case_id, node_type=node_type, attrs=attrs, provenance=provenance,
        extract_conf=conf, artifact_id=artifact_id, commitment_id=commitment_id, source_ref=source_ref,
    )


def process_artifact(
    case_id: str,
    artifact_id: str,
    data: bytes,
    filed_at_unix: Optional[float] = None,
    provenance: ProvenanceTier = ProvenanceTier.SUBMITTED,
    commitment_id: Optional[str] = None,
) -> tuple[Optional[EvidenceNode], dict]:
    """Returns (node_or_None, report). report always contains scan +
    forensics info for the audit trail, regardless of whether extraction
    succeeded."""
    scan_result = scan.scan_artifact(artifact_id, data)
    report: dict = {"scan": scan_result.model_dump()}
    if not scan_result.accepted:
        return None, report

    forensics_report = None
    if scan_result.sniffed_mime_type == "application/pdf":
        forensics_report = forensics.analyze_pdf(data, filed_at_unix)
    report["forensics"] = forensics_report.to_dict() if forensics_report else None

    extraction: Optional[ExtractionResult] = None
    if scan_result.sniffed_mime_type == "application/pdf":
        extraction = extract_native.extract_native(artifact_id, data)
        if extraction is None:
            if extract_ocr.is_available():
                # rasterize page 1 for OCR/VLM fallback
                import fitz

                doc = fitz.open(stream=data, filetype="pdf")
                png_bytes = doc[0].get_pixmap(dpi=200).tobytes("png")
                doc.close()
                extraction = extract_ocr.extract_ocr(artifact_id, png_bytes)
            if extraction is None and extract_vlm.is_available():
                import fitz

                doc = fitz.open(stream=data, filetype="pdf")
                png_bytes = doc[0].get_pixmap(dpi=200).tobytes("png")
                doc.close()
                extraction = extract_vlm.extract_vlm(artifact_id, png_bytes)
    else:
        # already an image
        if extract_ocr.is_available():
            extraction = extract_ocr.extract_ocr(artifact_id, data)
        if extraction is None and extract_vlm.is_available():
            extraction = extract_vlm.extract_vlm(artifact_id, data)

    report["extraction_method"] = extraction.extraction_method if extraction else None
    if extraction is None:
        return None, report

    conf_penalty = forensics_report.confidence_penalty() if forensics_report else 0.0
    node = _extraction_to_node(case_id, artifact_id, extraction, provenance, commitment_id)
    node.extract_conf = max(0.05, node.extract_conf * (1.0 - conf_penalty))
    return node, report
