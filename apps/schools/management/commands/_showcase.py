"""Five fictional schools with a realistic history, for trying Mon École end to end (see seed_showcase).

Everything is generated from a fixed random seed, so two runs give the same schools. School codes start
with SHOWCASE_PREFIX: that is how `seed_showcase --delete` finds them again. No login account is created
here: the platform owner sees every school with Director rights.

Edge cases on purpose: twins with the same name, apostrophes and hyphens in names, missing birth dates,
students without guardians, siblings sharing guardians (sibling discount), full scholarships (nothing to
pay), overpayments kept as credit and refunded, reversed payments, a cancelled invoice, overdue fees, a
shortage at a cash closing, a student who left mid-September, a class change, a transfer, a late
enrolment, an archived student, a full class, an empty class, a subject without a teacher, a teacher
without classes, an archived teacher, staff on leave, a chronic absentee, ties in rankings, gradebooks in
every state (in progress, submitted, sent back, published) and every grading style (averages, total of
points, missing marks as zero, excused students), a previous school year with published results and
report-card comments, and a school registered yesterday with almost nothing in it.
"""

import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.academics.models import AcademicYear, ClassGroup, ClassSubject, Level, Subject
from apps.academics.services import create_academic_year, set_current_year
from apps.assessments.models import Assessment, Grade, Gradebook, GradeCategory, GradingScale, ReportComment
from apps.attendance.models import AttendanceRecord, ClassRegister, StaffAttendance
from apps.cashregister import services as cash
from apps.cashregister.models import CashSession
from apps.enrollments import services as enrolments
from apps.enrollments.models import Enrollment
from apps.finance import services as finance
from apps.finance.models import FeeCategory, FeeSchedule, Invoice, Payment, StudentDiscount
from apps.finance.selectors import student_credit
from apps.people.models import StaffMember, Student
from apps.people.services import (
    archive_student,
    create_staff,
    create_student,
    link_guardian,
    set_staff_status,
)
from apps.schools.models import School
from apps.schools.services import create_school

SHOWCASE_PREFIX = "essai-"

# --- Names ---------------------------------------------------------------------------------------------

GN_MALE = [
    "Mamadou",
    "Ibrahima",
    "Alpha",
    "Ousmane",
    "Sékou",
    "Thierno",
    "Abdoulaye",
    "Mohamed",
    "Lansana",
    "Fodé",
    "Amadou",
    "Boubacar",
    "Saliou",
    "Elhadj",
    "Aboubacar",
    "Moussa",
    "Karamoko",
    "Sidiki",
    "Mory",
    "N'Faly",
    "Cheick",
    "Alseny",
    "Ansoumane",
    "Daouda",
]
GN_FEMALE = [
    "Mariama",
    "Fatoumata",
    "Aïssatou",
    "Kadiatou",
    "Hawa",
    "Djénabou",
    "Aminata",
    "Binta",
    "Oumou",
    "Ramatoulaye",
    "Mafoudia",
    "Saran",
    "Néné",
    "M'Mah",
    "Fanta",
    "Hadja",
    "Kadija",
    "Marie-Claire",
    "Safiatou",
    "Tenin",
    "Djéneba",
    "Assiatou",
]
GN_LAST = [
    "Diallo",
    "Bah",
    "Barry",
    "Sow",
    "Camara",
    "Condé",
    "Keïta",
    "Touré",
    "Soumah",
    "Sylla",
    "Bangoura",
    "Kourouma",
    "Kouyaté",
    "Cissé",
    "Baldé",
    "Doumbouya",
    "Fofana",
    "Traoré",
    "Kaba",
    "Konaté",
    "Diakité",
    "Kanté",
    "Magassouba",
    "Haba",
    "Loua",
    "Onivogui",
    "Kamano",
    "Guilavogui",
    "Kourouma-Diallo",
]
GN_PLACES = [
    "Conakry",
    "Kankan",
    "Labé",
    "Kindia",
    "Nzérékoré",
    "Boké",
    "Mamou",
    "Faranah",
    "Siguiri",
    "Kissidougou",
]
GN_OCCUPATIONS = [
    "Commerçant(e)",
    "Enseignant(e)",
    "Cultivateur",
    "Fonctionnaire",
    "Infirmière",
    "Chauffeur",
    "Couturière",
    "Mécanicien",
    "Ménagère",
    "Gendarme",
    "Comptable",
    "Agent de banque",
]
LR_MALE = [
    "James",
    "Emmanuel",
    "Joseph",
    "Samuel",
    "Moses",
    "Prince",
    "Benjamin",
    "Daniel",
    "Augustine",
    "Momo",
    "Varney",
    "Sekou",
    "Abraham",
    "Isaac",
    "Patrick",
    "George",
    "Alvin",
    "Mulbah",
    "Boakai",
    "Jallah",
    "Fomba",
]
LR_FEMALE = [
    "Grace",
    "Esther",
    "Mercy",
    "Patience",
    "Comfort",
    "Ruth",
    "Deborah",
    "Hawa",
    "Musu",
    "Kebeh",
    "Blessing",
    "Precious",
    "Victoria",
    "Martha",
    "Princess",
    "Gbessay",
    "Oretha",
    "Satta",
    "Josephine",
    "Mary-Ann",
]
LR_LAST = [
    "Kollie",
    "Johnson",
    "Doe",
    "Kamara",
    "Flomo",
    "Kpoto",
    "Weah",
    "Sirleaf",
    "Tubman",
    "Gbowee",
    "Sumo",
    "Kromah",
    "Boima",
    "Pewee",
    "Toe",
    "Nyumah",
    "Zinnah",
    "Kerkula",
    "Varney",
    "Mulbah",
    "Sackor",
    "O'Neil",
    "Harris",
    "Cooper",
    "Wesseh",
]
LR_PLACES = [
    "Monrovia",
    "Paynesville",
    "Gbarnga",
    "Buchanan",
    "Kakata",
    "Ganta",
    "Harper",
    "Voinjama",
    "Zwedru",
]
LR_OCCUPATIONS = [
    "Trader",
    "Teacher",
    "Farmer",
    "Nurse",
    "Driver",
    "Civil servant",
    "Tailor",
    "Mechanic",
    "Security guard",
    "Market woman",
    "Pastor",
    "Bank clerk",
]

FR_SUBJECTS = [
    ("Mathématiques", "MATH", 4),
    ("Français", "FR", 4),
    ("Anglais", "ANG", 2),
    ("Histoire-Géographie", "HG", 2),
    ("Sciences de la vie et de la Terre", "SVT", 2),
    ("Physique-Chimie", "PC", 3),
    ("Éducation civique et morale", "ECM", 1),
    ("Éducation physique et sportive", "EPS", 1),
]
FR_PRIMARY_SUBJECTS = [
    ("Calcul", "CAL", 3),
    ("Lecture", "LEC", 3),
    ("Écriture", "ECR", 2),
    ("Éveil", "EVE", 1),
    ("Dessin", "DES", 1),
]
EN_SUBJECTS = [
    ("Mathematics", "MATH", 3),
    ("Language Arts", "LA", 3),
    ("General Science", "SCI", 2),
    ("Social Studies", "SOC", 2),
    ("French", "FRE", 1),
    ("Physical Education", "PE", 1),
    ("Literature in English", "LIT", 2),
    ("Biology", "BIO", 2),
]
EN_PRIMARY_SUBJECTS = [
    ("Mathematics", "MATH", 3),
    ("Reading", "READ", 3),
    ("Spelling", "SPEL", 2),
    ("Science", "SCI", 2),
    ("Social Studies", "SOC", 2),
    ("Bible / Moral Education", "MOR", 1),
]

FR_BANDS = [
    {"min": 16, "label": "Très bien"},
    {"min": 14, "label": "Bien"},
    {"min": 12, "label": "Assez bien"},
    {"min": 10, "label": "Passable"},
]
FR_PRIMARY_BANDS = [
    {"min": 8, "label": "Très bien"},
    {"min": 7, "label": "Bien"},
    {"min": 6, "label": "Assez bien"},
    {"min": 5, "label": "Passable"},
]
LR_BANDS = [
    {"min": 90, "label": "Distinction"},
    {"min": 80, "label": "Merit"},
    {"min": 70, "label": "Credit"},
    {"min": 60, "label": "Pass"},
]


@dataclass
class Spec:
    name: str
    code: str
    country: str
    currency: str
    timezone: str
    language: str
    address: str
    phone: str
    email: str
    levels: list[tuple[str, int, str, int, int]]  # name, order, cycle, typical age, classes
    per_class: int
    terms: int
    scale: dict
    primary_scale: dict | None = None
    with_history: bool = False
    fees: dict = field(default_factory=dict)
    fresh: bool = False  # registered yesterday: almost nothing set up


SPECS = [
    Spec(
        name="Complexe Scolaire Les Palmiers",
        code="essai-palmiers",
        country="GN",
        currency="GNF",
        timezone="Africa/Conakry",
        language="fr",
        address="Lambanyi, Ratoma, Conakry",
        phone="+224 622 41 18 90",
        email="contact@lespalmiers.example",
        levels=[
            ("7ème année", 7, "lower_secondary", 12, 2),
            ("8ème année", 8, "lower_secondary", 13, 2),
            ("9ème année", 9, "lower_secondary", 14, 2),
            ("10ème année", 10, "lower_secondary", 15, 2),
            ("11ème année", 11, "upper_secondary", 16, 1),
            ("12ème année", 12, "upper_secondary", 17, 1),
            ("Terminale", 13, "upper_secondary", 18, 1),
        ],
        per_class=34,
        terms=3,
        scale={
            "max_mark": 20,
            "pass_mark": 10,
            "decimals": 2,
            "rank_method": "competition",
            "mentions": FR_BANDS,
        },
        with_history=True,
        fees={"registration": 300_000, "tuition": 3_600_000, "exam": 150_000},
    ),
    Spec(
        name="Groupe Scolaire Lumière de Kankan",
        code="essai-kankan",
        country="GN",
        currency="GNF",
        timezone="Africa/Conakry",
        language="fr",
        address="Quartier Kabada, Kankan",
        phone="+224 664 20 33 07",
        email="",
        levels=[
            ("1ère année", 1, "primary", 7, 1),
            ("2ème année", 2, "primary", 8, 1),
            ("3ème année", 3, "primary", 9, 1),
            ("4ème année", 4, "primary", 10, 1),
            ("5ème année", 5, "primary", 11, 1),
            ("6ème année", 6, "primary", 12, 1),
            ("7ème année", 7, "lower_secondary", 13, 1),
            ("8ème année", 8, "lower_secondary", 14, 1),
        ],
        per_class=28,
        terms=3,
        scale={"max_mark": 20, "pass_mark": 10, "decimals": 2, "rank_method": "dense", "mentions": FR_BANDS},
        primary_scale={
            "max_mark": 10,
            "pass_mark": 5,
            "decimals": 2,
            "rank_method": "dense",
            "mentions": FR_PRIMARY_BANDS,
        },
        fees={"registration": 150_000, "tuition": 1_500_000},
    ),
    Spec(
        name="Paynesville Hope Academy",
        code="essai-paynesville",
        country="LR",
        currency="LRD",
        timezone="Africa/Monrovia",
        language="en",
        address="ELWA Junction, Paynesville, Montserrado County",
        phone="+231 77 640 2215",
        email="office@paynesvillehope.example",
        levels=[
            ("Grade 7", 7, "lower_secondary", 12, 2),
            ("Grade 8", 8, "lower_secondary", 13, 2),
            ("Grade 9", 9, "lower_secondary", 14, 1),
            ("Grade 10", 10, "upper_secondary", 15, 1),
            ("Grade 11", 11, "upper_secondary", 16, 1),
            ("Grade 12", 12, "upper_secondary", 17, 1),
        ],
        per_class=32,
        terms=2,
        scale={
            "max_mark": 100,
            "pass_mark": 70,
            "decimals": 0,
            "rank_method": "competition",
            "mentions": LR_BANDS,
        },
        with_history=True,
        fees={"registration": 2_500, "tuition": 36_000, "exam": 3_000},
    ),
    Spec(
        name="Gbarnga Community Elementary School",
        code="essai-gbarnga",
        country="LR",
        currency="USD",
        timezone="Africa/Monrovia",
        language="en",
        address="Weala Road, Gbarnga, Bong County",
        phone="+231 88 512 4470",
        email="",
        levels=[
            ("Grade 1", 1, "primary", 6, 1),
            ("Grade 2", 2, "primary", 7, 1),
            ("Grade 3", 3, "primary", 8, 1),
            ("Grade 4", 4, "primary", 9, 1),
            ("Grade 5", 5, "primary", 10, 1),
            ("Grade 6", 6, "primary", 11, 1),
        ],
        per_class=22,
        terms=2,
        scale={"max_mark": 100, "pass_mark": 60, "decimals": 1, "rank_method": "competition", "mentions": []},
        fees={"registration": 15, "tuition": 180},
    ),
    Spec(
        name="Institut Fouta Excellence",
        code="essai-labe",
        country="GN",
        currency="GNF",
        timezone="Africa/Conakry",
        language="fr",
        address="Labé centre, près de la grande mosquée",
        phone="+224 657 08 91 12",
        email="",
        levels=[("7ème année", 7, "lower_secondary", 12, 1), ("8ème année", 8, "lower_secondary", 13, 1)],
        per_class=6,
        terms=3,
        scale={},
        fresh=True,
    ),
]

# --- Helpers ---------------------------------------------------------------------------------------------


def school_days(start: date, end: date) -> list[date]:
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def aware(day: date, hour: int, minute: int = 0) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


class Builder:
    def __init__(self, spec: Spec, out):
        self.spec = spec
        self.out = out
        self.rnd = random.Random(f"mon-ecole-showcase-{spec.code}")  # noqa: S311 - reproducible fake data
        self.fr = spec.language == "fr"
        self.male = GN_MALE if self.fr else LR_MALE
        self.female = GN_FEMALE if self.fr else LR_FEMALE
        self.last = GN_LAST if self.fr else LR_LAST
        self.places = GN_PLACES if self.fr else LR_PLACES
        self.occupations = GN_OCCUPATIONS if self.fr else LR_OCCUPATIONS
        self.today = timezone.localdate()
        self.ability: dict[int, float] = {}  # student id → 0..1, steady across subjects and years
        self.counts: dict[str, int] = {}

    def note(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n

    def phone(self) -> str:
        n = self.rnd.randint
        if self.fr:
            return f"+224 6{self.rnd.choice('2256')}{n(0, 9)} {n(10, 99)} {n(10, 99)} {n(10, 99)}"
        return f"+231 {self.rnd.choice(['77', '88', '55'])} {n(100, 999)} {n(1000, 9999)}"

    # --- School, years, levels, classes, subjects, staff -------------------------------------------------

    def build(self) -> School:
        spec = self.spec
        school = create_school(
            name=spec.name,
            code=spec.code,
            country=spec.country,
            currency=spec.currency,
            timezone=spec.timezone,
            default_language=spec.language,
            address=spec.address,
            phone=spec.phone,
            email=spec.email,
        )
        self.school = school
        start_year = 2026 if self.today >= date(2026, 9, 1) else self.today.year
        self.current_start = date(start_year, 9, 7) if not self.fr else date(start_year, 9, 14)
        self.current_end = date(start_year + 1, 6, 30)
        if spec.fresh:
            self.build_fresh()
            return school
        if spec.scale:
            GradingScale.objects.create(school=school, **spec.scale)
        self.subjects = self.make_subjects()
        self.staff = self.make_staff()
        if spec.with_history:
            previous = create_academic_year(
                school,
                name=f"{start_year - 1}-{start_year}",
                start_date=self.current_start.replace(year=start_year - 1),
                end_date=date(start_year, 6, 30),
                term_count=spec.terms,
            )
            self.levels = self.make_levels()
            old_classes = self.make_classes(previous)
            self.make_fees(previous)
            old_enrolled = self.enrol_everyone(previous, old_classes)
            self.history_finance(previous, old_enrolled)
            self.grades_for_year(previous, old_classes, complete=True)
            self.attendance(
                previous, old_classes, previous.start_date, previous.start_date + timedelta(days=60)
            )
            self.history_comments(previous, old_classes)
            year = create_academic_year(
                school,
                name=f"{start_year}-{start_year + 1}",
                start_date=self.current_start,
                end_date=self.current_end,
                term_count=spec.terms,
            )
            set_current_year(year)
            classes = self.make_classes(year)
            self.make_fees(year)
            self.promote(previous, old_classes, classes)
            previous.status = AcademicYear.Status.CLOSED
            previous.save(update_fields=["status", "updated_at"])
        else:
            year = create_academic_year(
                school,
                name=f"{start_year}-{start_year + 1}",
                start_date=self.current_start,
                end_date=self.current_end,
                term_count=spec.terms,
            )
            self.levels = self.make_levels()
            classes = self.make_classes(year)
            self.make_fees(year)
        self.year = year
        self.enrol_newcomers(year, classes)
        self.edge_cases(year, classes)
        self.current_finance(year)
        self.grades_for_year(year, classes, complete=False)
        self.attendance(year, classes, year.start_date, self.today - timedelta(days=1))
        self.staff_attendance(year)
        return school

    def make_subjects(self) -> dict[str, Subject]:
        subjects = {}
        main = FR_SUBJECTS if self.fr else EN_SUBJECTS
        for label, code, coef in main:
            subjects[code] = Subject.objects.create(
                school=self.school, name=label, code=code, default_coefficient=coef
            )
        if any(cycle == "primary" for _, _, cycle, _, _ in self.spec.levels):
            for label, code, coef in FR_PRIMARY_SUBJECTS if self.fr else EN_PRIMARY_SUBJECTS:
                key = f"P-{code}"
                subjects[key] = Subject.objects.create(
                    school=self.school, name=label, code=key, default_coefficient=coef
                )
        return subjects

    def make_staff(self) -> list[StaffMember]:
        staff = []
        teachers = 12 if len(self.spec.levels) > 6 else 9
        for index in range(teachers):
            gender = "F" if index % 3 == 1 else "M"
            first = self.rnd.choice(self.female if gender == "F" else self.male)
            staff.append(
                create_staff(
                    self.school,
                    data={
                        "first_name": first,
                        "last_name": self.rnd.choice(self.last),
                        "gender": gender,
                        "staff_type": "teacher",
                        "qualification": self.rnd.choice(
                            ["Licence", "Master", "DEUG", "CAP"]
                            if self.fr
                            else ["B.Sc.", "B.Ed.", "AA", "C-Certificate"]
                        ),
                        "phone": self.phone(),
                        "employment_date": date(self.rnd.randint(2005, 2025), self.rnd.randint(1, 12), 1),
                    },
                )
            )
        office = (
            [
                ("Secrétaire", "administrative"),
                ("Comptable", "administrative"),
                ("Gardien", "support"),
                ("Cuisinière", "support"),
            ]
            if self.fr
            else [
                ("Registrar", "administrative"),
                ("Business manager", "administrative"),
                ("Security", "support"),
                ("Cook", "support"),
            ]
        )
        for position, staff_type in office:
            gender = "F" if position in ("Secrétaire", "Cuisinière", "Registrar", "Cook") else "M"
            staff.append(
                create_staff(
                    self.school,
                    data={
                        "first_name": self.rnd.choice(self.female if gender == "F" else self.male),
                        "last_name": self.rnd.choice(self.last),
                        "gender": gender,
                        "staff_type": staff_type,
                        "position": position,
                        "phone": self.phone(),
                    },
                )
            )
        # A teacher who left last year (archived) and one hired this month with no class yet.
        left = create_staff(
            self.school,
            data={
                "first_name": self.rnd.choice(self.male),
                "last_name": self.rnd.choice(self.last),
                "staff_type": "teacher",
            },
        )
        set_staff_status(left, archived=True)
        staff.append(
            create_staff(
                self.school,
                data={
                    "first_name": self.rnd.choice(self.female),
                    "last_name": self.rnd.choice(self.last),
                    "gender": "F",
                    "staff_type": "teacher",
                    "position": "Stagiaire" if self.fr else "Trainee teacher",
                    "employment_date": self.today - timedelta(days=10),
                },
            )
        )
        return staff

    @property
    def teachers(self) -> list[StaffMember]:
        return [s for s in self.staff if s.staff_type == "teacher" and s.status == "active"][:-1]

    def make_levels(self) -> list[tuple[Level, int, int]]:
        levels = []
        for name, order, cycle, age, count in self.spec.levels:
            level = Level.objects.create(school=self.school, name=name, order=order, cycle=cycle)
            if cycle == "primary" and self.spec.primary_scale:
                GradingScale.objects.create(school=self.school, level=level, **self.spec.primary_scale)
            levels.append((level, age, count))
        return levels

    def make_classes(self, year: AcademicYear) -> list[ClassGroup]:
        classes: list[ClassGroup] = []
        teachers = self.teachers
        for level, _, count in self.levels:
            primary = level.cycle == "primary"
            for letter in "ABC"[:count]:
                short = level.name.split()[0] if self.fr else level.name.replace("Grade ", "G")
                name = f"{short} {letter}" if count > 1 else (level.name if not self.fr else f"{short}")
                class_group = ClassGroup.objects.create(
                    school=self.school,
                    academic_year=year,
                    level=level,
                    name=name,
                    room=f"{'Salle' if self.fr else 'Room'} {level.order}{letter}",
                    capacity=self.spec.per_class + 6,
                )
                codes = (
                    [k for k in self.subjects if k.startswith("P-")]
                    if primary
                    else [k for k in self.subjects if not k.startswith("P-")]
                )
                for index, code in enumerate(codes):
                    # A primary class teacher teaches everything; secondary subjects rotate among teachers.
                    teacher = (
                        teachers[len(classes) % len(teachers)]
                        if primary
                        else teachers[(index + len(classes)) % len(teachers)]
                    )
                    ClassSubject.objects.create(
                        school=self.school,
                        class_group=class_group,
                        subject=self.subjects[code],
                        teacher=teacher,
                        coefficient=self.subjects[code].default_coefficient,
                    )
                class_group.class_teacher = teachers[len(classes) % len(teachers)]
                class_group.save(update_fields=["class_teacher"])
                classes.append(class_group)
        # Edge cases: the last class has a subject nobody teaches; another has no class teacher.
        if len(classes) > 2:
            orphan = ClassSubject.objects.filter(class_group=classes[-1]).order_by("-id").first()
            if orphan is not None:
                ClassSubject.objects.filter(pk=orphan.pk).update(teacher=None)
            classes[1].class_teacher = None
            classes[1].save(update_fields=["class_teacher"])
        return classes

    # --- Fees ---------------------------------------------------------------------------------------------

    def make_fees(self, year: AcademicYear) -> None:
        fees = self.spec.fees
        if not fees:
            return
        categories = {}
        for kind, label_fr, label_en in [
            ("registration", "Inscription", "Registration"),
            ("tuition", "Scolarité", "Tuition"),
            ("exam", "Frais d'examen", "Exam fees"),
            ("uniform", "Tenue scolaire", "Uniform"),
        ]:
            category, _ = FeeCategory.objects.get_or_create(
                school=self.school, name=label_fr if self.fr else label_en, defaults={"kind": kind}
            )
            categories[kind] = category
        self.categories = categories
        start = year.start_date
        for level, _, _ in self.levels:
            factor = Decimal("1") + Decimal(level.order) / Decimal(40)
            tuition = (Decimal(fees["tuition"]) * factor).quantize(
                Decimal("1000") if self.spec.currency == "GNF" else Decimal("1")
            )
            parts = 3 if self.fr else 2
            share = (tuition / parts).quantize(Decimal("1"))
            installments = [
                {
                    "label": "",
                    "due_date": (start + timedelta(days=21 + 100 * i)).isoformat(),
                    "amount": str(share if i < parts - 1 else tuition - share * (parts - 1)),
                }
                for i in range(parts)
            ]
            FeeSchedule.objects.create(
                school=self.school,
                academic_year=year,
                level=level,
                category=categories["tuition"],
                amount=tuition,
                installments=installments,
            )
            FeeSchedule.objects.create(
                school=self.school,
                academic_year=year,
                level=level,
                category=categories["registration"],
                applies_to=FeeSchedule.AppliesTo.NEW,
                amount=Decimal(fees["registration"]),
                installments=[
                    {"label": "", "due_date": start.isoformat(), "amount": str(fees["registration"])}
                ],
            )
            if fees.get("exam") and level.order in (10, 12, 13, 9, 6):
                FeeSchedule.objects.create(
                    school=self.school,
                    academic_year=year,
                    level=level,
                    category=categories["exam"],
                    amount=Decimal(fees["exam"]),
                    installments=[
                        {
                            "label": "",
                            "due_date": (start + timedelta(days=150)).isoformat(),
                            "amount": str(fees["exam"]),
                        }
                    ],
                )

    # --- Students -----------------------------------------------------------------------------------------

    def new_student(self, age: int, year: AcademicYear, *, last: str | None = None, **extra) -> Student:
        gender = extra.pop("gender", self.rnd.choice("MF"))
        first = extra.pop("first_name", self.rnd.choice(self.male if gender == "M" else self.female))
        born = date(year.start_date.year - age, 1, 1) + timedelta(days=self.rnd.randint(0, 364))
        data = {
            "first_name": first,
            "last_name": last or self.rnd.choice(self.last),
            "gender": gender,
            "date_of_birth": born if self.rnd.random() > 0.03 else None,
            "place_of_birth": self.rnd.choice(self.places) if self.rnd.random() > 0.05 else "",
            "nationality": ("Guinéenne" if self.fr else "Liberian")
            if self.rnd.random() > 0.04
            else ("Ivoirienne" if self.fr else "Sierra Leonean"),
            "address": self.rnd.choice(self.places),
            **extra,
        }
        student = create_student(self.school, data=data)
        self.ability[student.pk] = min(0.98, max(0.15, self.rnd.gauss(0.58, 0.16)))
        return student

    def family(self, student: Student, families: list) -> None:
        """Parents for a new student: often a new family, sometimes a sibling's, sometimes nobody."""
        roll = self.rnd.random()
        if roll < 0.04:
            self.note("students without guardians")
            return
        if families and roll < 0.18:
            guardians = self.rnd.choice(families)
            for guardian, relationship, primary in guardians:
                link_guardian(student, guardian=guardian, relationship=relationship, is_primary=primary)
            self.note("siblings")
            return
        links = [
            link_guardian(
                student,
                guardian_data={
                    "first_name": self.rnd.choice(self.female),
                    "last_name": self.rnd.choice(self.last),
                    "phone": self.phone(),
                    "occupation": self.rnd.choice(self.occupations),
                },
                relationship="mother",
                is_primary=True,
                is_financial_contact=self.rnd.random() < 0.5,
            )
        ]
        if self.rnd.random() < 0.65:
            links.append(
                link_guardian(
                    student,
                    guardian_data={
                        "first_name": self.rnd.choice(self.male),
                        "last_name": student.last_name,
                        "phone": self.phone(),
                        "occupation": self.rnd.choice(self.occupations),
                    },
                    relationship="father",
                )
            )
        elif self.rnd.random() < 0.3:
            links.append(
                link_guardian(
                    student,
                    guardian_data={
                        "first_name": self.rnd.choice(self.male),
                        "last_name": self.rnd.choice(self.last),
                        "phone": self.phone(),
                    },
                    relationship="guardian",
                )
            )
        families.append([(link.guardian, link.relationship, link.is_primary) for link in links])

    def enrol_everyone(self, year: AcademicYear, classes: list[ClassGroup]) -> list[Enrollment]:
        families: list = []
        self.families = families
        enrolled = []
        ages = {level.pk: age for level, age, _ in self.levels}
        for class_group in classes:
            for _ in range(self.spec.per_class - self.rnd.randint(0, 4)):
                student = self.new_student(ages[class_group.level_id] - 1, year)
                self.family(student, families)
                enrolled.append(
                    enrolments.enroll(
                        student,
                        class_group,
                        enrollment_date=year.start_date - timedelta(days=self.rnd.randint(3, 30)),
                    )
                )
        Invoice.objects.filter(school=self.school, academic_year=year).update(
            issue_date=year.start_date - timedelta(days=7)
        )
        return enrolled

    def promote(
        self, previous: AcademicYear, old_classes: list[ClassGroup], classes: list[ClassGroup]
    ) -> None:
        """Last year's students move up one level; the last level leaves; a few repeat or leave."""
        by_level: dict[int, list[ClassGroup]] = {}
        for c in classes:
            by_level.setdefault(c.level_id, []).append(c)
        levels = [level for level, _, _ in self.levels]
        for old in old_classes:
            index = next(i for i, level in enumerate(levels) if level.pk == old.level_id)
            active = list(old.enrollments.filter(status=Enrollment.Status.ACTIVE).select_related("student"))
            leaving = [e for e in active if self.rnd.random() < 0.06]
            repeating = [e for e in active if e not in leaving and self.ability[e.student_id] < 0.33]
            moving = [e for e in active if e not in leaving and e not in repeating]
            for enrollment in leaving:
                enrolments.withdraw(
                    enrollment, on=previous.end_date, reason="Déménagement" if self.fr else "Family moved"
                )
            if repeating:
                same = by_level[old.level_id][0]
                enrolments.promote(old, same, enrollments=repeating)
                self.note("students repeating a year", len(repeating))
            if index + 1 < len(levels) and moving:
                targets = by_level[levels[index + 1].pk]
                half = (len(moving) + 1) // 2 if len(targets) > 1 else len(moving)
                for target, group in ((targets[0], moving[:half]), (targets[-1], moving[half:])):
                    if not group:
                        continue
                    # Upper levels have fewer, bigger classes (60 is common in Conakry).
                    seated = target.enrollments.filter(status=Enrollment.Status.ACTIVE).count()
                    if target.capacity and seated + len(group) > target.capacity:
                        target.capacity = seated + len(group) + 4
                        target.save(update_fields=["capacity"])
                    enrolments.promote(old, target, enrollments=group)
            elif moving:
                for enrollment in moving:
                    enrollment.status = Enrollment.Status.COMPLETED
                    enrollment.ended_on = previous.end_date
                    enrollment.save(update_fields=["status", "ended_on", "updated_at"])
                self.note("graduates", len(moving))
        Invoice.objects.filter(school=self.school, academic_year=self.year_of(classes)).update(
            issue_date=self.year_of(classes).start_date - timedelta(days=10)
        )

    @staticmethod
    def year_of(classes: list[ClassGroup]) -> AcademicYear:
        return classes[0].academic_year

    def enrol_newcomers(self, year: AcademicYear, classes: list[ClassGroup]) -> None:
        """Fill every class with new students (the first level is all new when there is no history)."""
        self.families = getattr(self, "families", [])
        ages = {level.pk: age for level, age, _ in self.levels}
        for class_group in classes:
            present = class_group.enrollments.filter(status=Enrollment.Status.ACTIVE).count()
            target = self.spec.per_class - self.rnd.randint(0, 5)
            for _ in range(max(0, target - present)):
                student = self.new_student(ages[class_group.level_id], year)
                self.family(student, self.families)
                transfer = self.rnd.random() < 0.08
                enrolments.enroll(
                    student,
                    class_group,
                    enrollment_date=year.start_date - timedelta(days=self.rnd.randint(1, 25)),
                    kind=Enrollment.Kind.TRANSFER_IN if transfer else Enrollment.Kind.NEW,
                    previous_school=(
                        self.rnd.choice(["Lycée 2 Octobre", "Collège Sainte-Marie", "École Les Bambous"])
                        if self.fr
                        else self.rnd.choice(["St. Teresa School", "Monrovia Central High", "Faith Academy"])
                    )
                    if transfer
                    else "",
                )
        Invoice.objects.filter(school=self.school, academic_year=year, issue_date__gt=year.start_date).update(
            issue_date=year.start_date - timedelta(days=5)
        )

    def edge_cases(self, year: AcademicYear, classes: list[ClassGroup]) -> None:
        ages = {level.pk: age for level, age, _ in self.levels}
        first = classes[0]
        # Twins with exactly the same name in the same class.
        twin_name = self.rnd.choice(self.male)
        last = self.rnd.choice(self.last)
        for _ in range(2):
            twin = self.new_student(ages[first.level_id], year, last=last, first_name=twin_name, gender="M")
            twin.date_of_birth = date(year.start_date.year - ages[first.level_id], 3, 14)
            twin.save(update_fields=["date_of_birth"])
            enrolments.enroll(twin, first, enrollment_date=year.start_date - timedelta(days=12))
        self.note("twins with the same name", 2)
        # A very long name.
        long = self.new_student(
            ages[first.level_id],
            year,
            first_name="Marie-Claire Fatoumata Bintou" if self.fr else "Mary-Ann Precious Gbessay",
            last="Kourouma-Diallo de la Haute-Guinée" if self.fr else "Johnson-Sirleaf Kpoto",
            gender="F",
        )
        enrolments.enroll(long, first, enrollment_date=year.start_date)
        # A student who left on the 4th week, one who changed class, one who arrived late.
        active = list(
            Enrollment.objects.filter(class_group=classes[2 % len(classes)], status=Enrollment.Status.ACTIVE)
        )
        if active:
            gone = active[0]
            enrolments.withdraw(
                gone,
                on=min(year.start_date + timedelta(days=17), self.today - timedelta(days=1)),
                reason="Départ pour Dakar" if self.fr else "Moved to Ghana",
                transfer_to="Lycée de Dakar" if self.fr else "Accra International School",
            )
            self.note("withdrawn mid-month")
        same_level = [c for c in classes if c.level_id == first.level_id and c.pk != first.pk]
        if same_level:
            mover = Enrollment.objects.filter(class_group=first, status=Enrollment.Status.ACTIVE).order_by(
                "-id"
            )[3]
            enrolments.change_class(
                mover,
                same_level[0],
                on=min(year.start_date + timedelta(days=10), self.today - timedelta(days=2)),
                reason="Demande des parents" if self.fr else "Parents' request",
            )
            self.note("class changes")
        late = self.new_student(ages[classes[-1].level_id], year)
        enrolments.enroll(late, classes[-1], enrollment_date=self.today - timedelta(days=3))
        self.note("late enrolments")
        # An archived student (left before this year, never re-enrolled).
        archived = self.new_student(ages[first.level_id], year)
        archive_student(archived)
        self.note("archived students")
        # A class filled to capacity and an empty class at the top level.
        full = classes[min(3, len(classes) - 1)]
        while full.enrollments.filter(status=Enrollment.Status.ACTIVE).count() < (full.capacity or 0):
            enrolments.enroll(
                self.new_student(ages[full.level_id], year), full, enrollment_date=year.start_date
            )
        self.note("full classes")
        ClassGroup.objects.create(
            school=self.school,
            academic_year=year,
            level=classes[-1].level,
            name=("Terminale B (ouverture prévue)" if self.fr else f"{classes[-1].name} (evening)"),
            capacity=30,
        )
        self.note("empty classes")

    # --- Money --------------------------------------------------------------------------------------------

    def session(self, opened: date) -> CashSession:
        register = cash.default_register(self.school)
        session = cash.open_session(register, opening_balance=None)
        session.opened_at = aware(opened, 7, 30)
        session.save(update_fields=["opened_at"])
        return session

    def close(self, session: CashSession, day: date, *, shortage: Decimal = Decimal(0)) -> None:
        expected = cash.totals(session)["expected"]
        cash.close_session(
            session,
            counted=expected - shortage,
            note=("Billet de 20 000 manquant, à vérifier" if self.fr else "Missing note, under review")
            if shortage
            else "",
        )
        CashSession.objects.filter(pk=session.pk).update(closed_at=aware(day, 17, 30))

    def pay(
        self,
        student: Student,
        amount: Decimal,
        day: date,
        *,
        session: CashSession | None = None,
        method: str | None = None,
    ) -> Payment | None:
        method = (
            method
            or self.rnd.choices(
                ["cash", "mobile_money", "bank_transfer"], weights=[6, 3, 1] if self.fr else [5, 2, 3]
            )[0]
        )
        if method == "cash" and session is None:
            method = "mobile_money"
        reference = ""
        if method == "mobile_money":
            reference = f"{'OM' if self.fr else 'MTN'}{self.rnd.randint(10_000_000, 99_999_999)}"
        elif method == "bank_transfer":
            reference = f"{'ECOBANK' if self.fr else 'LBDI'}-{self.rnd.randint(100_000, 999_999)}"
        try:
            return finance.record_payment(
                student,
                amount=amount,
                method=method,
                payment_date=day,
                reference=reference,
                cash_session=session if method == "cash" else None,
            )
        except Exception:
            return None

    def history_finance(self, year: AcademicYear, enrolled: list[Enrollment]) -> None:
        """Last year: most families paid everything, some still owe (arrears carried into this year)."""
        months = [year.start_date + timedelta(days=30 * m) for m in range(9)]
        for month_start in months:
            session = self.session(month_start)
            for enrollment in enrolled:
                if self.rnd.random() < 0.28:
                    invoice = Invoice.objects.filter(enrollment=enrollment).first()
                    if invoice is None:
                        continue
                    part = (invoice.total / Decimal(3)).quantize(Decimal("1"))
                    self.pay(
                        enrollment.student,
                        part,
                        month_start + timedelta(days=self.rnd.randint(0, 20)),
                        session=session,
                    )
            self.close(session, month_start + timedelta(days=27))

    def current_finance(self, year: AcademicYear) -> None:
        students = list(
            Student.objects.filter(
                school=self.school, enrollments__academic_year=year, enrollments__status="active"
            ).distinct()
        )
        # Discounts: siblings 10 %, a few scholarships, and one full scholarship.
        tuition = self.categories["tuition"]
        for student in students[:: max(1, len(students) // 6)][:6]:
            StudentDiscount.objects.get_or_create(
                school=self.school,
                student=student,
                academic_year=year,
                category=tuition,
                is_active=True,
                defaults={
                    "kind": "percent",
                    "value": Decimal(50),
                    "reason": "scholarship",
                    "note": "Bourse d'excellence" if self.fr else "Merit scholarship",
                },
            )
        full = students[5]
        StudentDiscount.objects.update_or_create(
            school=self.school,
            student=full,
            academic_year=year,
            category=tuition,
            is_active=True,
            defaults={
                "kind": "percent",
                "value": Decimal(100),
                "reason": "staff_child",
                "note": "Enfant du personnel" if self.fr else "Staff child",
            },
        )
        self.note("discounts")
        days = school_days(year.start_date - timedelta(days=10), self.today)
        weeks = [days[i : i + 5] for i in range(0, len(days), 5)]
        payers = [s for s in students if self.rnd.random() < 0.82]
        paid = set()
        for number, week in enumerate(weeks):
            last_week = number == len(weeks) - 1
            session = self.session(week[0])
            for student in payers:
                if self.rnd.random() > 0.3:
                    continue
                account = Invoice.objects.filter(student=student, academic_year=year, status="issued").first()
                if account is None:
                    continue
                share = self.rnd.choice([Decimal("0.25"), Decimal("0.34"), Decimal("0.5"), Decimal("1")])
                amount = (account.total * share).quantize(
                    Decimal("1000") if self.spec.currency == "GNF" else Decimal("1")
                )
                if amount > 0:
                    if self.pay(student, amount, self.rnd.choice(week), session=session):
                        paid.add(student.pk)
            if self.rnd.random() < 0.6:
                try:
                    finance.record_expense(
                        self.school,
                        amount=Decimal(
                            self.rnd.choice([150_000, 450_000, 90_000])
                            if self.fr
                            else self.rnd.choice([40, 120, 15])
                        ),
                        category=self.rnd.choice(["supplies", "maintenance", "transport", "food"]),
                        method="cash",
                        description=self.rnd.choice(
                            [
                                "Craies et marqueurs",
                                "Réparation des tables",
                                "Carburant du groupe électrogène",
                                "Riz pour la cantine",
                            ]
                            if self.fr
                            else [
                                "Chalk and markers",
                                "Desk repairs",
                                "Generator fuel",
                                "Rice for the canteen",
                            ]
                        ),
                        expense_date=week[-1],
                        cash_session=session,
                    )
                except Exception:  # noqa: S110 - an expense larger than the till is simply skipped
                    pass
            if last_week:
                self.open_session = session  # the register stays open today
            else:
                self.close(
                    session,
                    week[-1],
                    shortage=Decimal(20_000 if self.fr else 5) if number == 1 else Decimal(0),
                )
        # Salaries by bank transfer, rent, electricity.
        monthly = (
            [
                ("salaries", "Salaires de septembre", 48_000_000),
                ("rent", "Loyer du bâtiment B", 6_000_000),
                ("utilities", "Facture EDG", 1_250_000),
            ]
            if self.fr
            else [
                ("salaries", "September salaries", 210_000),
                ("rent", "Rent, annex building", 25_000),
                ("utilities", "LEC electricity bill", 4_800),
            ]
        )
        for category, description, amount in monthly:
            finance.record_expense(
                self.school,
                amount=Decimal(amount if self.spec.currency != "USD" else amount // 150),
                category=category,
                method="bank_transfer",
                description=description,
                expense_date=self.today - timedelta(days=2),
            )
        # An overpayment kept as credit, partly refunded; a payment typed twice, then reversed.
        # A newcomer (no arrears from last year), so the extra money stays as credit.
        newcomers = set(
            Enrollment.objects.filter(academic_year=year, kind=Enrollment.Kind.NEW).values_list(
                "student_id", flat=True
            )
        )
        rich = next((s for s in students if s.pk not in paid and s.pk in newcomers), students[0])
        invoice = Invoice.objects.filter(student=rich, academic_year=year, status="issued").first()
        if invoice is not None:
            over = self.pay(
                rich,
                invoice.total + Decimal(200_000 if self.fr else 20),
                self.today - timedelta(days=4),
                method="mobile_money",
            )
            credit = student_credit(rich) if over is not None else Decimal(0)
            if credit > 0:
                finance.record_refund(
                    rich,
                    amount=min(credit, Decimal(100_000 if self.fr else 10)),
                    method="mobile_money",
                    reason="Trop-perçu remboursé à la mère"
                    if self.fr
                    else "Overpayment returned to the mother",
                    refund_date=self.today - timedelta(days=1),
                )
                self.note("credit and refunds")
        twice = next((s for s in students if s.pk in paid), None)
        if twice is not None:
            duplicate = self.pay(
                twice,
                Decimal(100_000 if self.fr else 10),
                self.today - timedelta(days=2),
                method="mobile_money",
            )
            if duplicate is not None:
                finance.reverse_payment(duplicate, reason="Saisie en double" if self.fr else "Entered twice")
                self.note("reversed payments")
        # A uniform invoice issued by mistake, then cancelled and issued again.
        # (someone with nothing paid, so no credit settles the invoice before it is cancelled)
        target = next(s for s in reversed(students) if s.pk not in paid and s != rich)
        lines = [
            {
                "category": self.categories["uniform"],
                "description": "Tenue scolaire (2 ensembles)" if self.fr else "Uniform (2 sets)",
                "due_date": self.today + timedelta(days=14),
                "amount": Decimal(250_000 if self.fr else 25),
                "discount": Decimal(0),
            }
        ]
        wrong = finance.create_manual_invoice(target, academic_year=year, lines=lines, notes="")
        finance.cancel_invoice(wrong, reason="Mauvais élève" if self.fr else "Wrong student")
        finance.create_manual_invoice(students[-2], academic_year=year, lines=lines, notes="")
        self.note("cancelled invoices")

    # --- Grades -------------------------------------------------------------------------------------------

    STYLES = ["classic", "weighted", "points", "zero"]

    def rules(
        self, gradebook: Gradebook, style: str, scale_max: int
    ) -> list[tuple[GradeCategory, list[tuple[str, int]]]]:
        school = self.school
        if style == "classic":  # (class work + 2 × composition) / 3
            work = GradeCategory.objects.create(
                school=school, gradebook=gradebook, name="Interrogations" if self.fr else "Quizzes", weight=1
            )
            exam = GradeCategory.objects.create(
                school=school,
                gradebook=gradebook,
                name="Composition" if self.fr else "Exam",
                weight=2,
                order=1,
            )
            return [
                (
                    work,
                    [("Interro 1", 10), ("Interro 2", 10), ("Devoir surveillé" if self.fr else "Test", 20)],
                ),
                (exam, [("Composition" if self.fr else "Term exam", 20)]),
            ]
        if style == "weighted":  # Liberian-style percentages
            parts = [
                ("Quizzes", 20, [("Quiz 1", 10), ("Quiz 2", 10)]),
                ("Assignments", 20, [("Homework 1", 20), ("Project", 50)]),
                ("Tests", 20, [("Unit test", 50)]),
                ("Exam", 40, [("Period exam", 100)]),
            ]
            if self.fr:
                parts = [
                    ("Devoirs", 20, [("Devoir maison 1", 20), ("Exposé", 20)]),
                    ("Interrogations", 30, [("Interro 1", 10), ("Interro 2", 10)]),
                    ("Composition", 50, [("Composition", 40)]),
                ]
            return [
                (
                    GradeCategory.objects.create(
                        school=school, gradebook=gradebook, name=name, weight=weight, order=i
                    ),
                    items,
                )
                for i, (name, weight, items) in enumerate(parts)
            ]
        if style == "points":
            total = GradeCategory.objects.create(
                school=school, gradebook=gradebook, name="Points", weight=1, method="total"
            )
            return [(total, [("Test 1", 25), ("Test 2", 25), ("Examen" if self.fr else "Exam", 50)])]
        gradebook.missing_policy = Gradebook.MissingPolicy.ZERO
        gradebook.save(update_fields=["missing_policy"])
        work = GradeCategory.objects.create(
            school=school, gradebook=gradebook, name="Travaux" if self.fr else "Class work", weight=1
        )
        return [
            (
                work,
                [
                    ("TP 1" if self.fr else "Lab 1", 20),
                    ("TP 2" if self.fr else "Lab 2", 20),
                    ("TP 3" if self.fr else "Lab 3", 20),
                ],
            )
        ]

    def pass_offset(self, class_group: ClassGroup) -> float:
        primary = class_group.level.cycle == "primary" and self.spec.primary_scale
        scale = self.spec.primary_scale if primary else self.spec.scale
        if not scale:
            return 0.0
        return float(scale["pass_mark"]) / float(scale["max_mark"]) - 0.5

    def score(self, student_id: int, max_score: int, subject_bias: float) -> Decimal | None:
        ability = self.ability.get(student_id, 0.55) + subject_bias + self.rnd.gauss(0, 0.11)
        step = Decimal("0.5") if max_score <= 20 else Decimal("1")
        raw = Decimal(str(max(0.0, min(1.0, ability)) * max_score))
        return (raw / step).quantize(Decimal("1")) * step

    def grades_for_year(self, year: AcademicYear, classes: list[ClassGroup], *, complete: bool) -> None:
        terms = list(year.terms.order_by("order"))
        today = self.today
        for class_group in classes:
            enrollments = list(Enrollment.objects.filter(class_group=class_group).exclude(status="cancelled"))
            if not enrollments:
                continue
            for cs_index, class_subject in enumerate(
                ClassSubject.objects.filter(class_group=class_group).select_related("subject")
            ):
                # Marks sit around each school's own pass mark (70/100 in Liberia, 10/20 in Guinea).
                bias = self.rnd.gauss(0, 0.06) + self.pass_offset(class_group)
                style = self.STYLES[(cs_index + class_group.pk) % len(self.STYLES)]
                for term in terms:
                    if not complete and term.start_date > today:
                        continue
                    gradebook, _ = Gradebook.objects.get_or_create(
                        school=self.school, class_subject=class_subject, term=term
                    )
                    if not complete and self.rnd.random() < 0.25:
                        continue  # this teacher has not started yet
                    plan = self.rules(gradebook, style, 20)
                    grades = []
                    order = 0
                    # This year only the first weeks have happened: quizzes and tests so far, no exam yet.
                    last_day = term.end_date if complete else min(term.end_date, today - timedelta(days=1))
                    span = (last_day - term.start_date).days
                    if not complete and len(plan) > 1:
                        plan = plan[:-1]
                    for category, items in plan:
                        given = items if complete else items[: max(1, len(items) - 1)]
                        for position, (name, max_score) in enumerate(given):
                            day = term.start_date + timedelta(
                                days=int(span * (position + 1) / (len(given) + 1))
                            )
                            order += 1
                            assessment = Assessment.objects.create(
                                school=self.school,
                                gradebook=gradebook,
                                category=category,
                                name=name,
                                max_score=max_score,
                                date=day,
                            )
                            for enrollment in enrollments:
                                if enrollment.enrollment_date > day or (
                                    enrollment.ended_on and enrollment.ended_on < day
                                ):
                                    continue
                                roll = self.rnd.random()
                                if roll < 0.02:
                                    grades.append(
                                        Grade(
                                            school=self.school,
                                            assessment=assessment,
                                            enrollment=enrollment,
                                            excused=True,
                                            comment="Malade" if self.fr else "Sick",
                                        )
                                    )
                                elif roll < 0.05:
                                    continue  # missing mark
                                else:
                                    grades.append(
                                        Grade(
                                            school=self.school,
                                            assessment=assessment,
                                            enrollment=enrollment,
                                            score=self.score(enrollment.student_id, max_score, bias),
                                        )
                                    )
                    Grade.objects.bulk_create(grades, batch_size=500)
                    self.note("grades", len(grades))
                    if complete:
                        gradebook.status = Gradebook.Status.PUBLISHED
                        gradebook.published_at = aware(term.end_date, 16)
                    elif order:
                        roll = self.rnd.random()
                        if roll < 0.15:
                            gradebook.status = Gradebook.Status.SUBMITTED
                            gradebook.submitted_at = timezone.now() - timedelta(days=1)
                        elif roll < 0.22:
                            gradebook.status_note = (
                                "Il manque les notes de l'interrogation 2 pour trois élèves."
                                if self.fr
                                else "Quiz 2 marks are missing for three students."
                            )
                        elif roll < 0.32:
                            gradebook.status = Gradebook.Status.PUBLISHED
                            gradebook.published_at = timezone.now() - timedelta(hours=5)
                    gradebook.save()
            self.note("gradebooks", Gradebook.objects.filter(class_subject__class_group=class_group).count())

    def history_comments(self, year: AcademicYear, classes: list[ClassGroup]) -> None:
        comments = (
            [
                "Excellent travail, félicitations.",
                "Bon trimestre, continuez ainsi.",
                "Des efforts à fournir en mathématiques.",
                "Travail irrégulier, peut mieux faire.",
                "Élève sérieux et appliqué.",
                "Trop d'absences ce trimestre.",
            ]
            if self.fr
            else [
                "Excellent work, keep it up.",
                "Good progress this term.",
                "Needs to work harder in Mathematics.",
                "Inconsistent effort, can do better.",
                "Serious and hard-working student.",
                "Too many absences this term.",
            ]
        )
        terms = list(year.terms.order_by("order"))
        rows = []
        for class_group in classes:
            for enrollment in Enrollment.objects.filter(class_group=class_group):
                for term in [*terms, None]:
                    if self.rnd.random() < 0.7:
                        rows.append(
                            ReportComment(
                                school=self.school,
                                enrollment=enrollment,
                                term=term,
                                comment=self.rnd.choice(comments),
                            )
                        )
        ReportComment.objects.bulk_create(rows, batch_size=500)

    # --- Attendance ---------------------------------------------------------------------------------------

    def attendance(self, year: AcademicYear, classes: list[ClassGroup], start: date, end: date) -> None:
        days = school_days(max(start, year.start_date), min(end, year.end_date, self.today))
        for class_group in classes:
            enrollments = list(Enrollment.objects.filter(class_group=class_group).exclude(status="cancelled"))
            if not enrollments:
                continue
            absentee = enrollments[len(enrollments) // 2].pk
            late_one = enrollments[len(enrollments) // 3].pk
            taker = (
                class_group.class_teacher.user
                if class_group.class_teacher and class_group.class_teacher.user
                else None
            )
            for day in days:
                if self.rnd.random() < 0.06:
                    continue  # the register was not taken that day
                register = ClassRegister.objects.create(
                    school=self.school, class_group=class_group, date=day, created_by=taker
                )
                ClassRegister.objects.filter(pk=register.pk).update(
                    created_at=aware(day, 8, self.rnd.randint(0, 40))
                )
                records = []
                for enrollment in enrollments:
                    if enrollment.enrollment_date > day or (
                        enrollment.ended_on and enrollment.ended_on < day
                    ):
                        continue
                    if (
                        enrollment.status == "class_changed"
                        and enrollment.ended_on
                        and enrollment.ended_on < day
                    ):
                        continue
                    roll = self.rnd.random()
                    if enrollment.pk == absentee:
                        status = "absent" if roll < 0.35 else "present"
                    elif enrollment.pk == late_one:
                        status = "late" if roll < 0.4 else "present"
                    else:
                        status = (
                            "absent"
                            if roll < 0.04
                            else "late"
                            if roll < 0.08
                            else "excused"
                            if roll < 0.095
                            else "present"
                        )
                    records.append(
                        AttendanceRecord(
                            school=self.school,
                            register=register,
                            enrollment=enrollment,
                            status=status,
                            minutes_late=self.rnd.choice([5, 10, 15, 20, 30, 45])
                            if status == "late"
                            else None,
                            note=(
                                self.rnd.choice(
                                    ["Malade", "Funérailles", "Rendez-vous médical"]
                                    if self.fr
                                    else ["Sick", "Funeral", "Hospital visit"]
                                )
                                if status == "excused"
                                else ""
                            ),
                        )
                    )
                AttendanceRecord.objects.bulk_create(records, batch_size=500)
                self.note("attendance records", len(records))

    def staff_attendance(self, year: AcademicYear) -> None:
        staff = list(StaffMember.objects.filter(school=self.school, status="active"))
        on_leave = staff[2].pk if len(staff) > 2 else None
        rows = []
        for day in school_days(
            max(year.start_date, self.today - timedelta(days=14)), self.today - timedelta(days=1)
        ):
            for person in staff:
                if person.employment_date and person.employment_date > day:
                    continue
                roll = self.rnd.random()
                status = (
                    "leave"
                    if person.pk == on_leave
                    else "absent"
                    if roll < 0.03
                    else "late"
                    if roll < 0.08
                    else "present"
                )
                rows.append(
                    StaffAttendance(
                        school=self.school,
                        staff=person,
                        date=day,
                        status=status,
                        minutes_late=self.rnd.choice([10, 20, 35]) if status == "late" else None,
                        note=("Congé de maternité" if self.fr else "Maternity leave")
                        if status == "leave"
                        else "",
                    )
                )
        StaffAttendance.objects.bulk_create(rows, batch_size=500)

    # --- A school registered yesterday ---------------------------------------------------------------------

    def build_fresh(self) -> None:
        year = create_academic_year(
            self.school,
            name=f"{self.current_start.year}-{self.current_start.year + 1}",
            start_date=self.current_start,
            end_date=self.current_end,
            term_count=self.spec.terms,
        )
        self.levels = []
        for name, order, cycle, age, _ in self.spec.levels:
            self.levels.append(
                (Level.objects.create(school=self.school, name=name, order=order, cycle=cycle), age, 1)
            )
        director = create_staff(
            self.school,
            data={
                "first_name": "Thierno",
                "last_name": "Diallo",
                "staff_type": "administrative",
                "position": "Directeur",
            },
        )
        teacher = create_staff(
            self.school,
            data={"first_name": "Aminata", "last_name": "Barry", "gender": "F", "staff_type": "teacher"},
        )
        maths = Subject.objects.create(
            school=self.school, name="Mathématiques", code="MATH", default_coefficient=4
        )
        first = ClassGroup.objects.create(
            school=self.school,
            academic_year=year,
            level=self.levels[0][0],
            name="7ème",
            capacity=40,
            class_teacher=teacher,
        )
        ClassGroup.objects.create(
            school=self.school, academic_year=year, level=self.levels[1][0], name="8ème", capacity=40
        )
        ClassSubject.objects.create(
            school=self.school, class_group=first, subject=maths, teacher=teacher, coefficient=4
        )
        for _ in range(self.spec.per_class):
            student = self.new_student(12, year)
            enrolments.enroll(student, first, enrollment_date=self.today - timedelta(days=1))
        self.note("staff", 2 if director else 0)


@transaction.atomic
def build_school(spec: Spec, out) -> dict[str, int]:
    builder = Builder(spec, out)
    builder.build()
    return builder.counts
