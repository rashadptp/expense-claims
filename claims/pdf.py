"""
Generate a printable PDF for an expense claim: header, summary, line-item
table, the AI validation result, the audit trail, and every uploaded receipt
image embedded full-size on its own page.

Pure ReportLab (no system deps) so it runs the same on Windows and Linux.
Pillow (already a dependency for receipts) handles image sizing/orientation.
"""
from __future__ import annotations

import io

from django.conf import settings
from django.utils import timezone
from PIL import Image as PILImage, ImageOps

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

INK = colors.HexColor("#0f172a")      # slate-900
MUTED = colors.HexColor("#64748b")    # slate-500
LINE = colors.HexColor("#e2e8f0")     # slate-200
BRAND = colors.HexColor("#4f46e5")    # indigo-600


def _styles():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle("CFTitle", parent=s["Title"], fontSize=20,
                         textColor=INK, spaceAfter=2))
    s.add(ParagraphStyle("CFMuted", parent=s["Normal"], fontSize=9,
                         textColor=MUTED))
    s.add(ParagraphStyle("CFH2", parent=s["Heading2"], fontSize=12,
                         textColor=INK, spaceBefore=14, spaceAfter=6))
    s.add(ParagraphStyle("CFCell", parent=s["Normal"], fontSize=9,
                         textColor=INK, leading=12))
    s.add(ParagraphStyle("CFCellR", parent=s["CFCell"], alignment=2))
    s.add(ParagraphStyle("CFCaption", parent=s["Normal"], fontSize=10,
                         textColor=MUTED, alignment=TA_CENTER, spaceBefore=6))
    return s


def _money(claim, value) -> str:
    return f"{claim.currency} {value:,.2f}"


def _summary_table(claim, st):
    rows = [
        ["Employee", claim.employee.get_full_name() or claim.employee.username],
        ["Branch", claim.branch.name],
        ["Status", claim.get_status_display()],
        ["Receipts", str(claim.item_count)],
        ["Total amount", _money(claim, claim.total_amount)],
        ["Submitted", timezone.localtime(claim.created_at).strftime("%d %b %Y, %H:%M")],
    ]
    if claim.ai_score is not None:
        rows.append(["AI validation score", f"{claim.ai_score} / 100"])
    if claim.description:
        rows.append(["Description", claim.description])

    data = [[Paragraph(k, st["CFMuted"]), Paragraph(v, st["CFCell"])] for k, v in rows]
    t = Table(data, colWidths=[40 * mm, 120 * mm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE),
    ]))
    return t


def _items_table(claim, items, st):
    head = ["#", "Vendor", "Category", "Date", "Amount"]
    data = [[Paragraph(f"<b>{h}</b>", st["CFCell"] if h != "Amount" else st["CFCellR"])
             for h in head]]
    for i, item in enumerate(items, 1):
        vendor = item.vendor or "—"
        if item.is_duplicate:
            vendor += " (possible duplicate)"
        if item.is_rejected:
            reason = f": {item.reject_reason}" if item.reject_reason else ""
            vendor = f"<strike>{vendor}</strike> <font color='#dc2626'>(rejected{reason})</font>"
        date = item.expense_date.strftime("%d %b %Y") if item.expense_date else "—"
        amount = _money(claim, item.amount)
        if item.is_rejected:
            amount = f"<strike>{amount}</strike>"
        data.append([
            Paragraph(str(i), st["CFCell"]),
            Paragraph(vendor, st["CFCell"]),
            Paragraph(item.get_category_display(), st["CFCell"]),
            Paragraph(date, st["CFCell"]),
            Paragraph(amount, st["CFCellR"]),
        ])
    data.append([
        Paragraph("", st["CFCell"]), Paragraph("", st["CFCell"]),
        Paragraph("", st["CFCell"]), Paragraph("<b>Total</b>", st["CFCellR"]),
        Paragraph(f"<b>{_money(claim, claim.total_amount)}</b>", st["CFCellR"]),
    ])

    t = Table(data, colWidths=[10 * mm, 64 * mm, 36 * mm, 28 * mm, 22 * mm],
              repeatRows=1)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, MUTED),
        ("LINEBELOW", (0, 1), (-1, -2), 0.3, LINE),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, INK),
        ("ALIGN", (-1, 0), (-1, -1), "RIGHT"),
    ]))
    return t


def _flags_block(claim, st, flow):
    flow.append(Paragraph("AI validation", st["CFH2"]))
    if not claim.ai_flags:
        flow.append(Paragraph("No issues detected.", st["CFCell"]))
        return
    for f in claim.ai_flags:
        sev = "CRITICAL" if f.get("severity") == "critical" else "Warning"
        item = f"[{f['item']}] " if f.get("item") else ""
        flow.append(Paragraph(f"&bull; <b>{sev}</b> — {item}{f.get('message', '')}",
                              st["CFCell"]))
        flow.append(Spacer(1, 2))


def _audit_table(logs, st):
    data = [[Paragraph(f"<b>{h}</b>", st["CFCell"]) for h in
             ("When", "Action", "By", "Note")]]
    for log in logs:
        when = timezone.localtime(log.created_at).strftime("%d %b %Y %H:%M")
        actor = (log.actor.get_full_name() or log.actor.username) if log.actor else "System"
        note = log.comment or ""
        if log.from_status and log.to_status and log.from_status != log.to_status:
            arrow = f"{log.from_status} → {log.to_status}"
            note = f"{note}\n{arrow}".strip() if note else arrow
        data.append([
            Paragraph(when, st["CFCell"]),
            Paragraph(log.get_action_display(), st["CFCell"]),
            Paragraph(actor, st["CFCell"]),
            Paragraph(note.replace("\n", "<br/>"), st["CFCell"]),
        ])
    t = Table(data, colWidths=[32 * mm, 28 * mm, 34 * mm, 66 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE),
    ]))
    return t


def _receipt_image(receipt, max_w, max_h):
    """Load a receipt image, fix EXIF orientation, scale to fit, as a flowable."""
    try:
        receipt.image.open()
        raw = receipt.image.read()
    finally:
        receipt.image.close()

    pil = PILImage.open(io.BytesIO(raw))
    pil = ImageOps.exif_transpose(pil)
    if pil.mode not in ("RGB", "L"):
        pil = pil.convert("RGB")

    buf = io.BytesIO()
    pil.save(buf, format="PNG")
    buf.seek(0)

    iw, ih = pil.size
    scale = min(max_w / iw, max_h / ih, 1.0)
    return Image(buf, width=iw * scale, height=ih * scale)


def build_claim_pdf(claim) -> bytes:
    """Render the full claim (details + receipts) to PDF bytes."""
    items = list(claim.items.select_related("receipt"))
    logs = list(claim.logs.select_related("actor"))
    st = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"Claim #{claim.pk}", author=settings.SITE_NAME,
    )

    flow = []
    flow.append(Paragraph(settings.SITE_NAME, ParagraphStyle(
        "brand", fontSize=11, textColor=BRAND, spaceAfter=2)))
    title = f"Expense Claim #{claim.pk}"
    if claim.title:
        title += f" — {claim.title}"
    flow.append(Paragraph(title, st["CFTitle"]))
    flow.append(Paragraph(
        f"Generated {timezone.localtime().strftime('%d %b %Y, %H:%M')}", st["CFMuted"]))
    flow.append(Spacer(1, 10))

    flow.append(_summary_table(claim, st))

    flow.append(Paragraph(f"Line items ({len(items)})", st["CFH2"]))
    flow.append(_items_table(claim, items, st))

    _flags_block(claim, st, flow)

    if logs:
        flow.append(Paragraph("Audit trail", st["CFH2"]))
        flow.append(_audit_table(logs, st))

    # Each receipt image on its own page.
    avail_w = A4[0] - 36 * mm
    avail_h = A4[1] - 60 * mm
    for i, item in enumerate(items, 1):
        if not (item.receipt and item.receipt.image):
            continue
        flow.append(PageBreak())
        label = f"Receipt {i} of {len(items)}"
        if item.vendor:
            label += f" — {item.vendor}"
        flow.append(Paragraph(label, st["CFH2"]))
        try:
            flow.append(_receipt_image(item.receipt, avail_w, avail_h))
        except Exception as exc:  # corrupt/missing file shouldn't break the PDF
            flow.append(Paragraph(f"(Could not render image: {exc})", st["CFMuted"]))
        flow.append(Paragraph(
            f"{item.get_category_display()} · {_money(claim, item.amount)}",
            st["CFCaption"]))

    doc.build(flow)
    return buf.getvalue()
