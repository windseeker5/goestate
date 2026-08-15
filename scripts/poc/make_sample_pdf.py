"""Generate a sample PDF that mimics a real estate document: a funeral home
invoice with an itemized table plus a short narrative section. Used only to
test the Docling -> sqlite-vec ingestion pipeline (scripts/poc/ingest_poc.py).

Requires reportlab (dev-only, not a runtime dependency of the app):
    pip install reportlab

Usage:
    python scripts/poc/make_sample_pdf.py
"""

import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

OUT_PATH = os.path.join(os.path.dirname(__file__), "sample_docs", "funeral_home_invoice.pdf")


def build_pdf(path):
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(path, pagesize=letter, topMargin=0.75 * inch, bottomMargin=0.75 * inch)
    story = []

    story.append(Paragraph("Maple Grove Funeral Home", styles["Title"]))
    story.append(Paragraph("123 Rue Principale, Montreal, QC H2X 1Y4", styles["Normal"]))
    story.append(Spacer(1, 0.25 * inch))

    story.append(Paragraph("<b>Invoice #FH-2026-0417</b>", styles["Normal"]))
    story.append(Paragraph("Date of Service: August 3, 2026", styles["Normal"]))
    story.append(Paragraph("Estate of: Jean Tremblay", styles["Normal"]))
    story.append(Paragraph("Official Date of Death: August 1, 2026", styles["Normal"]))
    story.append(Spacer(1, 0.3 * inch))

    story.append(Paragraph("Itemized Charges", styles["Heading2"]))

    table_data = [
        ["Item", "Description", "Quantity", "Unit Price", "Total"],
        ["Casket", "Oak veneer, deluxe interior", "1", "$3,200.00", "$3,200.00"],
        ["Embalming", "Standard preparation", "1", "$850.00", "$850.00"],
        ["Visitation Room", "Full-day rental, 2 days", "2", "$400.00", "$800.00"],
        ["Death Certificates", "Certified copies", "6", "$25.00", "$150.00"],
        ["Obituary Notice", "Local newspaper, 3 days", "1", "$180.00", "$180.00"],
        ["Transportation", "Hearse + family car", "1", "$650.00", "$650.00"],
        ["Cremation Fee", "Standard cremation service", "1", "$975.00", "$975.00"],
    ]

    table = Table(table_data, colWidths=[1.3 * inch, 2.2 * inch, 0.8 * inch, 1 * inch, 1 * inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2b2b2b")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(table)
    story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("<b>Total Due: $6,805.00</b>", styles["Normal"]))
    story.append(Paragraph("Payment Terms: Due within 30 days of invoice date.", styles["Normal"]))
    story.append(Spacer(1, 0.3 * inch))

    story.append(Paragraph(
        "Notes: The family has requested that the ashes be transferred to a "
        "commemorative urn provided separately by the estate. All arrangements "
        "were confirmed verbally with the executor, Ken Dresdell, on August 2, 2026.",
        styles["Normal"],
    ))

    doc.build(story)


if __name__ == "__main__":
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    build_pdf(OUT_PATH)
    print(f"Sample PDF written to: {OUT_PATH}")
