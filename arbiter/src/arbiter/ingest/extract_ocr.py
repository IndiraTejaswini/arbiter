"""
Scanned/image document path: PaddleOCR if installed, else this module
reports unavailable and the router (route.py) falls through to the VLM
path. PaddleOCR is a large, GPU-friendly but heavy dependency (~500MB of
models on first run); it is deliberately NOT in this build's default
dependency set (pyproject.toml) so a `pip install -e .` stays fast --
install `paddleocr` and `paddlepaddle` to enable this path. Degrade, never
crash: an ImportError here is a routing signal, not a fatal error.
"""

from __future__ import annotations

from .schemas import ExtractedField, ExtractionResult, SourceRef
from .extract_native import classify_document_type

_ocr_engine = None


def _get_engine():
    global _ocr_engine
    if _ocr_engine is None:
        from paddleocr import PaddleOCR  # optional dependency, see module docstring

        _ocr_engine = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
    return _ocr_engine


def is_available() -> bool:
    try:
        import paddleocr  # noqa: F401

        return True
    except ImportError:
        return False


def extract_ocr(artifact_id: str, image_bytes: bytes, page: int = 0) -> ExtractionResult | None:
    if not is_available():
        return None

    import numpy as np
    from PIL import Image
    import io

    engine = _get_engine()
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    result = engine.ocr(np.array(img), cls=True)

    fields: list[ExtractedField] = []
    lines: list[str] = []
    for line in (result[0] if result else []):
        box, (text, conf) = line
        lines.append(text)
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        bbox = (min(xs), min(ys), max(xs), max(ys))
        fields.append(ExtractedField(
            field_name="ocr_line", value=text, confidence=float(conf),
            source_ref=SourceRef(artifact_id=artifact_id, page=page, bbox=bbox),
        ))

    doc_type = classify_document_type("\n".join(lines))
    return ExtractionResult(artifact_id=artifact_id, document_type=doc_type, fields=fields, extraction_method="ocr")
