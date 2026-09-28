import io

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.core.pdf import LIGHT, MUTED, NAVY, format_date, image_reader, labels_for
from apps.documents.models import Document

styles = getSampleStyleSheet()
TITLE = ParagraphStyle(
    "title",
    parent=styles["Title"],
    fontName="Helvetica-Bold",
    fontSize=15,
    textColor=NAVY,
    alignment=0,
    spaceAfter=2,
)
SUB = ParagraphStyle("sub", parent=styles["Normal"], fontSize=8.5, textColor=MUTED, leading=11)
H = ParagraphStyle(
    "h",
    parent=styles["Heading3"],
    fontName="Helvetica-Bold",
    fontSize=10.5,
    textColor=NAVY,
    spaceBefore=10,
    spaceAfter=4,
)
CELL = ParagraphStyle("cell", parent=styles["Normal"], fontSize=8.5, leading=11)
LABEL = ParagraphStyle("label", parent=CELL, textColor=MUTED)


def _grid(rows, widths):
    table = Table(
        [
            [
                Paragraph(str(a), LABEL),
                Paragraph(str(b or "—"), CELL),
                Paragraph(str(c), LABEL),
                Paragraph(str(d or "—"), CELL),
            ]
            for a, b, c, d in rows
        ],
        colWidths=widths,
    )
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, LIGHT),
                ("BACKGROUND", (0, 0), (0, -1), LIGHT),
                ("BACKGROUND", (2, 0), (2, -1), LIGHT),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def enrollment_form_pdf(enrollment) -> bytes:
    school = enrollment.school
    student = enrollment.student
    L = labels_for(school)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"{L['enrolment_form']} — {student.full_name}",
    )
    width = A4[0] - 32 * mm
    story = []

    # Header: logo + school details, and the student's photo on the right.
    logo = image_reader(school.logo)
    photo = image_reader(student.photo)
    contact = " · ".join(filter(None, [school.address, school.phone, school.email]))
    header_text = [Paragraph(school.name, TITLE), Paragraph(contact, SUB)]
    if school.registration_number:
        header_text.append(Paragraph(school.registration_number, SUB))
    header = Table(
        [
            [
                Image(logo, 18 * mm, 18 * mm, kind="proportional") if logo else "",
                header_text,
                Image(photo, 24 * mm, 30 * mm, kind="proportional") if photo else "",
            ]
        ],
        colWidths=[22 * mm, width - 50 * mm, 28 * mm],
    )
    header.setStyle(
        TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)])
    )
    story += [header, Spacer(1, 6 * mm)]
    story.append(Paragraph(f"{L['enrolment_form']} — {enrollment.academic_year.name}", TITLE))
    story.append(Paragraph(f"{L['student_number']} : <b>{student.student_number}</b>", SUB))

    col = [30 * mm, width / 2 - 30 * mm, 30 * mm, width / 2 - 30 * mm]
    story.append(Paragraph(L["student"], H))
    story.append(
        _grid(
            [
                (L["last_name"], student.last_name.upper(), L["first_name"], student.first_name),
                (
                    L["gender"],
                    L.get(student.gender, ""),
                    L["date_of_birth"],
                    format_date(student.date_of_birth, school),
                ),
                (L["place_of_birth"], student.place_of_birth, L["nationality"], student.nationality),
                (L["phone"], student.phone, L["email"], student.email),
                (L["address"], student.address, "", ""),
            ],
            col,
        )
    )

    story.append(Paragraph(L["schooling"], H))
    story.append(
        _grid(
            [
                (L["year"], enrollment.academic_year.name, L["level"], enrollment.class_group.level.name),
                (
                    L["class"],
                    enrollment.class_group.name,
                    L["enrollment_date"],
                    format_date(enrollment.enrollment_date, school),
                ),
                (
                    L["kind"],
                    L.get(enrollment.kind, enrollment.kind),
                    L["previous_school"],
                    enrollment.previous_school,
                ),
            ],
            col,
        )
    )

    story.append(Paragraph(L["guardians"], H))
    links = list(student.guardian_links.select_related("guardian"))
    if links:
        rows = [
            [
                Paragraph(f"<b>{h}</b>", CELL)
                for h in (L["relationship"], L["name"], L["phone"], L["email"], L["occupation"])
            ]
        ]
        for link in links:
            g = link.guardian
            rows.append(
                [
                    Paragraph(x or "—", CELL)
                    for x in (
                        L.get(link.relationship, link.relationship) + (" ★" if link.is_primary else ""),
                        g.full_name,
                        " / ".join(filter(None, [g.phone, g.alt_phone])),
                        g.email,
                        g.occupation,
                    )
                ]
            )
        table = Table(rows, colWidths=[24 * mm, 46 * mm, 36 * mm, 42 * mm, width - 148 * mm], repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.4, LIGHT),
                    ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        story.append(table)
    else:
        story.append(Paragraph(L["none"], CELL))

    story.append(Paragraph(L["documents"], H))
    documents = Document.objects.filter(school=school, owner_type="student", owner_id=student.pk)
    names = [d.title for d in documents]
    story.append(Paragraph(" · ".join(names) if names else L["none"], CELL))

    story.append(Spacer(1, 14 * mm))
    signatures = Table(
        [
            [Paragraph(L["signature_guardian"], LABEL), Paragraph(L["signature_school"], LABEL)],
            ["", ""],
            [
                Paragraph(f"{L['done_on']} ____ / ____ / ________", LABEL),
                Paragraph(f"{L['done_on']} ____ / ____ / ________", LABEL),
            ],
        ],
        colWidths=[width / 2, width / 2],
        rowHeights=[None, 22 * mm, None],
    )
    signatures.setStyle(TableStyle([("LINEBELOW", (0, 1), (-1, 1), 0.5, MUTED)]))
    story.append(signatures)

    doc.build(story)
    return buffer.getvalue()
