"""Academic reports (ToR §20): the grade report of a class, the ranking report and the subject results.

Only published marks count, as on report cards; each report says which subjects are not published yet. A
term report uses the term's marks; without a term, the year: each subject's mark is the average of its
term marks and the average is the average of the term averages (the annual report card's rules).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from apps.academics.models import AcademicYear, ClassGroup, Level, Term
from apps.assessments import engine
from apps.assessments.models import Gradebook
from apps.assessments.reportcards import annual_cards
from apps.assessments.selectors import class_results
from apps.assessments.services import engine_scale

from .base import Column, Report, Section

LABELS: dict[str, dict[str, Any]] = {
    "fr": {
        "grades": "Relevé de notes",
        "ranking": "Classement",
        "subjects": "Résultats par matière",
        "year": "Année",
        "term": "Période",
        "rank": "Rang",
        "student": "Élève",
        "student_number": "Matricule",
        "class": "Classe",
        "level": "Niveau",
        "average": "Moyenne /{max}",
        "mention": "Mention",
        "decision": "Décision",
        "passed": "Admis(e)",
        "failed": "Non admis(e)",
        "students": "Élèves",
        "ranked": "Classés",
        "class_average": "Moy. classe",
        "highest": "Plus forte",
        "lowest": "Plus faible",
        "pass_count": "Moyenne atteinte",
        "pass_rate": "Taux de réussite",
        "subject": "Matière",
        "code": "Code",
        "coefficient": "Coef.",
        "teacher": "Professeur",
        "marks": "Notes des élèves",
        "subject_stats": "Statistiques par matière",
        "classes": "Classes",
        "level_ranking": "Classement du niveau {level}",
        "class_ranking": "Classement — {name}",
        "status": "Statut",
        "assessments": "Évaluations",
        "not_published": "Matières non publiées",
        "statuses": {"open": "En cours de saisie", "submitted": "Soumise", "published": "Publiée"},
        "published_only": "Seules les notes publiées comptent.",
        "missing": "Pas encore publiées : {subjects}.",
        "pass_note": "Moyenne de passage : {pass_mark}/{max}.",
        "whole_school": "Tout l'établissement",
        "subjects_note": "Une ligne par matière et par classe, notes publiées seulement.",
        "unpublished_note": "Ces matières ne comptent pas encore dans les moyennes ni sur les bulletins.",
    },
    "en": {
        "grades": "Grade report",
        "ranking": "Ranking report",
        "subjects": "Results by subject",
        "year": "Year",
        "term": "Term",
        "rank": "Rank",
        "student": "Student",
        "student_number": "Student no.",
        "class": "Class",
        "level": "Level",
        "average": "Average /{max}",
        "mention": "Honours",
        "decision": "Decision",
        "passed": "Passed",
        "failed": "Failed",
        "students": "Students",
        "ranked": "Ranked",
        "class_average": "Class avg.",
        "highest": "Highest",
        "lowest": "Lowest",
        "pass_count": "Pass mark reached",
        "pass_rate": "Pass rate",
        "subject": "Subject",
        "code": "Code",
        "coefficient": "Coef.",
        "teacher": "Teacher",
        "marks": "Students' marks",
        "subject_stats": "Statistics by subject",
        "classes": "Classes",
        "level_ranking": "Ranking of the level {level}",
        "class_ranking": "Ranking — {name}",
        "status": "Status",
        "assessments": "Assessments",
        "not_published": "Subjects not published",
        "statuses": {"open": "Being marked", "submitted": "Submitted", "published": "Published"},
        "published_only": "Only published marks count.",
        "missing": "Not published yet: {subjects}.",
        "pass_note": "Pass mark: {pass_mark}/{max}.",
        "whole_school": "Whole school",
        "subjects_note": "One row per subject and class, published marks only.",
        "unpublished_note": "These subjects do not count in averages or on report cards yet.",
    },
}


@dataclass
class Params:
    academic_year: AcademicYear
    term: Term | None = None
    level: Level | None = None
    class_group: ClassGroup | None = None


def labels(language: str) -> dict[str, Any]:
    return LABELS["en" if language == "en" else "fr"]


def classes_for(school, p: Params) -> list[ClassGroup]:
    if p.class_group is not None:
        return [p.class_group]
    queryset = ClassGroup.objects.filter(
        school=school, academic_year=p.academic_year, status=ClassGroup.Status.ACTIVE
    )
    if p.level is not None:
        queryset = queryset.filter(level=p.level)
    return list(
        queryset.select_related("level", "academic_year", "class_teacher").order_by("level__order", "name")
    )


def _rate(passed: int, counted: int) -> Decimal | None:
    return (Decimal(100 * passed) / counted).quantize(Decimal("0.1")) if counted else None


def _fixed(value: Decimal | None, decimals: int) -> Decimal | None:
    """A mark with the scale's decimals ("15.00", not "15"), as printed on report cards."""
    return None if value is None else Decimal(value).quantize(Decimal(1).scaleb(-decimals))


def class_table(class_group: ClassGroup, term: Term | None) -> dict[str, Any]:
    """A class's published results for a term (or the year): subjects, each student's marks, ranks, stats."""
    if term is not None:
        results = class_results(class_group, term, published_only=True)
        scale_model = results["scale"]
        subjects = [
            {
                "key": s["class_subject"],
                "name": s["subject_name"],
                "code": s["subject_code"] or s["subject_name"][:4].upper(),
                "coefficient": s["coefficient"],
                "teacher": s["teacher_name"],
            }
            for s in results["subjects"]
        ]
        students = [
            {
                "name": row["student_name"],
                "number": row["student_number"],
                "marks": {m["class_subject"]: m["mark"] for m in row["marks"]},
                "average": row["average"],
                "rank": row["rank"],
                "passed": row["passed"],
            }
            for row in results["students"]
        ]
    else:
        data = annual_cards(class_group)
        scale_model = data["scale"]
        lines = data["cards"][0]["lines"] if data["cards"] else []
        codes = dict(class_group.class_subjects.values_list("subject__name", "subject__code"))
        subjects = [
            {
                "key": index,
                "name": line["subject"],
                "code": codes.get(line["subject"]) or line["subject"][:4].upper(),
                "coefficient": line["coefficient"],
                "teacher": line["teacher"],
            }
            for index, line in enumerate(lines)
        ]
        students = [
            {
                "name": card["enrollment"].student.full_name,
                "number": card["enrollment"].student.student_number,
                "marks": {index: line["mark"] for index, line in enumerate(card["lines"])},
                "average": card["average"],
                "rank": card["rank"],
                "passed": card["passed"],
            }
            for card in data["cards"]
        ]
    scale = engine_scale(scale_model)
    for subject in subjects:
        subject["stats"] = engine.stats((s["marks"].get(subject["key"]) for s in students), scale)
    return {
        "class_group": class_group,
        "scale": scale_model,
        "subjects": subjects,
        "students": students,
        "stats": engine.stats((s["average"] for s in students), scale),
        "unpublished": _unpublished(class_group, term),
    }


def _unpublished(class_group: ClassGroup, term: Term | None) -> list[Gradebook]:
    """Gradebooks of the class not published yet (any term of the year when there is no term)."""
    queryset = Gradebook.objects.filter(class_subject__class_group=class_group).exclude(
        status=Gradebook.Status.PUBLISHED
    )
    if term is not None:
        queryset = queryset.filter(term=term)
    return list(
        queryset.select_related("class_subject__subject", "class_subject__teacher", "term").order_by(
            "term__order", "class_subject__subject__name"
        )
    )


def _period(L: dict, p: Params) -> str:
    return p.term.name if p.term else f"{L['year']} {p.academic_year.name}"


def _subtitle(L: dict, p: Params, *parts: str) -> str:
    return " · ".join(
        part for part in [_period(L, p), p.academic_year.name if p.term else "", *parts] if part
    )


def _mention(value, scale_model) -> str:
    return engine.mention(value, scale_model.mentions or [])


def _decision(L: dict, passed: bool | None) -> str:
    return "" if passed is None else L["passed"] if passed else L["failed"]


def _scope(L: dict, p: Params) -> str:
    if p.class_group:
        return p.class_group.name
    if p.level:
        return p.level.name
    return L["whole_school"]


# --- Grade report: one class -------------------------------------------------------------------------------


def grades_report(school, p: Params, language: str) -> Report:
    L = labels(language)
    assert p.class_group is not None  # noqa: S101 — the view requires a class for this report
    table = class_table(p.class_group, p.term)
    scale = table["scale"]
    decimals = scale.decimals
    max_mark = f"{Decimal(scale.max_mark).normalize():f}"
    subjects = table["subjects"]
    columns = [
        Column("rank", L["rank"], "number"),
        Column("student", L["student"]),
        Column("number", L["student_number"]),
        *[Column(f"s{s['key']}", s["code"], "mark") for s in subjects],
        Column("average", L["average"].format(max=max_mark), "mark"),
        Column("decision", L["decision"]),
    ]
    rows = [
        {
            "rank": s["rank"],
            "student": s["name"],
            "number": s["number"],
            **{
                f"s{subject['key']}": _fixed(s["marks"].get(subject["key"]), decimals) for subject in subjects
            },
            "average": _fixed(s["average"], decimals),
            "decision": _decision(L, s["passed"]),
        }
        for s in sorted(table["students"], key=lambda s: (s["rank"] is None, s["rank"] or 0, s["name"]))
    ]
    stats = table["stats"]
    totals = {
        "student": L["class_average"],
        **{f"s{s['key']}": _fixed(s["stats"].average, decimals) for s in subjects},
        "average": _fixed(stats.average, decimals),
    }
    notes = [
        L["published_only"],
        L["pass_note"].format(pass_mark=f"{Decimal(scale.pass_mark).normalize():f}", max=max_mark),
    ]
    missing = sorted({g.class_subject.subject.name for g in table["unpublished"]})
    if missing:
        notes.append(L["missing"].format(subjects=", ".join(missing)))
    subject_rows = [
        {
            "subject": s["name"],
            "code": s["code"],
            "coefficient": s["coefficient"],
            "teacher": s["teacher"],
            "average": _fixed(s["stats"].average, decimals),
            "highest": _fixed(s["stats"].highest, decimals),
            "lowest": _fixed(s["stats"].lowest, decimals),
            "passed": s["stats"].passed,
            "counted": s["stats"].counted,
            "rate": _rate(s["stats"].passed, s["stats"].counted),
        }
        for s in subjects
    ]
    return Report(
        key="grades",
        title=L["grades"],
        subtitle=_subtitle(L, p, p.class_group.name),
        sections=[
            Section(L["marks"], columns, rows, totals=totals, note=" ".join(notes)),
            Section(
                L["subject_stats"],
                [
                    Column("subject", L["subject"]),
                    Column("code", L["code"]),
                    Column("coefficient", L["coefficient"], "number"),
                    Column("teacher", L["teacher"]),
                    Column("average", L["class_average"], "mark"),
                    Column("highest", L["highest"], "mark"),
                    Column("lowest", L["lowest"], "mark"),
                    Column("passed", L["pass_count"], "number"),
                    Column("counted", L["ranked"], "number"),
                    Column("rate", L["pass_rate"], "percent"),
                ],
                subject_rows,
            ),
        ],
    )


# --- Ranking report: a class, a level or the school ----------------------------------------------------


def ranking_report(school, p: Params, language: str) -> Report:
    L = labels(language)
    tables = [class_table(c, p.term) for c in classes_for(school, p)]
    class_rows = []
    for table in tables:
        stats, decimals = table["stats"], table["scale"].decimals
        class_rows.append(
            {
                "class": table["class_group"].name,
                "level": table["class_group"].level.name,
                "students": len(table["students"]),
                "ranked": stats.counted,
                "average": _fixed(stats.average, decimals),
                "highest": _fixed(stats.highest, decimals),
                "lowest": _fixed(stats.lowest, decimals),
                "passed": stats.passed,
                "rate": _rate(stats.passed, stats.counted),
            }
        )
    class_rows.sort(key=lambda r: (r["average"] is None, -(r["average"] or 0), r["class"]))
    sections = [
        Section(
            L["classes"],
            [
                Column("class", L["class"]),
                Column("level", L["level"]),
                Column("students", L["students"], "number"),
                Column("ranked", L["ranked"], "number"),
                Column("average", L["class_average"], "mark"),
                Column("highest", L["highest"], "mark"),
                Column("lowest", L["lowest"], "mark"),
                Column("passed", L["pass_count"], "number"),
                Column("rate", L["pass_rate"], "percent"),
            ],
            class_rows,
            note=L["published_only"],
        )
    ]

    def ranking_columns(with_class: bool, max_mark: str) -> list[Column]:
        return [
            Column("rank", L["rank"], "number"),
            Column("student", L["student"]),
            Column("number", L["student_number"]),
            *([Column("class", L["class"])] if with_class else []),
            Column("average", L["average"].format(max=max_mark), "mark"),
            Column("mention", L["mention"]),
            Column("decision", L["decision"]),
        ]

    def student_row(student: dict, table: dict, rank: int | None) -> dict:
        scale = table["scale"]
        return {
            "rank": rank,
            "student": student["name"],
            "number": student["number"],
            "class": table["class_group"].name,
            "average": _fixed(student["average"], scale.decimals),
            "mention": _mention(student["average"], scale),
            "decision": _decision(L, student["passed"]),
        }

    def ordered(rows: list[dict]) -> list[dict]:
        return sorted(rows, key=lambda r: (r["rank"] is None, r["rank"] or 0, r["student"]))

    # A level's students ranked together, when its classes share one scale.
    if p.level is not None and tables and len({t["scale"].pk for t in tables}) == 1:
        scale = tables[0]["scale"]
        averages = {
            (index, n): s["average"] for index, t in enumerate(tables) for n, s in enumerate(t["students"])
        }
        level_ranks = engine.ranks(averages, scale.rank_method)
        rows = [
            student_row(s, t, level_ranks.get((index, n)))
            for index, t in enumerate(tables)
            for n, s in enumerate(t["students"])
        ]
        sections.append(
            Section(
                L["level_ranking"].format(level=p.level.name),
                ranking_columns(True, f"{Decimal(scale.max_mark).normalize():f}"),
                ordered(rows),
            )
        )
    for table in tables:
        max_mark = f"{Decimal(table['scale'].max_mark).normalize():f}"
        sections.append(
            Section(
                L["class_ranking"].format(name=table["class_group"].name),
                ranking_columns(False, max_mark),
                ordered([student_row(s, table, s["rank"]) for s in table["students"]]),
            )
        )
    return Report(
        key="ranking",
        title=L["ranking"],
        subtitle=_subtitle(L, p, _scope(L, p)),
        sections=sections,
        landscape=False,
    )


# --- Subject results: every subject of every class ----------------------------------------------------


def subjects_report(school, p: Params, language: str) -> Report:
    L = labels(language)
    rows = []
    unpublished = []
    for class_group in classes_for(school, p):
        table = class_table(class_group, p.term)
        decimals = table["scale"].decimals
        for s in table["subjects"]:
            rows.append(
                {
                    "class": class_group.name,
                    "subject": s["name"],
                    "teacher": s["teacher"],
                    "coefficient": s["coefficient"],
                    "average": _fixed(s["stats"].average, decimals),
                    "highest": _fixed(s["stats"].highest, decimals),
                    "lowest": _fixed(s["stats"].lowest, decimals),
                    "passed": s["stats"].passed,
                    "counted": s["stats"].counted,
                    "rate": _rate(s["stats"].passed, s["stats"].counted),
                }
            )
        for gradebook in table["unpublished"]:
            teacher = gradebook.class_subject.teacher
            unpublished.append(
                {
                    "class": class_group.name,
                    "subject": gradebook.class_subject.subject.name,
                    "term": gradebook.term.name,
                    "teacher": teacher.full_name if teacher else "",
                    "status": L["statuses"].get(gradebook.status, gradebook.status),
                }
            )
    return Report(
        key="subjects",
        title=L["subjects"],
        subtitle=_subtitle(L, p, _scope(L, p)),
        sections=[
            Section(
                L["subjects"],
                [
                    Column("class", L["class"]),
                    Column("subject", L["subject"]),
                    Column("teacher", L["teacher"]),
                    Column("coefficient", L["coefficient"], "number"),
                    Column("average", L["class_average"], "mark"),
                    Column("highest", L["highest"], "mark"),
                    Column("lowest", L["lowest"], "mark"),
                    Column("passed", L["pass_count"], "number"),
                    Column("counted", L["ranked"], "number"),
                    Column("rate", L["pass_rate"], "percent"),
                ],
                rows,
                note=L["subjects_note"],
            ),
            Section(
                L["not_published"],
                [
                    Column("class", L["class"]),
                    Column("subject", L["subject"]),
                    *([] if p.term else [Column("term", L["term"])]),
                    Column("teacher", L["teacher"]),
                    Column("status", L["status"]),
                ],
                unpublished,
                note=L["unpublished_note"],
            ),
        ],
    )


BUILDERS = {"grades": grades_report, "ranking": ranking_report, "subjects": subjects_report}
# Reports about one class only.
CLASS_ONLY = {"grades"}
