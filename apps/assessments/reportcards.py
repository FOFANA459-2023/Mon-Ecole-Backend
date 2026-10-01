"""Report cards: what each student's card says (from published marks only) and the printed PDF.

A term card lists every published subject with its coefficient, the student's mark, the mark times the
coefficient, the class average and the student's rank in the subject, then the overall average, rank,
honours band, the term's attendance and the general comment. The annual card shows each term's mark per
subject, the annual subject marks (average of the terms), the term averages and the annual average (the
average of the term averages), the rank and the decision (pass mark reached or not).
"""

import io
from collections import Counter
from decimal import Decimal
from typing import Any
from xml.sax.saxutils import escape

from django.db.models import Count, Q
from django.utils import timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from apps.academics.models import ClassGroup, Term
from apps.core.pdf import LIGHT, MUTED, NAVY, format_date
from apps.enrollments.models import Enrollment
from apps.finance.pdf import document_header

from . import engine
from .models import ReportComment
from .selectors import class_results
from .services import engine_scale, scale_for

LABELS = {
    "fr": {
        "title": "BULLETIN DE NOTES",
        "annual_title": "BULLETIN ANNUEL",
        "term": "Période",
        "year": "Année scolaire",
        "annual": "Année",
        "student": "Élève",
        "student_number": "Matricule",
        "class": "Classe",
        "class_teacher": "Professeur principal",
        "class_size": "Effectif",
        "born": "Né(e) le",
        "subject": "Matière",
        "coef": "Coef.",
        "mark": "Note /{max}",
        "weighted": "Note × coef.",
        "class_average": "Moy. classe",
        "rank": "Rang",
        "appreciation": "Appréciation",
        "total": "Total",
        "average": "Moyenne",
        "general_rank": "Rang",
        "of": "sur",
        "tie": "ex aequo",
        "class_avg_long": "Moyenne de la classe",
        "highest": "Plus forte moyenne",
        "lowest": "Plus faible moyenne",
        "mention": "Mention",
        "decision": "Décision",
        "passed": "Admis(e)",
        "failed": "Non admis(e)",
        "attendance": "Assiduité",
        "absences": "Absences",
        "lates": "Retards",
        "excused": "dont excusées",
        "comment": "Appréciation générale",
        "sign_teacher": "Le professeur principal",
        "sign_director": "Le chef d'établissement",
        "sign_parent": "Le parent / tuteur",
        "term_average": "Moyenne de la période",
        "annual_mark": "Annuel",
        "none": "Aucune note publiée pour cette période.",
        "printed": "Édité le",
    },
    "en": {
        "title": "REPORT CARD",
        "annual_title": "ANNUAL REPORT CARD",
        "term": "Term",
        "year": "School year",
        "annual": "Year",
        "student": "Student",
        "student_number": "Student no.",
        "class": "Class",
        "class_teacher": "Class teacher",
        "class_size": "Class size",
        "born": "Born",
        "subject": "Subject",
        "coef": "Coef.",
        "mark": "Mark /{max}",
        "weighted": "Mark × coef.",
        "class_average": "Class avg.",
        "rank": "Rank",
        "appreciation": "Remark",
        "total": "Total",
        "average": "Average",
        "general_rank": "Rank",
        "of": "of",
        "tie": "tie",
        "class_avg_long": "Class average",
        "highest": "Highest average",
        "lowest": "Lowest average",
        "mention": "Honours",
        "decision": "Decision",
        "passed": "Passed",
        "failed": "Failed",
        "attendance": "Attendance",
        "absences": "Absences",
        "lates": "Late arrivals",
        "excused": "of which excused",
        "comment": "General comment",
        "sign_teacher": "Class teacher",
        "sign_director": "Head of school",
        "sign_parent": "Parent / guardian",
        "term_average": "Term average",
        "annual_mark": "Year",
        "none": "No published marks for this period.",
        "printed": "Printed on",
    },
}


def _language(school) -> str:
    return school.default_language if school.default_language in LABELS else "fr"


def _attendance(enrollment_ids: list[int], start, end) -> dict[int, dict[str, int]]:
    from apps.attendance.models import AttendanceRecord

    rows = (
        AttendanceRecord.objects.filter(enrollment__in=enrollment_ids, register__date__range=(start, end))
        .values("enrollment")
        .annotate(
            absent=Count("pk", filter=Q(status__in=["absent", "excused"])),
            excused=Count("pk", filter=Q(status="excused")),
            late=Count("pk", filter=Q(status="late")),
        )
    )
    return {row["enrollment"]: row for row in rows}


def _comments(enrollment_ids: list[int], term: Term | None) -> dict[int, str]:
    comments = ReportComment.objects.filter(enrollment__in=enrollment_ids)
    comments = comments.filter(term=term) if term else comments.filter(term__isnull=True)
    return dict(comments.values_list("enrollment", "comment"))


def _ties(ranking: dict) -> set[int]:
    counts = Counter(ranking.values())
    return {rank for rank, n in counts.items() if n > 1}


def term_cards(class_group: ClassGroup, term: Term, enrollment: Enrollment | None = None) -> dict[str, Any]:
    """Every student's term card (or one student's): published subjects only."""
    results = class_results(class_group, term, published_only=True)
    scale_model = results["scale"]
    bands = scale_model.mentions or []
    subjects = {s["class_subject"]: s for s in results["subjects"]}
    students = results["students"]
    ids = [s["enrollment"] for s in students]
    ranked = {s["enrollment"]: s["rank"] for s in students if s["rank"] is not None}
    subject_ties = {
        cs: _ties(
            {
                s["enrollment"]: m["rank"]
                for s in students
                for m in s["marks"]
                if m["class_subject"] == cs and m["rank"]
            }
        )
        for cs in subjects
    }
    attendance = _attendance(ids, term.start_date, term.end_date)
    comments = _comments(ids, term)
    enrollments = {
        e.pk: e
        for e in Enrollment.objects.filter(pk__in=ids).select_related("student", "class_group__class_teacher")
    }
    cards = []
    for row in students:
        if enrollment is not None and row["enrollment"] != enrollment.pk:
            continue
        lines = []
        for mark in row["marks"]:
            subject = subjects[mark["class_subject"]]
            value = mark["mark"]
            lines.append(
                {
                    "subject": subject["subject_name"],
                    "teacher": subject["teacher_name"],
                    "coefficient": subject["coefficient"],
                    "mark": value,
                    "weighted": None if value is None else value * subject["coefficient"],
                    "class_average": subject["stats"]["average"],
                    "rank": mark["rank"],
                    "tie": mark["rank"] in subject_ties[mark["class_subject"]],
                    "appreciation": engine.mention(value, bands),
                }
            )
        counted = [line for line in lines if line["mark"] is not None]
        cards.append(
            {
                "enrollment": enrollments[row["enrollment"]],
                "lines": lines,
                "total_coefficients": sum((line["coefficient"] for line in counted), Decimal(0)),
                "total_weighted": sum((line["weighted"] for line in counted), Decimal(0)),
                "average": row["average"],
                "rank": row["rank"],
                "tie": row["rank"] in _ties(ranked),
                "mention": engine.mention(row["average"], bands),
                "passed": row["passed"],
                "attendance": attendance.get(row["enrollment"], {"absent": 0, "excused": 0, "late": 0}),
                "comment": comments.get(row["enrollment"], ""),
            }
        )
    return {
        "kind": "term",
        "class_group": class_group,
        "term": term,
        "scale": scale_model,
        "class_size": len(students),
        "ranked": len(ranked),
        "stats": results["stats"],
        "cards": cards,
    }


def annual_cards(class_group: ClassGroup, enrollment: Enrollment | None = None) -> dict[str, Any]:
    """Every student's annual card (or one student's): the average of the term averages."""
    scale_model = scale_for(class_group.school, class_group.level)
    scale = engine_scale(scale_model)
    bands = scale_model.mentions or []
    terms = list(class_group.academic_year.terms.order_by("order"))
    per_term = {t.pk: class_results(class_group, t, published_only=True) for t in terms}
    students = per_term[terms[0].pk]["students"] if terms else []
    ids = [s["enrollment"] for s in students]
    # Subjects in the order of their first appearance, with each term's mark per student.
    subjects: dict[int, dict[str, Any]] = {}
    marks: dict[tuple[int, int, int], Decimal | None] = {}
    averages: dict[tuple[int, int], Decimal | None] = {}
    for term in terms:
        result = per_term[term.pk]
        for subject in result["subjects"]:
            subjects.setdefault(subject["class_subject"], subject)
        for row in result["students"]:
            averages[(row["enrollment"], term.pk)] = row["average"]
            for mark in row["marks"]:
                marks[(row["enrollment"], mark["class_subject"], term.pk)] = mark["mark"]
    annual = {pk: engine.mean((averages.get((pk, t.pk)) for t in terms), scale.decimals) for pk in ids}
    ranking = engine.ranks(annual, scale.rank_method)
    ties = _ties(ranking)
    attendance = (
        _attendance(ids, class_group.academic_year.start_date, class_group.academic_year.end_date)
        if ids
        else {}
    )
    comments = _comments(ids, None)
    enrollments = {
        e.pk: e
        for e in Enrollment.objects.filter(pk__in=ids).select_related("student", "class_group__class_teacher")
    }
    cards = []
    for pk in ids:
        if enrollment is not None and pk != enrollment.pk:
            continue
        average = annual[pk]
        lines = []
        for cs, subject in subjects.items():
            by_term = [marks.get((pk, cs, t.pk)) for t in terms]
            year_mark = engine.mean(by_term, scale.decimals)
            lines.append(
                {
                    "subject": subject["subject_name"],
                    "teacher": subject["teacher_name"],
                    "coefficient": subject["coefficient"],
                    "terms": by_term,
                    "mark": year_mark,
                    "appreciation": engine.mention(year_mark, bands),
                }
            )
        cards.append(
            {
                "enrollment": enrollments[pk],
                "lines": lines,
                "term_averages": [averages.get((pk, t.pk)) for t in terms],
                "average": average,
                "rank": ranking.get(pk),
                "tie": ranking.get(pk) in ties,
                "mention": engine.mention(average, bands),
                "passed": None if average is None else average >= scale.pass_mark,
                "attendance": attendance.get(pk, {"absent": 0, "excused": 0, "late": 0}),
                "comment": comments.get(pk, ""),
            }
        )
    return {
        "kind": "annual",
        "class_group": class_group,
        "terms": terms,
        "scale": scale_model,
        "class_size": len(students),
        "ranked": len(ranking),
        "stats": {
            "average": engine.mean(annual.values(), scale.decimals),
            "highest": max((v for v in annual.values() if v is not None), default=None),
            "lowest": min((v for v in annual.values() if v is not None), default=None),
        },
        "cards": cards,
    }


# --- PDF ---------------------------------------------------------------------------------------------------

styles = getSampleStyleSheet()
CELL = ParagraphStyle("rc-cell", parent=styles["Normal"], fontSize=8.5, leading=10.5)
SMALL = ParagraphStyle("rc-small", parent=CELL, fontSize=7, leading=8.5, textColor=MUTED)
NUM = ParagraphStyle("rc-num", parent=CELL, alignment=1)
BOLD = ParagraphStyle("rc-bold", parent=CELL, fontName="Helvetica-Bold")
BOLD_NUM = ParagraphStyle("rc-boldnum", parent=NUM, fontName="Helvetica-Bold")
BIG = ParagraphStyle(
    "rc-big", parent=CELL, fontName="Helvetica-Bold", fontSize=13, leading=16, textColor=NAVY
)
HEAD = ParagraphStyle("rc-head", parent=CELL, fontName="Helvetica-Bold", fontSize=7.5, leading=9, alignment=1)
HEAD_LEFT = ParagraphStyle("rc-headleft", parent=HEAD, alignment=0)


def _number(value, decimals: int, language: str) -> str:
    if value is None:
        return "—"
    text = f"{Decimal(value):.{decimals}f}"
    return text.replace(".", ",") if language == "fr" else text


def _coefficient(value, language: str) -> str:
    """4 rather than 4.0; 2,5 in French."""
    text = f"{Decimal(value).normalize():f}"
    return text.replace(".", ",") if language == "fr" else text


def _ordinal(rank: int | None, tie: bool, language: str) -> str:
    if rank is None:
        return "—"
    if language == "fr":
        text = "1er" if rank == 1 else f"{rank}e"
    else:
        suffix = "th" if 10 <= rank % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(rank % 10, "th")
        text = f"{rank}{suffix}"
    return f"{text} {LABELS[language]['tie']}" if tie else text


def _grid(rows, widths, *, header_rows=1, total_rows=0):
    table = Table(rows, colWidths=widths, repeatRows=header_rows)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.4, LIGHT),
        ("BACKGROUND", (0, 0), (-1, header_rows - 1), LIGHT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]
    if total_rows:
        style.append(("BACKGROUND", (0, -total_rows), (-1, -1), LIGHT))
    table.setStyle(TableStyle(style))
    return table


def _student_box(card, data, L, width, school):
    enrollment = card["enrollment"]
    student = enrollment.student
    teacher = enrollment.class_group.class_teacher
    pairs = [
        (
            L["student"],
            f"<b>{escape(student.full_name)}</b>",
            L["student_number"],
            escape(student.student_number),
        ),
        (
            L["class"],
            escape(enrollment.class_group.name),
            L["class_teacher"],
            escape(teacher.full_name) if teacher else "—",
        ),
        (
            L["born"],
            format_date(student.date_of_birth, school) or "—",
            L["class_size"],
            str(data["class_size"]),
        ),
    ]
    rows = [
        [Paragraph(a, SMALL), Paragraph(b, CELL), Paragraph(c, SMALL), Paragraph(d, CELL)]
        for a, b, c, d in pairs
    ]
    box = Table(rows, colWidths=[30 * mm, width / 2 - 30 * mm, 34 * mm, width / 2 - 34 * mm])
    box.setStyle(
        TableStyle([("BACKGROUND", (0, 0), (-1, -1), LIGHT), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")])
    )
    return box


def _summary(card, data, L, language, decimals, width, *, annual: bool):
    scale = data["scale"]
    num = lambda v: _number(v, decimals, language)  # noqa: E731
    left = [
        [Paragraph(L["average"], CELL), Paragraph(f"{num(card['average'])} / {num(scale.max_mark)}", BIG)],
        [
            Paragraph(L["general_rank"], CELL),
            Paragraph(
                f"<b>{_ordinal(card['rank'], card['tie'], language)}</b> {L['of']} {data['ranked']}", CELL
            ),
        ],
    ]
    if card["mention"]:
        left.append([Paragraph(L["mention"], CELL), Paragraph(f"<b>{escape(card['mention'])}</b>", CELL)])
    if annual and card["passed"] is not None:
        left.append(
            [
                Paragraph(L["decision"], CELL),
                Paragraph(f"<b>{L['passed'] if card['passed'] else L['failed']}</b>", CELL),
            ]
        )
    stats = data["stats"]
    right = [
        [Paragraph(L["class_avg_long"], SMALL), Paragraph(num(stats["average"]), CELL)],
        [Paragraph(L["highest"], SMALL), Paragraph(num(stats["highest"]), CELL)],
        [Paragraph(L["lowest"], SMALL), Paragraph(num(stats["lowest"]), CELL)],
    ]
    attendance = card["attendance"]
    right.append(
        [
            Paragraph(L["attendance"], SMALL),
            Paragraph(
                f"{L['absences']} : {attendance['absent']} ({L['excused']} {attendance['excused']}) · "
                f"{L['lates']} : {attendance['late']}"
                if language == "fr"
                else f"{L['absences']}: {attendance['absent']} ({L['excused']} {attendance['excused']}) · "
                f"{L['lates']}: {attendance['late']}",
                CELL,
            ),
        ]
    )
    half = width / 2 - 2 * mm
    left_table = Table(left, colWidths=[30 * mm, half - 30 * mm])
    right_table = Table(right, colWidths=[36 * mm, half - 36 * mm])
    for table in (left_table, right_table):
        table.setStyle(
            TableStyle([("BOX", (0, 0), (-1, -1), 0.6, LIGHT), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")])
        )
    outer = Table([[left_table, right_table]], colWidths=[half + 2 * mm, half + 2 * mm])
    outer.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    return outer


def _comment_and_signatures(card, L, width):
    comment = Table(
        [[Paragraph(f"<b>{L['comment']}</b>", CELL)], [Paragraph(escape(card["comment"]) or "&nbsp;", CELL)]],
        colWidths=[width],
        rowHeights=[None, 16 * mm],
    )
    comment.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, LIGHT), ("VALIGN", (0, 1), (-1, -1), "TOP")]))
    signatures = Table(
        [
            [
                Paragraph(L["sign_teacher"], SMALL),
                Paragraph(L["sign_director"], SMALL),
                Paragraph(L["sign_parent"], SMALL),
            ]
        ],
        colWidths=[width / 3] * 3,
        rowHeights=[20 * mm],
    )
    signatures.setStyle(
        TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), 0.4, LIGHT)])
    )
    return [comment, Spacer(1, 3 * mm), signatures]


def _term_page(card, data, L, language, width, school):
    decimals = data["scale"].decimals
    num = lambda v: _number(v, decimals, language)  # noqa: E731
    max_mark = _number(data["scale"].max_mark, 0, language)
    header = [
        Paragraph(L["subject"], HEAD_LEFT),
        Paragraph(L["coef"], HEAD),
        Paragraph(L["mark"].format(max=max_mark), HEAD),
        Paragraph(L["weighted"], HEAD),
        Paragraph(L["class_average"], HEAD),
        Paragraph(L["rank"], HEAD),
        Paragraph(L["appreciation"], HEAD_LEFT),
    ]
    rows = [header]
    for line in card["lines"]:
        subject = f"<b>{escape(line['subject'])}</b>"
        if line["teacher"]:
            subject += f"<br/><font size=6.5 color='#5A6675'>{escape(line['teacher'])}</font>"
        rows.append(
            [
                Paragraph(subject, CELL),
                Paragraph(_coefficient(line["coefficient"], language), NUM),
                Paragraph(f"<b>{num(line['mark'])}</b>", NUM),
                Paragraph(num(line["weighted"]), NUM),
                Paragraph(num(line["class_average"]), NUM),
                Paragraph(_ordinal(line["rank"], line["tie"], language), NUM),
                Paragraph(escape(line["appreciation"]), CELL),
            ]
        )
    rows.append(
        [
            Paragraph(f"<b>{L['total']}</b>", CELL),
            Paragraph(_coefficient(card["total_coefficients"], language), BOLD_NUM),
            Paragraph("", NUM),
            Paragraph(num(card["total_weighted"]), BOLD_NUM),
            Paragraph("", NUM),
            Paragraph("", NUM),
            Paragraph("", CELL),
        ]
    )
    widths = [width - 134 * mm, 13 * mm, 19 * mm, 22 * mm, 20 * mm, 28 * mm, 32 * mm]
    return _grid(rows, widths, total_rows=1)


def _annual_page(card, data, L, language, width, school):
    decimals = data["scale"].decimals
    num = lambda v: _number(v, decimals, language)  # noqa: E731
    terms = data["terms"]
    term_width = min(20 * mm, 60 * mm / max(len(terms), 1))
    header = [Paragraph(L["subject"], HEAD_LEFT), Paragraph(L["coef"], HEAD)]
    header += [Paragraph(escape(t.name), HEAD) for t in terms]
    header += [Paragraph(L["annual_mark"], HEAD), Paragraph(L["appreciation"], HEAD_LEFT)]
    rows = [header]
    for line in card["lines"]:
        rows.append(
            [
                Paragraph(f"<b>{escape(line['subject'])}</b>", CELL),
                Paragraph(_coefficient(line["coefficient"], language), NUM),
                *[Paragraph(num(v), NUM) for v in line["terms"]],
                Paragraph(f"<b>{num(line['mark'])}</b>", NUM),
                Paragraph(escape(line["appreciation"]), CELL),
            ]
        )
    rows.append(
        [
            Paragraph(f"<b>{L['term_average']}</b>", CELL),
            Paragraph("", NUM),
            *[Paragraph(f"<b>{num(v)}</b>", NUM) for v in card["term_averages"]],
            Paragraph(f"<b>{num(card['average'])}</b>", NUM),
            Paragraph("", CELL),
        ]
    )
    fixed = 14 * mm + term_width * len(terms) + 20 * mm + 34 * mm
    widths = [width - fixed, 14 * mm, *([term_width] * len(terms)), 20 * mm, 34 * mm]
    return _grid(rows, widths, total_rows=1)


def report_cards_pdf(data: dict[str, Any]) -> bytes:
    """One A4 page per student (term or annual cards built above)."""
    class_group = data["class_group"]
    school = class_group.school
    language = _language(school)
    L = LABELS[language]
    annual = data["kind"] == "annual"
    period = L["annual"] if annual else data["term"].name
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=f"{L['annual_title'] if annual else L['title']} — {class_group.name} — {period}",
    )
    width = A4[0] - 28 * mm
    printed = timezone.localdate()
    story: list = []
    for index, card in enumerate(data["cards"]):
        if index:
            story.append(PageBreak())
        sep = " : " if language == "fr" else ": "
        story += [
            document_header(
                school,
                L["annual_title"] if annual else L["title"],
                L["year"] if annual else L["term"],
                class_group.academic_year.name if annual else period,
                f"{L['printed']}{sep}{format_date(printed, school)}"
                if annual
                else f"{L['year']}{sep}{class_group.academic_year.name}",
                width,
                18,
            ),
            Spacer(1, 4 * mm),
            _student_box(card, data, L, width, school),
            Spacer(1, 4 * mm),
        ]
        if card["lines"]:
            page = _annual_page if annual else _term_page
            story += [page(card, data, L, language, width, school), Spacer(1, 4 * mm)]
        else:
            story += [Paragraph(L["none"], CELL), Spacer(1, 4 * mm)]
        story += [
            _summary(card, data, L, language, data["scale"].decimals, width, annual=annual),
            Spacer(1, 4 * mm),
            *_comment_and_signatures(card, L, width),
        ]
    if not story:
        story.append(Paragraph(L["none"], CELL))
    doc.build(story)
    return buffer.getvalue()


def student_results(enrollment: Enrollment) -> dict[str, Any]:
    """One student's term averages, ranks and honours bands this year, and the year's (published marks)."""
    class_group = enrollment.class_group
    scale_model = scale_for(class_group.school, class_group.level)
    bands = scale_model.mentions or []
    rows = []
    for term in class_group.academic_year.terms.order_by("order"):
        result = class_results(class_group, term, published_only=True)
        row = next((s for s in result["students"] if s["enrollment"] == enrollment.pk), None)
        rows.append(
            {
                "term": term.pk,
                "term_name": term.name,
                "average": row["average"] if row else None,
                "rank": row["rank"] if row else None,
                "ranked": sum(1 for s in result["students"] if s["rank"] is not None),
                "mention": engine.mention(row["average"] if row else None, bands),
                "passed": row["passed"] if row else None,
                "subjects": len(result["subjects"]),
            }
        )
    year = annual_cards(class_group, enrollment)
    card = year["cards"][0] if year["cards"] else None
    rows.append(
        {
            "term": None,
            "term_name": "",
            "average": card["average"] if card else None,
            "rank": card["rank"] if card else None,
            "ranked": year["ranked"],
            "mention": card["mention"] if card else "",
            "passed": card["passed"] if card else None,
            "subjects": len(card["lines"]) if card else 0,
        }
    )
    return {
        "enrollment": enrollment.pk,
        "class_group": class_group.pk,
        "class_name": class_group.name,
        "scale": scale_model,
        "terms": rows,
    }
