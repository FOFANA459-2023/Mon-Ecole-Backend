import io

from reportlab.lib.colors import white
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from apps.core.pdf import LIGHT, MUTED, NAVY, TEAL, format_date, image_reader, labels_for, pdf_response

__all__ = ["pdf_response", "student_cards_pdf"]

CARD_W, CARD_H = 85.6 * mm, 54 * mm  # ISO/IEC 7810 ID-1 (bank card size)


def _fit(c, text: str, font: str, size: float, max_width: float) -> tuple[str, float]:
    """Shrink the font (down to 6pt) and then truncate so text fits on one line."""
    while size > 6 and c.stringWidth(text, font, size) > max_width:
        size -= 0.5
    while text and c.stringWidth(text, font, size) > max_width:
        text = text[:-2] + "…"
    return text, size


def _draw_card(c, x: float, y: float, student, enrollment, school, labels, logo) -> None:
    c.saveState()
    c.setFillColor(white)
    c.setStrokeColor(LIGHT)
    c.roundRect(x, y, CARD_W, CARD_H, 3 * mm, stroke=1, fill=1)

    # Header band (rounded top corners, square bottom) with logo and school name.
    band = 13 * mm
    c.setFillColor(NAVY)
    c.roundRect(x, y + CARD_H - band, CARD_W, band, 3 * mm, stroke=0, fill=1)
    c.rect(x, y + CARD_H - band, CARD_W, band / 2, stroke=0, fill=1)
    text_x = x + 4 * mm
    if logo:
        c.drawImage(
            logo,
            x + 3 * mm,
            y + CARD_H - band + 2 * mm,
            9 * mm,
            9 * mm,
            preserveAspectRatio=True,
            mask="auto",
        )
        text_x = x + 14 * mm
    c.setFillColor(white)
    name, size = _fit(c, school.name, "Helvetica-Bold", 9, CARD_W - (text_x - x) - 3 * mm)
    c.setFont("Helvetica-Bold", size)
    c.drawString(text_x, y + CARD_H - 6.5 * mm, name)
    c.setFont("Helvetica", 6)
    c.drawString(text_x, y + CARD_H - 10 * mm, labels["student_card"])

    # Photo (or initials).
    photo_x, photo_y, photo_w, photo_h = x + 4 * mm, y + 6 * mm, 21 * mm, 27 * mm
    photo = image_reader(student.photo)
    if photo:
        c.drawImage(
            photo, photo_x, photo_y, photo_w, photo_h, preserveAspectRatio=True, anchor="c", mask="auto"
        )
    else:
        c.setFillColor(LIGHT)
        c.rect(photo_x, photo_y, photo_w, photo_h, stroke=0, fill=1)
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 14)
        initials = (student.first_name[:1] + student.last_name[:1]).upper()
        c.drawCentredString(photo_x + photo_w / 2, photo_y + photo_h / 2 - 5, initials)

    # Details.
    info_x = x + 29 * mm
    width = CARD_W - 33 * mm
    c.setFillColor(NAVY)
    full, size = _fit(c, f"{student.last_name.upper()} {student.first_name}", "Helvetica-Bold", 10, width)
    c.setFont("Helvetica-Bold", size)
    c.drawString(info_x, y + 30 * mm, full)
    rows = [
        (labels["student_number"], student.student_number),
        (labels["class"], enrollment.class_group.name if enrollment else "—"),
        (labels["year"], enrollment.academic_year.name if enrollment else "—"),
        (labels["born"], format_date(student.date_of_birth, school) or "—"),
    ]
    line_y = y + 24.5 * mm
    for label, value in rows:
        c.setFont("Helvetica", 6)
        c.setFillColor(MUTED)
        c.drawString(info_x, line_y, label)
        c.setFont("Helvetica-Bold", 7.5)
        c.setFillColor(NAVY)
        value, size = _fit(c, str(value), "Helvetica-Bold", 7.5, width - 20 * mm)
        c.setFont("Helvetica-Bold", size)
        c.drawString(info_x + 20 * mm, line_y, value)
        line_y -= 4.6 * mm

    # Accent stripe.
    c.setFillColor(TEAL)
    c.rect(x + 3 * mm, y + 2.5 * mm, CARD_W - 6 * mm, 0.8 * mm, stroke=0, fill=1)
    c.restoreState()


def _current_enrollment(student):
    active = getattr(student, "active_enrollments", None)
    if active is None:
        active = list(
            student.enrollments.filter(status="active").select_related("academic_year", "class_group")
        )
    return max(active, key=lambda e: e.academic_year.start_date, default=None)


def student_cards_pdf(school, students) -> bytes:
    """One card on its own page for a single student; an A4 sheet of 10 cards (2 × 5) otherwise."""
    labels = labels_for(school)
    logo = image_reader(school.logo)
    buffer = io.BytesIO()
    students = list(students)
    if len(students) == 1:
        c = canvas.Canvas(buffer, pagesize=(CARD_W, CARD_H))
        _draw_card(c, 0, 0, students[0], _current_enrollment(students[0]), school, labels, logo)
        c.showPage()
    else:
        c = canvas.Canvas(buffer, pagesize=A4)
        page_w, page_h = A4
        margin_x = (page_w - 2 * CARD_W - 6 * mm) / 2
        margin_y = (page_h - 5 * CARD_H - 4 * 4 * mm) / 2
        for index, student in enumerate(students):
            slot = index % 10
            if index and slot == 0:
                c.showPage()
            col, row = slot % 2, slot // 2
            x = margin_x + col * (CARD_W + 6 * mm)
            y = page_h - margin_y - (row + 1) * CARD_H - row * 4 * mm
            _draw_card(c, x, y, student, _current_enrollment(student), school, labels, logo)
        c.showPage()
    c.setTitle(labels["student_card"])
    c.save()
    return buffer.getvalue()
