"""
Synthetic document generation for exercising arbiter.ingest end to end.

Builds real PDF bytes (via PyMuPDF, the same library extract_native.py
reads with) and real PNG raster bytes (via Pillow) -- not fixtures that
merely *describe* a document, but files a scan/forensics/extract_* call can
actually open and process. This is what lets the adversarial suite
(adversarial.py) construct a genuinely backdated PDF, a genuinely
prompt-injected image, etc., rather than simulating their effects.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional


def make_delivery_confirmation_pdf(
    order_id: str,
    address: str,
    delivery_date: datetime,
    tracking_number: str,
    amount_minor: int,
    currency: str = "USD",
) -> bytes:
    import fitz  # PyMuPDF

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    y = 60
    lines = [
        "ACME LOGISTICS -- PROOF OF DELIVERY",
        "",
        f"Order #{order_id}",
        f"Delivered to: {address}",
        f"Delivery Date: {delivery_date.date().isoformat()}",
        f"Tracking: {tracking_number}",
        "Status: DELIVERED - Signature on file",
        f"Amount: ${amount_minor / 100:.2f} {currency}",
    ]
    for line in lines:
        page.insert_text((50, y), line, fontsize=12)
        y += 22
    data = doc.tobytes()
    doc.close()
    return data


def make_invoice_pdf(order_id: str, line_items: list[tuple[str, int]], currency: str = "USD") -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    y = 60
    page.insert_text((50, y), f"INVOICE -- Order #{order_id}", fontsize=14)
    y += 30
    total = 0
    for name, minor in line_items:
        page.insert_text((50, y), f"{name}  ${minor/100:.2f}", fontsize=11)
        total += minor
        y += 18
    y += 10
    page.insert_text((50, y), f"Subtotal: ${total/100:.2f} {currency}", fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


def make_refund_record_pdf(order_id: str, refund_amount_minor: int, refund_date: datetime, currency: str = "USD") -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    lines = [
        "REFUND CONFIRMATION",
        f"Order #{order_id}",
        f"Refund issued: {refund_date.date().isoformat()}",
        f"Amount refunded: ${refund_amount_minor/100:.2f} {currency}",
    ]
    y = 60
    for line in lines:
        page.insert_text((50, y), line, fontsize=12)
        y += 22
    data = doc.tobytes()
    doc.close()
    return data


def make_communication_pdf(subject: str, body: str, sender: str = "support@merchant.example") -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    y = 60
    page.insert_text((50, y), f"From: {sender}", fontsize=10)
    y += 16
    page.insert_text((50, y), f"Subject: {subject}", fontsize=12)
    y += 24
    for line in body.split("\n"):
        page.insert_text((50, y), line, fontsize=11)
        y += 16
    data = doc.tobytes()
    doc.close()
    return data


def render_png(pdf_bytes: bytes, page_index: int = 0, dpi: int = 200) -> bytes:
    """Rasterize a page for the OCR/VLM path -- what route.py does
    internally when the native text layer is too sparse."""
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    png = doc[page_index].get_pixmap(dpi=dpi).tobytes("png")
    doc.close()
    return png


def make_image_with_text(lines: list[str], hidden_line: Optional[str] = None, size: tuple[int, int] = (900, 600)) -> bytes:
    """A scanned-document stand-in built directly as a raster (no PDF text
    layer at all), for OCR/VLM-path testing. `hidden_line`, if given, is
    rendered in near-background colour -- the "white 1pt text" prompt-
    injection pattern, made concrete rather than merely described."""
    import io

    from PIL import Image, ImageDraw

    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    y = 40
    for line in lines:
        draw.text((40, y), line, fill="black")
        y += 30
    if hidden_line:
        draw.text((40, y + 20), hidden_line, fill=(250, 250, 248))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
