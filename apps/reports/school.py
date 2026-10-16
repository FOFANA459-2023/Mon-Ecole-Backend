"""School reports (ToR §20): enrolment, classes, teachers, attendance and student statistics.

Each builder returns a `Report` for the shared renderers (screen, PDF, Excel, CSV). Figures cover one school
year, optionally one level or class; the attendance report covers a period (the year so far by default).
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from django.db.models import Count, Q
from django.utils import timezone

from apps.academics.models import AcademicYear, ClassGroup, ClassSubject, Level
from apps.enrollments.models import Enrollment
from apps.people.models import StaffMember

from .base import Column, Report, Section, period_label

LABELS: dict[str, dict[str, Any]] = {
    "fr": {
        "enrollment": "Rapport des inscriptions",
        "classes": "Rapport des classes",
        "teachers": "Rapport du personnel enseignant",
        "attendance": "Rapport des présences",
        "statistics": "Statistiques des élèves",
        "whole_school": "Tout l'établissement",
        "by_class": "Par classe",
        "students": "Élèves",
        "student": "Élève",
        "student_number": "Matricule",
        "class": "Classe",
        "level": "Niveau",
        "gender": "Sexe",
        "born": "Date de naissance",
        "date": "Date d'inscription",
        "kind": "Type",
        "status": "Statut",
        "new": "Nouveaux",
        "re_enrolment": "Réinscrits",
        "transfer_in": "Transferts",
        "boys": "Garçons",
        "girls": "Filles",
        "total": "Total",
        "kinds": {"new": "Nouvelle inscription", "re_enrolment": "Réinscription", "transfer_in": "Transfert"},
        "statuses": {
            "active": "Inscrit",
            "class_changed": "Changé de classe",
            "withdrawn": "Parti",
            "completed": "Année terminée",
            "cancelled": "Annulée",
        },
        "genders": {"M": "M", "F": "F", "": "—"},
        "list": "Liste des inscriptions",
        "class_teacher": "Professeur principal",
        "room": "Salle",
        "capacity": "Capacité",
        "fill": "Remplissage",
        "subjects": "Matières",
        "subjects_teachers": "Matières et enseignants",
        "subject": "Matière",
        "teacher": "Enseignant",
        "coefficient": "Coef.",
        "none": "—",
        "staff": "Personnel",
        "employee_number": "Matricule",
        "name": "Nom",
        "type": "Catégorie",
        "position": "Fonction",
        "department": "Département",
        "phone": "Téléphone",
        "email": "E-mail",
        "classes_taught": "Classes",
        "subjects_taught": "Matières enseignées",
        "homeroom": "Classe principale",
        "access": "Accès",
        "yes": "Oui",
        "no": "Non",
        "workload": "Charge d'enseignement",
        "class_subjects": "Cours (classe × matière)",
        "coefficients": "Total des coefficients",
        "staff_types": {
            "teacher": "Enseignant",
            "administrative": "Administratif",
            "support": "Personnel d'appui",
        },
        "registers": "Appels faits",
        "present": "Présents",
        "absent": "Absents",
        "late": "Retards",
        "excused": "Excusés",
        "rate": "Taux de présence",
        "most_absent": "Élèves les plus absents",
        "staff_attendance": "Présence du personnel",
        "leave": "Congés",
        "days": "Jours",
        "by_level": "Par niveau",
        "by_age": "Par âge",
        "age": "Âge",
        "years": "{n} ans",
        "unknown": "Non renseigné",
        "by_nationality": "Par nationalité",
        "nationality": "Nationalité",
        "share": "Part",
        "average_age": "Âge moyen",
        "by_kind": "Par type d'inscription",
        "attendance_note": "Taux de présence = (présents + retards) / élèves marqués.",
    },
    "en": {
        "enrollment": "Enrolment report",
        "classes": "Class report",
        "teachers": "Teacher report",
        "attendance": "Attendance report",
        "statistics": "Student statistics",
        "whole_school": "Whole school",
        "by_class": "By class",
        "students": "Students",
        "student": "Student",
        "student_number": "Student no.",
        "class": "Class",
        "level": "Level",
        "gender": "Gender",
        "born": "Date of birth",
        "date": "Enrolment date",
        "kind": "Type",
        "status": "Status",
        "new": "New",
        "re_enrolment": "Re-enrolled",
        "transfer_in": "Transfers",
        "boys": "Boys",
        "girls": "Girls",
        "total": "Total",
        "kinds": {"new": "New enrolment", "re_enrolment": "Re-enrolment", "transfer_in": "Transfer"},
        "statuses": {
            "active": "Enrolled",
            "class_changed": "Changed class",
            "withdrawn": "Left",
            "completed": "Year completed",
            "cancelled": "Cancelled",
        },
        "genders": {"M": "M", "F": "F", "": "—"},
        "list": "Enrolments",
        "class_teacher": "Class teacher",
        "room": "Room",
        "capacity": "Capacity",
        "fill": "Filled",
        "subjects": "Subjects",
        "subjects_teachers": "Subjects and teachers",
        "subject": "Subject",
        "teacher": "Teacher",
        "coefficient": "Coef.",
        "none": "—",
        "staff": "Staff",
        "employee_number": "Staff no.",
        "name": "Name",
        "type": "Category",
        "position": "Position",
        "department": "Department",
        "phone": "Phone",
        "email": "Email",
        "classes_taught": "Classes",
        "subjects_taught": "Subjects taught",
        "homeroom": "Class teacher of",
        "access": "Login",
        "yes": "Yes",
        "no": "No",
        "workload": "Teaching load",
        "class_subjects": "Courses (class × subject)",
        "coefficients": "Total coefficients",
        "staff_types": {"teacher": "Teacher", "administrative": "Administrative", "support": "Support staff"},
        "registers": "Registers taken",
        "present": "Present",
        "absent": "Absent",
        "late": "Late",
        "excused": "Excused",
        "rate": "Attendance rate",
        "most_absent": "Students absent most",
        "staff_attendance": "Staff attendance",
        "leave": "Leave",
        "days": "Days",
        "by_level": "By level",
        "by_age": "By age",
        "age": "Age",
        "years": "{n} years",
        "unknown": "Not recorded",
        "by_nationality": "By nationality",
        "nationality": "Nationality",
        "share": "Share",
        "average_age": "Average age",
        "by_kind": "By type of enrolment",
        "attendance_note": "Attendance rate = (present + late) / students marked.",
    },
}


@dataclass
class Params:
    academic_year: AcademicYear
    level: Level | None = None
    class_group: ClassGroup | None = None
    date_from: date | None = None
    date_to: date | None = None


def labels(language: str) -> dict[str, Any]:
    return LABELS["en" if language == "en" else "fr"]


def _classes(school, p: Params):
    queryset = ClassGroup.objects.filter(school=school, academic_year=p.academic_year)
    if p.class_group is not None:
        queryset = queryset.filter(pk=p.class_group.pk)
    elif p.level is not None:
        queryset = queryset.filter(level=p.level)
    return queryset


def _scope(L: dict, p: Params) -> str:
    if p.class_group is not None:
        return p.class_group.name
    if p.level is not None:
        return p.level.name
    return L["whole_school"]


def _subtitle(L: dict, p: Params, language: str, with_period: bool = False) -> str:
    parts = [p.academic_year.name, _scope(L, p)]
    if with_period and p.date_from and p.date_to:
        parts.insert(0, period_label(p.date_from, p.date_to, language))
    return " · ".join(parts)


def _percent(part: int, whole: int) -> Decimal | None:
    return (Decimal(100 * part) / whole).quantize(Decimal("0.1")) if whole else None


def _age(born: date | None, on: date) -> int | None:
    if born is None:
        return None
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


# --- Enrolment ----------------------------------------------------------------------------------------------


def enrollment_report(school, p: Params, language: str) -> Report:
    L = labels(language)
    enrollments = (
        Enrollment.objects.filter(school=school, class_group__in=_classes(school, p))
        .select_related("student", "class_group__level")
        .order_by(
            "class_group__level__order", "class_group__name", "student__last_name", "student__first_name"
        )
    )
    if p.date_from:
        enrollments = enrollments.filter(enrollment_date__gte=p.date_from)
    if p.date_to:
        enrollments = enrollments.filter(enrollment_date__lte=p.date_to)
    rows = list(enrollments)
    summary: dict[str, Counter] = defaultdict(Counter)
    for e in rows:
        if e.status == Enrollment.Status.CANCELLED:
            continue
        counter = summary[e.class_group.name]
        counter[e.kind] += 1
        counter["total"] += 1
        counter[e.student.gender or "?"] += 1
    by_class: list[dict[str, Any]] = [
        {
            "class": name,
            "new": c["new"],
            "re_enrolment": c["re_enrolment"],
            "transfer_in": c["transfer_in"],
            "boys": c["M"],
            "girls": c["F"],
            "total": c["total"],
        }
        for name, c in summary.items()
    ]
    totals_keys = ["new", "re_enrolment", "transfer_in", "boys", "girls", "total"]
    totals = {"class": L["total"], **{k: sum(int(r[k]) for r in by_class) for k in totals_keys}}
    return Report(
        key="enrollment",
        title=L["enrollment"],
        subtitle=_subtitle(L, p, language, with_period=True),
        sections=[
            Section(
                L["by_class"],
                [
                    Column("class", L["class"]),
                    Column("new", L["new"], "number"),
                    Column("re_enrolment", L["re_enrolment"], "number"),
                    Column("transfer_in", L["transfer_in"], "number"),
                    Column("boys", L["boys"], "number"),
                    Column("girls", L["girls"], "number"),
                    Column("total", L["total"], "number"),
                ],
                by_class,
                totals=totals,
            ),
            Section(
                L["list"],
                [
                    Column("number", L["student_number"]),
                    Column("student", L["student"]),
                    Column("gender", L["gender"]),
                    Column("born", L["born"], "date"),
                    Column("class", L["class"]),
                    Column("kind", L["kind"]),
                    Column("date", L["date"], "date"),
                    Column("status", L["status"]),
                ],
                [
                    {
                        "number": e.student.student_number,
                        "student": e.student.full_name,
                        "gender": L["genders"].get(e.student.gender, ""),
                        "born": e.student.date_of_birth,
                        "class": e.class_group.name,
                        "kind": L["kinds"].get(e.kind, e.kind),
                        "date": e.enrollment_date,
                        "status": L["statuses"].get(e.status, e.status),
                    }
                    for e in rows
                ],
            ),
        ],
    )


# --- Classes ------------------------------------------------------------------------------------------------


def classes_report(school, p: Params, language: str) -> Report:
    L = labels(language)
    active = Q(enrollments__status=Enrollment.Status.ACTIVE)
    classes = (
        _classes(school, p)
        .select_related("level", "class_teacher")
        .annotate(
            students=Count("enrollments", filter=active, distinct=True),
            boys=Count("enrollments", filter=active & Q(enrollments__student__gender="M"), distinct=True),
            girls=Count("enrollments", filter=active & Q(enrollments__student__gender="F"), distinct=True),
            subjects=Count("class_subjects", distinct=True),
        )
        .order_by("level__order", "name")
    )
    rows = [
        {
            "class": c.name,
            "level": c.level.name,
            "class_teacher": c.class_teacher.full_name if c.class_teacher else L["none"],
            "room": c.room,
            "capacity": c.capacity,
            "students": c.students,
            "boys": c.boys,
            "girls": c.girls,
            "fill": _percent(c.students, c.capacity) if c.capacity else None,
            "subjects": c.subjects,
        }
        for c in classes
    ]
    totals = {
        "class": L["total"],
        **{k: sum(r[k] or 0 for r in rows) for k in ("capacity", "students", "boys", "girls")},
    }
    courses = (
        ClassSubject.objects.filter(class_group__in=classes.values("pk"))
        .select_related("class_group", "subject", "teacher")
        .order_by("class_group__level__order", "class_group__name", "subject__name")
    )
    return Report(
        key="classes",
        title=L["classes"],
        subtitle=_subtitle(L, p, language),
        sections=[
            Section(
                L["by_class"],
                [
                    Column("class", L["class"]),
                    Column("level", L["level"]),
                    Column("class_teacher", L["class_teacher"]),
                    Column("room", L["room"]),
                    Column("capacity", L["capacity"], "number"),
                    Column("students", L["students"], "number"),
                    Column("boys", L["boys"], "number"),
                    Column("girls", L["girls"], "number"),
                    Column("fill", L["fill"], "percent"),
                    Column("subjects", L["subjects"], "number"),
                ],
                rows,
                totals=totals,
            ),
            Section(
                L["subjects_teachers"],
                [
                    Column("class", L["class"]),
                    Column("subject", L["subject"]),
                    Column("teacher", L["teacher"]),
                    Column("coefficient", L["coefficient"], "number"),
                ],
                [
                    {
                        "class": cs.class_group.name,
                        "subject": cs.subject.name,
                        "teacher": cs.teacher.full_name if cs.teacher else L["none"],
                        "coefficient": cs.coefficient,
                    }
                    for cs in courses
                ],
            ),
        ],
    )


# --- Teachers -----------------------------------------------------------------------------------------------


def teachers_report(school, p: Params, language: str) -> Report:
    L = labels(language)
    classes = _classes(school, p)
    courses = ClassSubject.objects.filter(class_group__in=classes).select_related("class_group", "subject")
    taught: dict[int, list] = defaultdict(list)
    for cs in courses:
        if cs.teacher_id:
            taught[cs.teacher_id].append(cs)
    homeroom: dict[int, list[str]] = defaultdict(list)
    for c in classes.exclude(class_teacher=None):
        homeroom[c.class_teacher_id].append(c.name)
    staff = StaffMember.objects.filter(school=school, status=StaffMember.Status.ACTIVE).select_related(
        "department"
    )
    if p.class_group is not None or p.level is not None:
        staff = staff.filter(pk__in=set(taught) | set(homeroom))
    staff = staff.order_by("staff_type", "last_name", "first_name")
    rows = []
    workload = []
    for s in staff:
        own = taught.get(s.pk, [])
        rows.append(
            {
                "number": s.employee_number,
                "name": s.full_name,
                "type": L["staff_types"].get(s.staff_type, s.staff_type),
                "position": s.position,
                "department": s.department.name if s.department else "",
                "phone": s.phone,
                "email": s.email,
                "homeroom": ", ".join(homeroom.get(s.pk, [])),
                "access": L["yes"] if s.user_id else L["no"],
            }
        )
        if own or s.staff_type == StaffMember.StaffType.TEACHER:
            workload.append(
                {
                    "name": s.full_name,
                    "classes": len({cs.class_group_id for cs in own}),
                    "courses": len(own),
                    "coefficients": sum((cs.coefficient for cs in own), Decimal(0)),
                    "subjects": ", ".join(sorted({cs.subject.name for cs in own})),
                }
            )
    return Report(
        key="teachers",
        title=L["teachers"],
        subtitle=_subtitle(L, p, language),
        sections=[
            Section(
                L["staff"],
                [
                    Column("number", L["employee_number"]),
                    Column("name", L["name"]),
                    Column("type", L["type"]),
                    Column("position", L["position"]),
                    Column("department", L["department"]),
                    Column("phone", L["phone"]),
                    Column("email", L["email"]),
                    Column("homeroom", L["homeroom"]),
                    Column("access", L["access"]),
                ],
                rows,
            ),
            Section(
                L["workload"],
                [
                    Column("name", L["teacher"]),
                    Column("classes", L["classes_taught"], "number"),
                    Column("courses", L["class_subjects"], "number"),
                    Column("coefficients", L["coefficients"], "number"),
                    Column("subjects", L["subjects_taught"]),
                ],
                sorted(workload, key=lambda r: (-r["courses"], r["name"])),
            ),
        ],
    )


# --- Attendance ---------------------------------------------------------------------------------------------


def attendance_report(school, p: Params, language: str) -> Report:
    from apps.attendance.models import AttendanceRecord, ClassRegister, StaffAttendance, Status

    L = labels(language)
    start = p.date_from or p.academic_year.start_date
    end = p.date_to or min(p.academic_year.end_date, timezone.localdate())
    classes = _classes(school, p)
    records = AttendanceRecord.objects.filter(
        register__class_group__in=classes, register__date__range=(start, end)
    )
    counts = {
        row["register__class_group"]: row
        for row in records.values("register__class_group").annotate(
            present=Count("pk", filter=Q(status=Status.PRESENT)),
            absent=Count("pk", filter=Q(status=Status.ABSENT)),
            late=Count("pk", filter=Q(status=Status.LATE)),
            excused=Count("pk", filter=Q(status=Status.EXCUSED)),
        )
    }
    registers = dict(
        ClassRegister.objects.filter(class_group__in=classes, date__range=(start, end))
        .values_list("class_group")
        .annotate(n=Count("pk"))
        .values_list("class_group", "n")
    )
    by_class = []
    for c in classes.order_by("level__order", "name"):
        row = counts.get(c.pk, {})
        tally = {k: row.get(k, 0) for k in ("present", "absent", "late", "excused")}
        marked = sum(tally.values())
        by_class.append(
            {
                "class": c.name,
                "registers": registers.get(c.pk, 0),
                **tally,
                "rate": _percent(tally["present"] + tally["late"], marked),
            }
        )
    sums = {k: sum(r[k] for r in by_class) for k in ("registers", "present", "absent", "late", "excused")}
    totals = {
        "class": L["total"],
        **sums,
        "rate": _percent(
            sums["present"] + sums["late"], sums["present"] + sums["absent"] + sums["late"] + sums["excused"]
        ),
    }
    most = (
        records.filter(status__in=[Status.ABSENT, Status.EXCUSED, Status.LATE])
        .values(
            "enrollment__student__student_number",
            "enrollment__student__first_name",
            "enrollment__student__last_name",
            "register__class_group__name",
        )
        .annotate(
            absent=Count("pk", filter=Q(status=Status.ABSENT)),
            excused=Count("pk", filter=Q(status=Status.EXCUSED)),
            late=Count("pk", filter=Q(status=Status.LATE)),
        )
        .order_by("-absent", "-late", "enrollment__student__last_name")[:50]
    )
    staff = (
        StaffAttendance.objects.filter(school=school, date__range=(start, end))
        .values("staff__employee_number", "staff__first_name", "staff__last_name")
        .annotate(
            days=Count("pk"),
            absent=Count("pk", filter=Q(status="absent")),
            late=Count("pk", filter=Q(status="late")),
            excused=Count("pk", filter=Q(status="excused")),
            leave=Count("pk", filter=Q(status="leave")),
        )
        .order_by("-absent", "-late", "staff__last_name")
    )
    p.date_from, p.date_to = start, end
    return Report(
        key="attendance",
        title=L["attendance"],
        subtitle=_subtitle(L, p, language, with_period=True),
        sections=[
            Section(
                L["by_class"],
                [
                    Column("class", L["class"]),
                    Column("registers", L["registers"], "number"),
                    Column("present", L["present"], "number"),
                    Column("absent", L["absent"], "number"),
                    Column("late", L["late"], "number"),
                    Column("excused", L["excused"], "number"),
                    Column("rate", L["rate"], "percent"),
                ],
                by_class,
                totals=totals,
                note=L["attendance_note"],
            ),
            Section(
                L["most_absent"],
                [
                    Column("number", L["student_number"]),
                    Column("student", L["student"]),
                    Column("class", L["class"]),
                    Column("absent", L["absent"], "number"),
                    Column("excused", L["excused"], "number"),
                    Column("late", L["late"], "number"),
                ],
                [
                    {
                        "number": r["enrollment__student__student_number"],
                        "student": " ".join(
                            (r["enrollment__student__first_name"], r["enrollment__student__last_name"])
                        ),
                        "class": r["register__class_group__name"],
                        "absent": r["absent"],
                        "excused": r["excused"],
                        "late": r["late"],
                    }
                    for r in most
                ],
            ),
            Section(
                L["staff_attendance"],
                [
                    Column("number", L["employee_number"]),
                    Column("name", L["name"]),
                    Column("days", L["days"], "number"),
                    Column("absent", L["absent"], "number"),
                    Column("late", L["late"], "number"),
                    Column("excused", L["excused"], "number"),
                    Column("leave", L["leave"], "number"),
                ],
                [
                    {
                        "number": r["staff__employee_number"],
                        "name": f"{r['staff__first_name']} {r['staff__last_name']}",
                        **{k: r[k] for k in ("days", "absent", "late", "excused", "leave")},
                    }
                    for r in staff
                ],
            ),
        ],
    )


# --- Student statistics -------------------------------------------------------------------------------------


def statistics_report(school, p: Params, language: str) -> Report:
    L = labels(language)
    enrollments = list(
        Enrollment.objects.filter(
            school=school, class_group__in=_classes(school, p), status=Enrollment.Status.ACTIVE
        ).select_related("student", "class_group__level")
    )
    total = len(enrollments)
    on = min(timezone.localdate(), p.academic_year.end_date)
    levels: dict[str, dict[str, Any]] = {}
    ages: Counter = Counter()
    nations: Counter = Counter()
    kinds: Counter = Counter()
    for e in sorted(enrollments, key=lambda e: e.class_group.level.order):
        level = levels.setdefault(
            e.class_group.level.name, {"students": 0, "boys": 0, "girls": 0, "ages": []}
        )
        level["students"] += 1
        level["boys"] += e.student.gender == "M"
        level["girls"] += e.student.gender == "F"
        age = _age(e.student.date_of_birth, on)
        if age is not None:
            level["ages"].append(age)
        ages[age] += 1
        nations[(e.student.nationality or "").strip() or L["unknown"]] += 1
        kinds[e.kind] += 1

    def mean(values: list[int]) -> Decimal | None:
        return (Decimal(sum(values)) / len(values)).quantize(Decimal("0.1")) if values else None

    level_rows = [
        {
            "level": name,
            "students": v["students"],
            "boys": v["boys"],
            "girls": v["girls"],
            "share": _percent(v["students"], total),
            "average_age": mean(v["ages"]),
        }
        for name, v in levels.items()
    ]
    all_ages = [age for v in levels.values() for age in v["ages"]]
    totals = {
        "level": L["total"],
        "students": total,
        "boys": sum(r["boys"] for r in level_rows),
        "girls": sum(r["girls"] for r in level_rows),
        "average_age": mean(all_ages),
    }
    age_rows = [
        {
            "age": L["years"].format(n=age) if age is not None else L["unknown"],
            "students": n,
            "share": _percent(n, total),
        }
        for age, n in sorted(ages.items(), key=lambda item: (item[0] is None, item[0] or 0))
    ]
    nation_rows = [
        {"nationality": name, "students": n, "share": _percent(n, total)} for name, n in nations.most_common()
    ]
    kind_rows = [
        {"kind": L["kinds"].get(kind, kind), "students": n, "share": _percent(n, total)}
        for kind, n in kinds.most_common()
    ]
    count_columns = [Column("students", L["students"], "number"), Column("share", L["share"], "percent")]
    return Report(
        key="statistics",
        title=L["statistics"],
        subtitle=_subtitle(L, p, language),
        landscape=False,
        sections=[
            Section(
                L["by_level"],
                [
                    Column("level", L["level"]),
                    Column("students", L["students"], "number"),
                    Column("boys", L["boys"], "number"),
                    Column("girls", L["girls"], "number"),
                    Column("share", L["share"], "percent"),
                    Column("average_age", L["average_age"], "number"),
                ],
                level_rows,
                totals=totals,
            ),
            Section(L["by_age"], [Column("age", L["age"]), *count_columns], age_rows),
            Section(
                L["by_nationality"], [Column("nationality", L["nationality"]), *count_columns], nation_rows
            ),
            Section(L["by_kind"], [Column("kind", L["kind"]), *count_columns], kind_rows),
        ],
    )


BUILDERS = {
    "enrollment": enrollment_report,
    "classes": classes_report,
    "teachers": teachers_report,
    "attendance": attendance_report,
    "statistics": statistics_report,
}
