from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.academics.models import ClassSubject, Level, Term
from apps.audit import services as audit
from apps.enrollments.models import Enrollment

from . import engine
from .models import Assessment, Grade, Gradebook, GradeCategory, GradingScale
from .scoping import can_edit


def _user(request):
    user = getattr(request, "user", None)
    return user if user is not None and user.is_authenticated else None


# --- Grading scales ----------------------------------------------------------------------------------------


def scale_for(school, level: Level | None) -> GradingScale:
    """The scale a level reports marks on: its own, else the school's, else out of 20 (pass mark 10)."""
    scales = {
        s.level_id: s
        for s in GradingScale.objects.filter(Q(level=level) | Q(level__isnull=True), school=school)
    }
    if level is not None and level.pk in scales:
        return scales[level.pk]
    return scales.get(None) or GradingScale(school=school)


def engine_scale(scale: GradingScale) -> engine.Scale:
    return engine.Scale(
        max_mark=scale.max_mark,
        pass_mark=scale.pass_mark,
        decimals=scale.decimals,
        rank_method=scale.rank_method,
    )


# --- Gradebooks --------------------------------------------------------------------------------------------


def ensure_gradebooks(school, term: Term) -> None:
    """Every subject taught in the term's year gets its gradebook the first time the list is read."""
    subjects = ClassSubject.objects.filter(school=school, class_group__academic_year=term.academic_year_id)
    existing = set(Gradebook.objects.filter(term=term).values_list("class_subject_id", flat=True))
    Gradebook.objects.bulk_create(
        [
            Gradebook(school=school, class_subject_id=pk, term=term)
            for pk in subjects.values_list("pk", flat=True)
            if pk not in existing
        ],
        ignore_conflicts=True,
    )


def roster(gradebook: Gradebook) -> list[Enrollment]:
    """The students of the gradebook: the class's current students, plus anyone who left but has marks."""
    return list(
        Enrollment.objects.filter(
            Q(status=Enrollment.Status.ACTIVE) | Q(grades__assessment__gradebook=gradebook),
            class_group_id=gradebook.class_subject.class_group_id,
        )
        .select_related("student")
        .distinct()
        .order_by("student__last_name", "student__first_name", "id")
    )


def require_editable(request, gradebook: Gradebook) -> None:
    """Marks and rules change only while the gradebook is in progress, and only by its teacher."""
    if not can_edit(request, gradebook):
        raise PermissionDenied(_("Only the teacher of this subject can change its marks."))
    if gradebook.status != Gradebook.Status.OPEN:
        raise ValidationError(
            {"status": [_("These marks were submitted or published. Ask for them to be reopened first.")]}
        )


def _locked(gradebook: Gradebook) -> Gradebook:
    return (
        Gradebook.objects.select_for_update()
        .select_related("class_subject__class_group", "class_subject__subject", "term")
        .get(pk=gradebook.pk)
    )


def _describe(gradebook: Gradebook) -> str:
    cs = gradebook.class_subject
    return f"{cs.subject.name} — {cs.class_group.name}, {gradebook.term.name}"


@transaction.atomic
def update_gradebook(gradebook: Gradebook, *, missing_policy: str, request=None) -> Gradebook:
    gradebook = _locked(gradebook)
    require_editable(request, gradebook)
    if gradebook.missing_policy != missing_policy:
        old = gradebook.missing_policy
        gradebook.missing_policy = missing_policy
        gradebook.save(update_fields=["missing_policy", "updated_at"])
        audit.record(
            "update",
            request=request,
            instance=gradebook,
            module="grades",
            summary=f"Missing marks rule changed: {_describe(gradebook)}",
            old={"missing_policy": old},
            new={"missing_policy": missing_policy},
        )
    return gradebook


@transaction.atomic
def copy_setup(target: Gradebook, source: Gradebook, *, with_assessments: bool, request=None) -> Gradebook:
    """Reuse the categories (and, if asked, the assessments without marks) of another gradebook."""
    target = _locked(target)
    require_editable(request, target)
    if source.pk == target.pk:
        raise ValidationError({"source": [_("Choose another gradebook to copy from.")]})
    if target.assessments.exists():
        raise ValidationError(
            {"source": [_("This gradebook already has assessments. Copy the rules into an empty one.")]}
        )
    target.categories.all().delete()
    for category in source.categories.all():
        copy = GradeCategory.objects.create(
            school=target.school,
            gradebook=target,
            name=category.name,
            weight=category.weight,
            method=category.method,
            order=category.order,
            created_by=_user(request),
        )
        if with_assessments:
            Assessment.objects.bulk_create(
                [
                    Assessment(
                        school=target.school,
                        gradebook=target,
                        category=copy,
                        name=item.name,
                        max_score=item.max_score,
                        weight=item.weight,
                        created_by=_user(request),
                    )
                    for item in category.assessments.all()
                ]
            )
    target.missing_policy = source.missing_policy
    target.save(update_fields=["missing_policy", "updated_at"])
    audit.record(
        "update",
        request=request,
        instance=target,
        module="grades",
        summary=f"Grading rules copied into {_describe(target)} from {_describe(source)}",
        new={"source": source.pk, "with_assessments": with_assessments},
    )
    return target


# --- Categories and assessments ----------------------------------------------------------------------------


def check_max_score(assessment: Assessment, max_score: Decimal) -> None:
    """An assessment cannot be marked out of less than a score it already has."""
    highest = assessment.grades.filter(score__isnull=False).order_by("-score").values_list("score", flat=True)
    top = highest.first()
    if top is not None and top > max_score:
        raise ValidationError(
            {"max_score": [_("A student already has %(score)s on this assessment.") % {"score": top}]}
        )


# --- Marks -------------------------------------------------------------------------------------------------


def _parse_entries(gradebook: Gradebook, entries: list[dict]) -> list[dict]:
    assessments = {a.pk: a for a in gradebook.assessments.all()}
    students = {e.pk: e for e in roster(gradebook)}
    errors: dict[str, list[str]] = {}
    seen = set()
    for index, entry in enumerate(entries):
        assessment = assessments.get(entry["assessment"])
        enrollment = students.get(entry["enrollment"])
        key = (entry["assessment"], entry["enrollment"])
        if assessment is None or enrollment is None:
            errors[str(index)] = [_("This mark is not for an assessment and a student of this gradebook.")]
            continue
        if key in seen:
            errors[str(index)] = [_("This mark appears twice.")]
            continue
        seen.add(key)
        score = entry.get("score")
        if entry.get("excused"):
            score = None
        elif score is not None and score > assessment.max_score:
            errors[str(index)] = [
                _("%(name)s is marked out of %(max)s.")
                % {"name": assessment.name, "max": assessment.max_score}
            ]
            continue
        entry.update(assessment_obj=assessment, enrollment_obj=enrollment, score=score)
    if errors:
        raise ValidationError({"grades": errors})
    return entries


def _value(grade: Grade | None) -> str | None:
    if grade is None:
        return None
    if grade.excused:
        return "excused"
    return None if grade.score is None else str(grade.score)


@transaction.atomic
def save_grades(gradebook: Gradebook, entries: list[dict], *, request=None) -> dict:
    """Record a batch of marks. A mark with no score, not excused and no comment is cleared.

    Returns how many marks changed. Every change is written to the audit log (old → new).
    """
    gradebook = _locked(gradebook)
    require_editable(request, gradebook)
    entries = _parse_entries(gradebook, entries)
    existing = {
        (g.assessment_id, g.enrollment_id): g
        for g in Grade.objects.filter(assessment__gradebook=gradebook).select_for_update()
    }
    user = _user(request)
    old_values: dict[str, str | None] = {}
    new_values: dict[str, str | None] = {}
    for entry in entries:
        assessment, enrollment = entry["assessment_obj"], entry["enrollment_obj"]
        score, excused = entry["score"], bool(entry.get("excused"))
        comment = (entry.get("comment") or "").strip()
        grade = existing.get((assessment.pk, enrollment.pk))
        before = _value(grade)
        key = f"{assessment.pk}:{enrollment.pk}"
        if score is None and not excused and not comment:
            if grade is not None:
                grade.delete()
                old_values[key], new_values[key] = before, None
            continue
        if grade is None:
            grade = Grade(
                school=gradebook.school, assessment=assessment, enrollment=enrollment, created_by=user
            )
        elif grade.score == score and grade.excused == excused and grade.comment == comment:
            continue
        grade.score, grade.excused, grade.comment, grade.updated_by = score, excused, comment, user
        grade.save()
        if before != _value(grade):
            old_values[key], new_values[key] = before, _value(grade)
    if new_values:
        audit.record(
            "update",
            request=request,
            instance=gradebook,
            module="grades",
            summary=f"{len(new_values)} mark(s) entered: {_describe(gradebook)}",
            old=old_values,
            new=new_values,
        )
    return {"changed": len(new_values)}


# --- Workflow ----------------------------------------------------------------------------------------------


def _transition(
    gradebook: Gradebook, *, to: str, action: str, request, note: str = "", **fields
) -> Gradebook:
    old = gradebook.status
    gradebook.status = to
    gradebook.status_note = note
    for name, value in fields.items():
        setattr(gradebook, name, value)
    gradebook.save()
    audit.record(
        action,
        request=request,
        instance=gradebook,
        module="grades",
        summary=f"Marks {action}: {_describe(gradebook)}" + (f" — {note}" if note else ""),
        old={"status": old},
        new={"status": to, **({"reason": note} if note else {})},
    )
    return gradebook


def _require_assessments(gradebook: Gradebook) -> None:
    if not gradebook.assessments.exists():
        raise ValidationError({"status": [_("Add at least one assessment first.")]})


@transaction.atomic
def submit(gradebook: Gradebook, *, request=None) -> Gradebook:
    """The teacher hands the marks in for review; they can no longer change them."""
    gradebook = _locked(gradebook)
    require_editable(request, gradebook)
    _require_assessments(gradebook)
    return _transition(
        gradebook,
        to=Gradebook.Status.SUBMITTED,
        action="submit",
        request=request,
        submitted_at=timezone.now(),
        submitted_by=_user(request),
    )


@transaction.atomic
def send_back(gradebook: Gradebook, *, reason: str, request=None) -> Gradebook:
    """The reviewer returns submitted marks to the teacher, saying what to fix."""
    gradebook = _locked(gradebook)
    if gradebook.status != Gradebook.Status.SUBMITTED:
        raise ValidationError({"status": [_("Only submitted marks can be sent back.")]})
    return _transition(
        gradebook, to=Gradebook.Status.OPEN, action="return", request=request, note=reason.strip()
    )


@transaction.atomic
def publish(gradebook: Gradebook, *, request=None) -> Gradebook:
    """The school makes the marks official: they count in the class results and are locked."""
    gradebook = _locked(gradebook)
    if gradebook.status == Gradebook.Status.PUBLISHED:
        raise ValidationError({"status": [_("These marks are already published.")]})
    _require_assessments(gradebook)
    return _transition(
        gradebook,
        to=Gradebook.Status.PUBLISHED,
        action="publish",
        request=request,
        published_at=timezone.now(),
        published_by=_user(request),
    )


@transaction.atomic
def reopen(gradebook: Gradebook, *, reason: str, request=None) -> Gradebook:
    """Unlock published marks so the teacher can correct them. The reason is kept in the audit log."""
    gradebook = _locked(gradebook)
    if gradebook.status != Gradebook.Status.PUBLISHED:
        raise ValidationError({"status": [_("Only published marks can be reopened.")]})
    return _transition(
        gradebook,
        to=Gradebook.Status.OPEN,
        action="reopen",
        request=request,
        note=reason.strip(),
        published_at=None,
        published_by=None,
    )
