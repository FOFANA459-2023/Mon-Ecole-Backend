import io
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from django.utils import timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.core.pdf import LIGHT, NAVY, format_date
from apps.finance.pdf import BOLD_NUM, CELL, NUM, SUB, WARN, colon, document_header, format_money

from . import services

LABELS = {
    "fr": {
        "journal": "JOURNAL DE CAISSE",
        "opened": "Ouverte le",
        "opened_by": "Ouverte par",
        "closed": "Clôturée le",
        "closed_by": "Clôturée par",
        "still_open": "Session encore ouverte : chiffres au {when}.",
        "time": "Heure",
        "description": "Libellé",
        "by": "Par",
        "in": "Entrées",
        "out": "Sorties",
        "none": "Aucun mouvement.",
        "opening": "Fonds de caisse à l'ouverture",
        "total_in": "Total des entrées",
        "total_out": "Total des sorties",
        "expected": "Solde théorique",
        "counted": "Espèces comptées",
        "difference": "Écart (excédent + / manquant −)",
        "note": "Observation",
        "cashier": "Le caissier",
        "checked": "Vérifié par",
    },
    "en": {
        "journal": "CASH JOURNAL",
        "opened": "Opened",
        "opened_by": "Opened by",
        "closed": "Closed",
        "closed_by": "Closed by",
        "still_open": "Session still open: figures as of {when}.",
        "time": "Time",
        "description": "Description",
        "by": "By",
        "in": "In",
        "out": "Out",
        "none": "No movements.",
        "opening": "Float at opening",
        "total_in": "Total in",
        "total_out": "Total out",
        "expected": "Expected cash",
        "counted": "Cash counted",
        "difference": "Difference (surplus + / shortage −)",
        "note": "Note",
        "cashier": "Cashier",
        "checked": "Checked by",
    },
}


def session_journal_pdf(session) -> bytes:
    """A4 journal of one cash session: every movement, the totals and the closing count, to sign."""
    school = session.school
    language = school.default_language if school.default_language in LABELS else "fr"
    L = LABELS[language]
    sep = colon(language)
    currency = school.currency
    money = lambda value: format_money(value, currency, language)  # noqa: E731
    zone = ZoneInfo(school.timezone) if school.timezone else None

    def when(value) -> str:
        local = timezone.localtime(value, zone)
        return f"{format_date(local.date(), school)} {local:%H:%M}"

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"{L['journal']} — {session.register.name}",
    )
    width = A4[0] - 32 * mm
    story = [
        document_header(
            school,
            L["journal"],
            "",
            session.register.name,
            f"{L['opened']}{sep}{when(session.opened_at)}",
            width,
            18,
        ),
        Spacer(1, 5 * mm),
    ]
    if session.status == "open":
        story += [Paragraph(L["still_open"].format(when=when(timezone.now())), WARN), Spacer(1, 3 * mm)]

    def name(user) -> str:
        return escape(user.full_name) if user else "—"

    info = [
        [Paragraph(L["opened_by"], SUB), Paragraph(name(session.created_by), CELL)],
    ]
    if session.closed_at:
        info += [
            [Paragraph(L["closed"], SUB), Paragraph(when(session.closed_at), CELL)],
            [Paragraph(L["closed_by"], SUB), Paragraph(name(session.closed_by), CELL)],
        ]
    info_table = Table(info, colWidths=[36 * mm, width - 36 * mm])
    info_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), LIGHT)]))
    story += [info_table, Spacer(1, 5 * mm)]

    movements = list(session.movements.all())
    rows = [
        [
            Paragraph(f"<b>{h}</b>", NUM if i >= 3 else CELL)
            for i, h in enumerate((L["time"], L["description"], L["by"], L["in"], L["out"]))
        ]
    ]
    for movement in movements:
        local = timezone.localtime(movement.created_at, zone)
        amount = Paragraph(money(movement.amount), NUM)
        rows.append(
            [
                Paragraph(f"{local:%H:%M}", CELL),
                Paragraph(escape(movement.description), CELL),
                Paragraph(name(movement.created_by), CELL),
                amount if movement.direction == "in" else "",
                amount if movement.direction == "out" else "",
            ]
        )
    if not movements:
        rows.append([Paragraph(L["none"], SUB), "", "", "", ""])
    table = Table(rows, colWidths=[16 * mm, width - 112 * mm, 36 * mm, 30 * mm, 30 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, LIGHT),
                ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story += [table, Spacer(1, 5 * mm)]

    figures = services.totals(session)
    summary = [
        (L["opening"], session.opening_balance, NUM),
        (L["total_in"], figures["money_in"], NUM),
        (L["total_out"], -figures["money_out"], NUM),
        (L["expected"], figures["expected"], BOLD_NUM),
    ]
    if session.counted_closing is not None:
        summary += [
            (L["counted"], session.counted_closing, BOLD_NUM),
            (L["difference"], session.difference, NUM),
        ]
    totals_table = Table(
        [[Paragraph(label, style), Paragraph(money(value), style)] for label, value, style in summary],
        colWidths=[width - 50 * mm, 50 * mm],
    )
    totals_table.setStyle(TableStyle([("LINEABOVE", (0, 3), (-1, 3), 0.6, NAVY)]))
    story.append(totals_table)
    if session.closing_note:
        story += [
            Spacer(1, 3 * mm),
            Paragraph(f"<b>{L['note']}</b>{sep}{escape(session.closing_note)}", CELL),
        ]

    signatures = Table(
        [[Paragraph(L["cashier"], SUB), Paragraph(L["checked"], SUB)]],
        colWidths=[width / 2, width / 2],
        rowHeights=[22 * mm],
    )
    signatures.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [Spacer(1, 8 * mm), signatures]
    doc.build(story)
    return buffer.getvalue()
