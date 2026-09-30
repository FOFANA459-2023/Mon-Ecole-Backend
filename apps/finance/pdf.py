import io
from decimal import Decimal
from xml.sax.saxutils import escape

from django.utils import timezone
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, A5
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.core.pdf import LIGHT, MUTED, NAVY, format_date, image_reader

from .money import minor_places
from .selectors import student_account
from .words import amount_in_words

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


def colon(language: str) -> str:
    """French typography puts a space before a colon; English does not."""
    return " : " if language == "fr" else ": "


def document_header(
    school, title: str, number_label: str, number: str, date_line: str, width: float, logo_mm: float
):
    logo = image_reader(school.logo)
    logo_column = (logo_mm + 4) * mm if logo else 0
    contact = " · ".join(escape(x) for x in [school.address, school.phone, school.email] if x)
    header = Table(
        [
            [
                Image(logo, logo_mm * mm, logo_mm * mm, kind="proportional") if logo else "",
                [Paragraph(escape(school.name), TITLE), Paragraph(contact, SUB)],
                [
                    Paragraph(title, TITLE),
                    Paragraph(f"{number_label} <b>{escape(number)}</b>", SUB),
                    Paragraph(date_line, SUB),
                ],
            ]
        ],
        colWidths=[logo_column, width - logo_column - 56 * mm, 56 * mm],
    )
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    return header


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

    header = document_header(
        school,
        L["invoice"],
        L["number"],
        invoice.number,
        f"{L['issued']}{colon(language)}{format_date(invoice.issue_date, school)}",
        width,
        18,
    )
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
        story += [
            Spacer(1, 5 * mm),
            Paragraph(f"<b>{L['notes']}</b>{colon(language)}{escape(invoice.notes)}", CELL),
        ]
    doc.build(story)
    return buffer.getvalue()


RECEIPT_LABELS = {
    "fr": {
        "receipt": "REÇU DE PAIEMENT",
        "number": "N°",
        "date": "Date",
        "reversed": "REÇU ANNULÉ",
        "reversed_on": "Paiement annulé le {date}",
        "student": "Élève",
        "student_number": "Matricule",
        "class": "Classe",
        "payer": "Versé par",
        "method": "Mode de paiement",
        "reference": "Référence",
        "amount": "Montant reçu",
        "in_words": "Arrêté le présent reçu à la somme de : {words}.",
        "applied_to": "Affectation",
        "description": "Désignation",
        "credit": "Avance conservée au crédit de l'élève",
        "balance": "Reste à payer au {date}",
        "received_by": "Reçu par",
        "signature": "Cachet et signature",
        "cash": "Espèces",
        "mobile_money": "Mobile money",
        "bank_transfer": "Virement / versement bancaire",
        "cheque": "Chèque",
        "card": "Carte bancaire",
        "other": "Autre",
    },
    "en": {
        "receipt": "PAYMENT RECEIPT",
        "number": "No.",
        "date": "Date",
        "reversed": "CANCELLED RECEIPT",
        "reversed_on": "Payment reversed on {date}",
        "student": "Student",
        "student_number": "Student no.",
        "class": "Class",
        "payer": "Paid by",
        "method": "Payment method",
        "reference": "Reference",
        "amount": "Amount received",
        "in_words": "Amount in words: {words}.",
        "applied_to": "Applied to",
        "description": "Description",
        "credit": "Kept as credit for the student",
        "balance": "Balance due as of {date}",
        "received_by": "Received by",
        "signature": "Stamp and signature",
        "cash": "Cash",
        "mobile_money": "Mobile money",
        "bank_transfer": "Bank transfer / deposit",
        "cheque": "Cheque",
        "card": "Card",
        "other": "Other",
    },
}
AMOUNT = ParagraphStyle("amount", parent=TITLE, fontSize=16, alignment=2)


def _class_name(payment) -> str:
    """The class of the invoices the payment paid, else the student's current class."""
    for allocation in payment.allocations.all():
        enrollment = allocation.invoice_line.invoice.enrollment
        if enrollment is not None:
            return enrollment.class_group.name
    current = (
        payment.student.enrollments.filter(status="active")
        .select_related("class_group")
        .order_by("-academic_year__start_date")
        .first()
    )
    return current.class_group.name if current else ""


def receipt_pdf(payment) -> bytes:
    """A5 receipt. `payment` comes from selectors.with_allocated, with its allocations."""
    school = payment.school
    language = school.default_language if school.default_language in RECEIPT_LABELS else "fr"
    L = RECEIPT_LABELS[language]
    currency = school.currency
    money = lambda value: format_money(value, currency, language)  # noqa: E731
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A5,
        leftMargin=11 * mm,
        rightMargin=11 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        title=f"{L['receipt']} {payment.number}",
    )
    width = A5[0] - 22 * mm
    story = [
        document_header(
            school,
            L["receipt"],
            L["number"],
            payment.number,
            f"{L['date']}{colon(language)}{format_date(payment.date, school)}",
            width,
            14,
        ),
        Spacer(1, 4 * mm),
    ]
    if payment.status == "reversed":
        story += [
            Paragraph(L["reversed"], WARN),
            Paragraph(
                L["reversed_on"].format(date=format_date(payment.reversed_at.date(), school))
                + (f" — {escape(payment.reversal_reason)}" if payment.reversal_reason else ""),
                SUB,
            ),
            Spacer(1, 3 * mm),
        ]

    student = payment.student
    method = L.get(payment.method, payment.method)
    if payment.reference:
        method += f" — {L['reference']} {payment.reference}"
    rows = [
        (L["student"], f"<b>{escape(student.full_name)}</b>"),
        (L["student_number"], escape(student.student_number)),
        (L["class"], escape(_class_name(payment)) or "—"),
        (L["payer"], escape(payment.payer_name) or "—"),
        (L["method"], escape(method)),
    ]
    who = Table(
        [[Paragraph(label, SUB), Paragraph(value, CELL)] for label, value in rows],
        colWidths=[32 * mm, width - 32 * mm],
    )
    who.setStyle(
        TableStyle([("BACKGROUND", (0, 0), (-1, -1), LIGHT), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")])
    )
    story += [who, Spacer(1, 4 * mm)]

    amount = Table(
        [[Paragraph(f"<b>{L['amount']}</b>", CELL), Paragraph(money(payment.amount), AMOUNT)]],
        colWidths=[width - 60 * mm, 60 * mm],
    )
    amount.setStyle(
        TableStyle([("BOX", (0, 0), (-1, -1), 0.8, NAVY), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")])
    )
    words = amount_in_words(payment.amount, currency, language)
    story += [amount, Spacer(1, 2 * mm), Paragraph(escape(L["in_words"].format(words=words)), CELL)]

    allocations = list(payment.allocations.all())
    if allocations or payment.unallocated:
        lines = [[Paragraph(f"<b>{L['applied_to']}</b>", CELL), ""]]
        lines += [
            [
                Paragraph(
                    f"{escape(a.invoice_line.description)} "
                    f"<font color='#5A6675'>({escape(a.invoice_line.invoice.number)})</font>",
                    CELL,
                ),
                Paragraph(money(a.amount), NUM),
            ]
            for a in allocations
        ]
        if payment.unallocated:
            lines.append([Paragraph(L["credit"], CELL), Paragraph(money(payment.unallocated), NUM)])
        table = Table(lines, colWidths=[width - 40 * mm, 40 * mm])
        table.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.4, LIGHT), ("SPAN", (0, 0), (-1, 0))]))
        story += [Spacer(1, 4 * mm), table]

    if payment.status == "posted":
        today = timezone.localdate()
        balance = student_account(student, today)["balance"]
        story += [
            Spacer(1, 3 * mm),
            Table(
                [
                    [
                        Paragraph(f"<b>{L['balance'].format(date=format_date(today, school))}</b>", CELL),
                        Paragraph(money(balance), BOLD_NUM),
                    ]
                ],
                colWidths=[width - 40 * mm, 40 * mm],
            ),
        ]

    received_by = payment.created_by.full_name if payment.created_by_id else ""
    signatures = Table(
        [
            [
                Paragraph(f"{L['received_by']}{colon(language)}{escape(received_by)}", SUB),
                Paragraph(L["signature"], SUB),
            ]
        ],
        colWidths=[width / 2, width / 2],
        rowHeights=[18 * mm],
    )
    signatures.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [Spacer(1, 6 * mm), signatures]
    doc.build(story)
    return buffer.getvalue()
