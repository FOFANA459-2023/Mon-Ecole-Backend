"""Marks, ranks and statistics computed from a gradebook's rules (never stored, so always up to date)."""

from decimal import Decimal
from typing import Any

from django.db.models import Count, IntegerField, OuterRef, Prefetch, Q, Subquery
from django.db.models.functions import Coalesce

from apps.academics.models import ClassGroup, Term
from apps.enrollments.models import Enrollment

from . import engine
from .models import Assessment, Grade, Gradebook, GradeCategory
from .services import engine_scale, roster, scale_for


def with_counts(queryset):
    """Annotate gradebooks with how many assessments, students and marks they have."""
    students = (
        Enrollment.objects.filter(
            class_group=OuterRef("class_subject__class_group"), status=Enrollment.Status.ACTIVE
        )
        .order_by()
        .values("class_group")
        .annotate(n=Count("pk"))
        .values("n")
    )
    marks = (
        Grade.objects.filter(assessment__gradebook=OuterRef("pk"))
        .filter(Q(score__isnull=False) | Q(excused=True))
        .order_by()
        .values("assessment__gradebook")
        .annotate(n=Count("pk"))
        .values("n")
    )
    assessments = (
        Assessment.objects.filter(gradebook=OuterRef("pk"))
        .order_by()
        .values("gradebook")
        .annotate(n=Count("pk"))
        .values("n")
    )
    return queryset.annotate(
        student_count=Coalesce(Subquery(students, output_field=IntegerField()), 0),
        mark_count=Coalesce(Subquery(marks, output_field=IntegerField()), 0),
        assessment_count=Coalesce(Subquery(assessments, output_field=IntegerField()), 0),
    )


def _rules(gradebook: Gradebook) -> tuple[list[engine.Category], list[engine.Item]]:
    categories = [
        engine.Category(id=c.pk, weight=c.weight, method=c.method) for c in gradebook.categories.all()
    ]
    items = [
        engine.Item(id=a.pk, category_id=a.category_id, max_score=a.max_score, weight=a.weight)
        for a in gradebook.assessments.all()
    ]
    return categories, items


def _marks_by_student(grades) -> dict[int, dict[int, engine.Mark]]:
    marks: dict[int, dict[int, engine.Mark]] = {}
    for grade in grades:
        marks.setdefault(grade.enrollment_id, {})[grade.assessment_id] = engine.Mark(
            grade.score, grade.excused
        )
    return marks


def _started(grades) -> set[int]:
    return {g.assessment_id for g in grades if g.score is not None}


def _is_missing(mark: engine.Mark | None) -> bool:
    return mark is None or (mark.score is None and not mark.excused)


def subject_marks(
    gradebook: Gradebook, scale: engine.Scale, enrollment_ids: list[int], grades: list[Grade]
) -> dict[int, Decimal | None]:
    """Each student's mark in the subject, on the school's scale."""
    categories, items = _rules(gradebook)
    marks = _marks_by_student(grades)
    started = _started(grades)
    return {
        pk: engine.to_scale(
            engine.subject_fraction(
                categories, items, marks.get(pk, {}), missing_policy=gradebook.missing_policy, started=started
            ),
            scale,
        )
        for pk in enrollment_ids
    }


def _stats(values, scale: engine.Scale) -> dict:
    s = engine.stats(values, scale)
    return {
        "average": s.average,
        "lowest": s.lowest,
        "highest": s.highest,
        "passed": s.passed,
        "counted": s.counted,
    }


def gradebook_sheet(gradebook: Gradebook) -> dict:
    """Everything the marks grid shows: students, their marks, category and subject marks, ranks, stats."""
    level = gradebook.class_subject.class_group.level
    scale_model = scale_for(gradebook.school, level)
    scale = engine_scale(scale_model)
    categories, items = _rules(gradebook)
    students = roster(gradebook)
    grades = list(Grade.objects.filter(assessment__gradebook=gradebook))
    marks = _marks_by_student(grades)
    started = _started(grades)
    grades_of: dict[int, list[Grade]] = {}
    for grade in grades:
        grades_of.setdefault(grade.enrollment_id, []).append(grade)
    rows: list[dict[str, Any]] = []
    for enrollment in students:
        student_marks = marks.get(enrollment.pk, {})
        counted = engine.counted_scores(
            items, student_marks, missing_policy=gradebook.missing_policy, started=started
        )
        by_category = {
            c.id: engine.to_scale(engine.category_fraction(c, items, counted), scale) for c in categories
        }
        fraction = engine.subject_fraction(
            categories, items, student_marks, missing_policy=gradebook.missing_policy, started=started
        )
        rows.append(
            {
                "enrollment": enrollment.pk,
                "student": enrollment.student_id,
                "student_name": enrollment.student.full_name,
                "student_number": enrollment.student.student_number,
                "is_active": enrollment.status == Enrollment.Status.ACTIVE,
                "marks": [
                    {
                        "assessment": g.assessment_id,
                        "score": g.score,
                        "excused": g.excused,
                        "comment": g.comment,
                    }
                    for g in grades_of.get(enrollment.pk, [])
                ],
                "categories": [{"category": pk, "mark": mark} for pk, mark in by_category.items()],
                "mark": engine.to_scale(fraction, scale),
                "missing": sum(1 for pk in started if _is_missing(student_marks.get(pk))),
            }
        )
    active: dict[int, Decimal | None] = {row["enrollment"]: row["mark"] for row in rows if row["is_active"]}
    ranking = engine.ranks(active, scale.rank_method)
    for row in rows:
        row["rank"] = ranking.get(row["enrollment"])
        row["passed"] = None if row["mark"] is None else row["mark"] >= scale.pass_mark
    assessment_stats = []
    for item in items:
        scores = [g.score for g in grades if g.assessment_id == item.id and g.score is not None]
        assessment_stats.append(
            {
                "assessment": item.id,
                "marked": sum(
                    1 for g in grades if g.assessment_id == item.id and (g.score is not None or g.excused)
                ),
                "average": engine.round_mark(sum(scores, engine.ZERO) / len(scores), 2) if scores else None,
            }
        )
    return {
        "scale": scale_model,
        "students": rows,
        "assessments": assessment_stats,
        "stats": _stats(active.values(), scale),
    }


def _result_row(enrollment, marks, average, ranking, scale: engine.Scale) -> dict[str, Any]:
    return {
        "enrollment": enrollment.pk,
        "student": enrollment.student_id,
        "student_name": enrollment.student.full_name,
        "student_number": enrollment.student.student_number,
        "marks": [{"class_subject": cs, "mark": mark, "rank": rank} for cs, mark, rank in marks],
        "average": average,
        "rank": ranking.get(enrollment.pk),
        "passed": None if average is None else average >= scale.pass_mark,
    }


def class_results(class_group: ClassGroup, term: Term, *, published_only: bool = False) -> dict:
    """Every subject's mark for every current student of a class, the overall average and the rank.

    `published_only` (report cards) leaves out the subjects whose marks are not published yet.
    """
    scale_model = scale_for(class_group.school, class_group.level)
    scale = engine_scale(scale_model)
    enrollments = list(
        Enrollment.objects.filter(class_group=class_group, status=Enrollment.Status.ACTIVE)
        .select_related("student")
        .order_by("student__last_name", "student__first_name", "id")
    )
    ids = [e.pk for e in enrollments]
    gradebooks = {
        g.class_subject_id: g
        for g in Gradebook.objects.filter(class_subject__class_group=class_group, term=term).prefetch_related(
            Prefetch("categories", queryset=GradeCategory.objects.all()),
            Prefetch("assessments", queryset=Assessment.objects.all()),
            Prefetch("assessments__grades", queryset=Grade.objects.all()),
        )
    }
    subjects = []
    marks_by_subject: dict[int, dict[int, Decimal | None]] = {}
    for class_subject in class_group.class_subjects.select_related("subject", "teacher").order_by(
        "subject__name"
    ):
        gradebook = gradebooks.get(class_subject.pk)
        if published_only and (gradebook is None or gradebook.status != Gradebook.Status.PUBLISHED):
            continue
        if gradebook is not None and gradebook.assessments.all():
            grades = [g for a in gradebook.assessments.all() for g in a.grades.all()]
            marks = subject_marks(gradebook, scale, ids, grades)
        else:
            marks = dict.fromkeys(ids)
        marks_by_subject[class_subject.pk] = marks
        subjects.append(
            {
                "class_subject": class_subject.pk,
                "subject_name": class_subject.subject.name,
                "subject_code": class_subject.subject.code,
                "coefficient": class_subject.coefficient,
                "teacher_name": class_subject.teacher.full_name if class_subject.teacher else "",
                "gradebook": gradebook.pk if gradebook else None,
                "status": gradebook.status if gradebook else Gradebook.Status.OPEN,
                "stats": _stats(marks.values(), scale),
            }
        )
    coefficients = {s["class_subject"]: s["coefficient"] for s in subjects}
    subject_ranks = {cs: engine.ranks(marks_by_subject[cs], scale.rank_method) for cs in coefficients}
    averages = {
        pk: engine.overall_average(
            ((marks_by_subject[cs][pk], coefficients[cs]) for cs in coefficients), scale.decimals
        )
        for pk in ids
    }
    ranking = engine.ranks(averages, scale.rank_method)
    students = [
        _result_row(
            e,
            [(cs, marks_by_subject[cs][e.pk], subject_ranks[cs].get(e.pk)) for cs in coefficients],
            averages[e.pk],
            ranking,
            scale,
        )
        for e in enrollments
    ]
    return {
        "scale": scale_model,
        "subjects": subjects,
        "students": students,
        "stats": _stats(averages.values(), scale),
    }
