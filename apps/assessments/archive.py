"""Issued report cards: every printed card is stored, and a reprint of an unchanged card is the same file.

Each student's card is rendered on its own page and stored as a `ReportCard`. Printing again reuses the
stored page as long as nothing on it has changed; when something has (marks reopened and published again,
a comment, attendance corrected...), the card is issued again as a new version and the old ones are kept.
A class PDF is the issued pages put together, so what was handed out is exactly what the school keeps.

Printing a whole level or school runs as a background job (`run_report_cards_job`): one PDF per class,
in a ZIP when there are several classes.
"""

import hashlib
import io
import json
import zipfile
from datetime import date
from decimal import Decimal
from typing import Any

from django.core.files.base import ContentFile
from django.db import models, transaction
from django.utils.text import slugify
from pypdf import PdfReader, PdfWriter

from apps.academics.models import ClassGroup, Term
from apps.audit import services as audit
from apps.core import jobs
from apps.enrollments.models import Enrollment

from .models import ReportCard
from .reportcards import LABELS, annual_cards, report_cards_pdf, term_cards
from .services import IN_CLASS


def _plain(value):
    if isinstance(value, Decimal):
        return str(value.normalize())
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, models.Model):
        return value.pk
    raise TypeError(type(value).__name__)


def fingerprint(card: dict[str, Any], data: dict[str, Any]) -> str:
    """A hash of everything printed on one card (but not the print date)."""
    enrollment = card["enrollment"]
    student = enrollment.student
    class_group = data["class_group"]
    school = class_group.school
    teacher = class_group.class_teacher
    scale = data["scale"]
    content = {
        "school": [school.name, school.address, school.phone, school.email, str(school.logo or "")],
        "language": school.default_language,
        "class": [class_group.name, class_group.academic_year.name, teacher.full_name if teacher else ""],
        "student": [student.full_name, student.student_number, student.date_of_birth],
        "period": data["term"].name if data["kind"] == "term" else [t.name for t in data["terms"]],
        "scale": [scale.max_mark, scale.decimals],
        "class_size": data["class_size"],
        "ranked": data["ranked"],
        "stats": data["stats"],
        "card": {key: value for key, value in card.items() if key != "enrollment"},
    }
    encoded = json.dumps(content, default=_plain, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


Issued = tuple[ReportCard, bytes, bool]


def _issue_one(card: dict[str, Any], data: dict[str, Any], term: Term | None, user) -> Issued:
    enrollment = card["enrollment"]
    digest = fingerprint(card, data)
    with transaction.atomic():
        # One issuer at a time per student, so two printers never take the same version number.
        list(Enrollment.objects.select_for_update().filter(pk=enrollment.pk).values_list("pk", flat=True))
        latest = ReportCard.objects.filter(enrollment=enrollment, term=term).order_by("-version").first()
        if latest is not None and latest.fingerprint == digest:
            with latest.file.open("rb") as stored:
                return latest, stored.read(), False
        content = report_cards_pdf({**data, "cards": [card]})
        issued = ReportCard(
            school=enrollment.school,
            created_by=user,
            enrollment=enrollment,
            term=term,
            version=latest.version + 1 if latest else 1,
            fingerprint=digest,
            average=card["average"],
            rank=card["rank"],
            size=len(content),
        )
        issued.file.save(f"{enrollment.pk}.pdf", ContentFile(content), save=False)
        issued.save()
    return issued, content, True


def issue(data: dict[str, Any], *, user=None) -> list[Issued]:
    """Store every card of `data` (from term_cards / annual_cards): (card, its PDF, newly issued?)."""
    term = data["term"] if data["kind"] == "term" else None
    return [_issue_one(card, data, term, user) for card in data["cards"]]


def merge(pages: list[bytes], title: str) -> bytes:
    writer = PdfWriter()
    for content in pages:
        writer.append(PdfReader(io.BytesIO(content)))
    writer.add_metadata({"/Title": title})
    # A school logo repeated on every page is stored once.
    writer.compress_identical_objects()
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _title(class_group: ClassGroup, term: Term | None) -> str:
    language = class_group.school.default_language
    L = LABELS.get(language, LABELS["fr"])
    period = term.name if term else class_group.academic_year.name
    return f"{L['title'] if term else L['annual_title']} — {class_group.name} — {period}"


def period_slug(term: Term | None) -> str:
    return slugify(term.name) if term else "annuel"


def class_pdf(data: dict[str, Any], *, user=None) -> tuple[bytes, list[Issued]]:
    """Issue a class's (or one student's) cards and put the pages together in one PDF."""
    class_group = data["class_group"]
    term = data["term"] if data["kind"] == "term" else None
    issued = issue(data, user=user)
    if not issued:
        return report_cards_pdf(data), issued  # a page saying nothing is published yet
    return merge([content for _, content, _ in issued], _title(class_group, term)), issued


def report_card_filename(card: ReportCard) -> str:
    student = card.enrollment.student
    return f"bulletin-{slugify(student.full_name)}-{period_slug(card.term)}-v{card.version}.pdf"


# --- Background job: a level or the whole school ---------------------------------------------------------


def count_students(class_ids: list[int]) -> int:
    return Enrollment.objects.filter(class_group__in=class_ids, status__in=IN_CLASS).count()


def run_report_cards_job(job) -> None:
    """params: {"class_groups": [ids], "term": id or None}. Result: one PDF, or a ZIP of one PDF per class."""
    params = job.params
    classes = list(
        ClassGroup.objects.filter(school=job.school, pk__in=params["class_groups"])
        .select_related("academic_year", "level", "class_teacher", "school")
        .order_by("level__order", "name")
    )
    term = Term.objects.filter(school=job.school, pk=params["term"]).first() if params.get("term") else None
    jobs.progress(job, 0, count_students([c.pk for c in classes]))
    files: list[tuple[str, bytes]] = []
    done = new = 0
    for class_group in classes:
        data = term_cards(class_group, term) if term else annual_cards(class_group)
        content, issued = class_pdf(data, user=job.created_by)
        files.append((f"bulletins-{slugify(class_group.name)}-{period_slug(term)}.pdf", content))
        done += len(issued)
        new += sum(1 for _, _, created in issued if created)
        jobs.progress(job, done)

    period = term.name if term else "year"
    audit.record(
        "export",
        school=job.school,
        user=job.created_by,
        module="grades",
        entity_type="report_cards",
        entity_id=job.pk,
        summary=f"Report cards printed: {len(classes)} classes, {period} ({done} students, {new} new)",
    )
    if len(files) == 1:
        filename, content = files[0]
    else:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, pdf in files:
                archive.writestr(name, pdf)
        filename, content = f"bulletins-{period_slug(term)}.zip", buffer.getvalue()
    jobs.finish(
        job, content=content, filename=filename, outcome={"classes": len(classes), "cards": done, "new": new}
    )
