import io
from decimal import Decimal
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.core.pdf import LIGHT, MUTED, NAVY, format_date, image_reader

from .money import minor_places

LABELS = {
    "fr": {
        "invoice": "FACTURE",
        "number": "N°",
        "issued": "Date d'émission",
        "student": "Élève",
        "student_number": "Matricule",
        "class": "Classe",
        "year": "Année scolaire",
        "description": "Désignation",
        "due": "Échéance",
        "amount": "Montant",
        "discount": "Remise",
        "net": "Net à payer",
        "subtotal": "Sous-total",
        "discounts": "Remises",
        "total": "Total",
        "paid": "Déjà payé",
        "balance": "Reste à payer",
        "cancelled": "FACTURE ANNULÉE",
        "notes": "Notes",
    },
    "en": {
        "invoice": "INVOICE",
        "number": "No.",
        "issued": "Issue date",
        "student": "Student",
        "student_number": "Student no.",
        "class": "Class",
        "year": "School year",
        "description": "Description",
        "due": "Due date",
        "amount": "Amount",
        "discount": "Discount",
        "net": "Net due",
        "subtotal": "Subtotal",
        "discounts": "Discounts",
        "total": "Total",
        "paid": "Paid so far",
        "balance": "Balance due",
        "cancelled": "CANCELLED INVOICE",
        "notes": "Notes",
    },
}

styles = getSampleStyleSheet()
TITLE = ParagraphStyle(
    "title", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=15, textColor=NAVY, alignment=0
)
SUB = ParagraphStyle("sub", parent=styles["Normal"], fontSize=8.5, textColor=MUTED, leading=11)
CELL = ParagraphStyle("cell", parent=styles["Normal"], fontSize=8.5, leading=11)
NUM = ParagraphStyle("num", parent=CELL, alignment=2)
BOLD_NUM = ParagraphStyle("boldnum", parent=NUM, fontName="Helvetica-Bold")
WARN = ParagraphStyle("warn", parent=TITLE, textColor=HexColor("#B42318"), fontSize=12)


def format_money(value: Decimal, currency: str, language: str) -> str:
    """1 250 000 GNF (French) or 1,250,000.00 LRD (English)."""
    places = minor_places(currency)
    text = f"{value:,.{places}f}"
    if language == "fr":
        text = text.replace(",", " ").replace(".", ",")
    return f"{text} {currency}"


def invoice_pdf(invoice) -> bytes:
    """A4 invoice. `invoice` comes from selectors.with_balances (amount_paid, balance) with its lines."""
    school = invoice.school
    language = school.default_language if school.default_language in LABELS else "fr"
    L = LABELS[language]
    currency = school.currency
    money = lambda value: format_money(value, currency, language)  # noqa: E731
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"{L['invoice']} {invoice.number}",
    )
    width = A4[0] - 32 * mm
    story = []

    logo = image_reader(school.logo)
    contact = " · ".join(escape(x) for x in [school.address, school.phone, school.email] if x)
    header = Table(
        [
            [
                Image(logo, 18 * mm, 18 * mm, kind="proportional") if logo else "",
                [Paragraph(escape(school.name), TITLE), Paragraph(contact, SUB)],
                [
                    Paragraph(L["invoice"], TITLE),
                    Paragraph(f"{L['number']} <b>{escape(invoice.number)}</b>", SUB),
                    Paragraph(f"{L['issued']} : {format_date(invoice.issue_date, school)}", SUB),
                ],
            ]
        ],
        colWidths=[22 * mm, width - 82 * mm, 60 * mm],
    )
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [header, Spacer(1, 5 * mm)]
    if invoice.status == "cancelled":
        story += [Paragraph(L["cancelled"], WARN), Spacer(1, 3 * mm)]

    student = invoice.student
    class_name = invoice.enrollment.class_group.name if invoice.enrollment_id else ""
    who = Table(
        [
            [
                Paragraph(L["student"], SUB),
                Paragraph(f"<b>{escape(student.full_name)}</b>", CELL),
                Paragraph(L["student_number"], SUB),
                Paragraph(escape(student.student_number), CELL),
            ],
            [
                Paragraph(L["class"], SUB),
                Paragraph(escape(class_name) or "—", CELL),
                Paragraph(L["year"], SUB),
                Paragraph(escape(invoice.academic_year.name), CELL),
            ],
        ],
        colWidths=[28 * mm, width / 2 - 28 * mm, 28 * mm, width / 2 - 28 * mm],
    )
    who.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.4, LIGHT), ("BACKGROUND", (0, 0), (-1, -1), LIGHT)]))
    story += [who, Spacer(1, 5 * mm)]

    rows = [
        [
            Paragraph(f"<b>{h}</b>", NUM if i >= 2 else CELL)
            for i, h in enumerate((L["description"], L["due"], L["amount"], L["discount"], L["net"]))
        ]
    ]
    for line in invoice.lines.all():
        rows.append(
            [
                Paragraph(escape(line.description), CELL),
                Paragraph(format_date(line.due_date, school), CELL),
                Paragraph(money(line.amount), NUM),
                Paragraph(money(line.discount) if line.discount else "—", NUM),
                Paragraph(money(line.net), NUM),
            ]
        )
    table = Table(rows, colWidths=[width - 128 * mm, 26 * mm, 34 * mm, 30 * mm, 38 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, LIGHT),
                ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story += [table, Spacer(1, 4 * mm)]

    totals = [
        (L["subtotal"], invoice.subtotal, NUM),
        (L["discounts"], -invoice.discount_total, NUM),
        (L["total"], invoice.total, BOLD_NUM),
        (L["paid"], invoice.amount_paid, NUM),
        (L["balance"], invoice.balance, BOLD_NUM),
    ]
    summary = Table(
        [
            [Paragraph(label, NUM if style is NUM else BOLD_NUM), Paragraph(money(value), style)]
            for label, value, style in totals
        ],
        colWidths=[width - 50 * mm, 50 * mm],
    )
    summary.setStyle(
        TableStyle([("LINEABOVE", (0, 2), (-1, 2), 0.6, NAVY), ("LINEABOVE", (0, 4), (-1, 4), 0.6, NAVY)])
    )
    story.append(summary)

    if invoice.notes:
        story += [Spacer(1, 5 * mm), Paragraph(f"<b>{L['notes']}</b> : {escape(invoice.notes)}", CELL)]
    doc.build(story)
    return buffer.getvalue()
