"""Realistic demo content for local development: school year, levels, classes, subjects, staff, students."""

import random
from datetime import date, timedelta

from django.contrib.auth import get_user_model

from apps.academics.models import AcademicYear, ClassGroup, ClassSubject, Level, Subject
from apps.academics.services import create_academic_year
from apps.enrollments.services import enroll
from apps.people.services import create_staff, create_student, link_guardian

User = get_user_model()

FR = {
    "year": ("2026-2027", date(2026, 10, 1), date(2027, 6, 30), 3),
    "levels": [
        ("7ème année", 7, "lower_secondary", 12),
        ("8ème année", 8, "lower_secondary", 13),
        ("9ème année", 9, "lower_secondary", 14),
        ("10ème année", 10, "lower_secondary", 15),
        ("11ème année", 11, "upper_secondary", 16),
    ],
    "class_name": lambda level, letter: f"{level.split()[0]} {letter}",
    "subjects": [
        ("Mathématiques", "MATH", 4),
        ("Français", "FR", 4),
        ("Anglais", "ANG", 2),
        ("Histoire-Géographie", "HG", 2),
        ("Sciences de la vie et de la Terre", "SVT", 2),
        ("Physique-Chimie", "PC", 3),
        ("Éducation civique et morale", "ECM", 1),
        ("Éducation physique et sportive", "EPS", 1),
    ],
    "teachers": [
        ("Mamadou", "Barry", "M", "MATH"),
        ("Mariama", "Condé", "F", "FR"),
        ("Thierno", "Sylla", "M", "ANG"),
        ("Kadiatou", "Bangoura", "F", "HG"),
        ("Ousmane", "Touré", "M", "SVT"),
        ("Aboubacar", "Kouyaté", "M", "PC"),
        ("Hawa", "Cissé", "F", "ECM"),
        ("Lansana", "Soumah", "M", "EPS"),
        ("Fodé", "Kaba", "M", "MATH"),
        ("Djénabou", "Baldé", "F", "FR"),
    ],
    "teacher_user": "enseignant@monecole.test",
    "admin_staff": [
        ("Fatoumata", "Bah", "administrative", "Secrétaire"),
        ("Ibrahima", "Sow", "administrative", "Comptable"),
        ("Sékou", "Camara", "support", "Gardien"),
    ],
    "first_m": [
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
        "Moussa",
        "Boubacar",
        "Amadou",
        "Souleymane",
        "Ismaël",
        "Karim",
    ],
    "first_f": [
        "Aïssatou",
        "Mariama",
        "Fatoumata",
        "Kadiatou",
        "Hawa",
        "Aminata",
        "Djénabou",
        "Oumou",
        "Fanta",
        "Nènè",
        "Binta",
        "Ramatoulaye",
        "Mafoudia",
        "Sayon",
        "Awa",
        "Salématou",
    ],
    "last": [
        "Diallo",
        "Barry",
        "Bah",
        "Camara",
        "Sow",
        "Condé",
        "Keita",
        "Touré",
        "Sylla",
        "Kouyaté",
        "Bangoura",
        "Cissé",
        "Soumah",
        "Kaba",
        "Baldé",
        "Traoré",
        "Doumbouya",
        "Fofana",
        "Kourouma",
    ],
    "places": ["Conakry", "Kindia", "Labé", "Kankan", "Mamou", "Boké", "Nzérékoré", "Faranah"],
    "nationality": "Guinéenne",
    "occupations": [
        "Commerçant(e)",
        "Enseignant(e)",
        "Fonctionnaire",
        "Infirmier(ère)",
        "Chauffeur",
        "Couturière",
        "Agriculteur",
        "Mécanicien",
        "Comptable",
        "Ménagère",
    ],
    "phone": lambda rnd: (
        f"+224 6{rnd.randint(20, 69)} {rnd.randint(10, 99)} {rnd.randint(10, 99)} {rnd.randint(10, 99)}"
    ),
    "rooms": "Salle",
    "per_class": 22,
}

EN = {
    "year": ("2026-2027", date(2026, 9, 7), date(2027, 7, 16), 2),
    "levels": [
        ("Grade 7", 7, "lower_secondary", 12),
        ("Grade 8", 8, "lower_secondary", 13),
        ("Grade 9", 9, "lower_secondary", 14),
    ],
    "class_name": lambda level, letter: f"{level}{letter}",
    "subjects": [
        ("English", "ENG", 3),
        ("Mathematics", "MATHS", 3),
        ("General Science", "SCI", 2),
        ("Social Studies", "SOC", 2),
        ("French", "FRE", 1),
        ("Physical Education", "PE", 1),
    ],
    "teachers": [
        ("James", "Kollie", "M", "ENG"),
        ("Grace", "Tarr", "F", "MATHS"),
        ("Emmanuel", "Doe", "M", "SCI"),
        ("Comfort", "Weah", "F", "SOC"),
        ("Joseph", "Sirleaf", "M", "FRE"),
        ("Patience", "Kamara", "F", "PE"),
    ],
    "teacher_user": "teacher@monecole.test",
    "admin_staff": [("Mary", "Johnson", "administrative", "Registrar")],
    "first_m": [
        "James",
        "Joseph",
        "Emmanuel",
        "Samuel",
        "Moses",
        "Prince",
        "Varney",
        "Momo",
        "Abraham",
        "Daniel",
        "Musa",
        "Jerome",
    ],
    "first_f": [
        "Grace",
        "Comfort",
        "Patience",
        "Mercy",
        "Esther",
        "Fatu",
        "Oretha",
        "Musu",
        "Hawa",
        "Martha",
        "Precious",
        "Blessing",
    ],
    "last": [
        "Kollie",
        "Tarr",
        "Doe",
        "Weah",
        "Johnson",
        "Kamara",
        "Sirleaf",
        "Taylor",
        "Flomo",
        "Kpoto",
        "Gbowee",
        "Cooper",
        "Dennis",
        "Toe",
        "Sackor",
    ],
    "places": ["Monrovia", "Gbarnga", "Buchanan", "Kakata", "Harper", "Voinjama"],
    "nationality": "Liberian",
    "occupations": [
        "Trader",
        "Teacher",
        "Civil servant",
        "Nurse",
        "Driver",
        "Farmer",
        "Mechanic",
        "Accountant",
    ],
    "phone": lambda rnd: f"+231 77{rnd.randint(1, 9)} {rnd.randint(100, 999)} {rnd.randint(100, 999)}",
    "rooms": "Room",
    "per_class": 18,
}


def seed_school_data(school, out) -> None:
    if AcademicYear.objects.filter(school=school).exists():
        return
    data = FR if school.default_language == "fr" else EN
    rnd = random.Random(f"monecole-{school.code}")  # noqa: S311 - reproducible demo data, not security

    name, start, end, terms = data["year"]
    year = create_academic_year(school, name=name, start_date=start, end_date=end, term_count=terms)

    subjects = {
        code: Subject.objects.create(school=school, name=label, code=code, default_coefficient=coef)
        for label, code, coef in data["subjects"]
    }

    teachers: dict[str, list] = {}
    teacher_user = User.objects.filter(email=data["teacher_user"]).first()
    for index, (first, last, gender, subject_code) in enumerate(data["teachers"]):
        staff = create_staff(
            school,
            data={
                "first_name": first,
                "last_name": last,
                "gender": gender,
                "staff_type": "teacher",
                "position": subjects[subject_code].name,
                "specialization": subjects[subject_code].name,
                "qualification": rnd.choice(
                    ["Licence", "Master", "DEUG"]
                    if school.default_language == "fr"
                    else ["B.Sc.", "B.Ed.", "M.A."]
                ),
                "phone": data["phone"](rnd),
                "employment_date": date(rnd.randint(2008, 2024), rnd.randint(1, 12), 1),
            },
        )
        if teacher_user and first == teacher_user.first_name and last == teacher_user.last_name:
            staff.user = teacher_user
            staff.email = teacher_user.email
            staff.save()
        teachers.setdefault(subject_code, []).append(staff)
        if index == 0:
            first_teacher = staff
    for first, last, staff_type, position in data["admin_staff"]:
        create_staff(
            school,
            data={
                "first_name": first,
                "last_name": last,
                "staff_type": staff_type,
                "position": position,
                "phone": data["phone"](rnd),
            },
        )

    classes = []
    for level_name, order, cycle, age in data["levels"]:
        level = Level.objects.create(school=school, name=level_name, order=order, cycle=cycle)
        for letter in "AB":
            classes.append(
                (
                    ClassGroup.objects.create(
                        school=school,
                        academic_year=year,
                        level=level,
                        name=data["class_name"](level_name, letter),
                        room=f"{data['rooms']} {order}{letter}",
                        capacity=45,
                    ),
                    age,
                )
            )

    for class_index, (class_group, _) in enumerate(classes):
        for code, subject in subjects.items():
            pool = teachers.get(code) or [first_teacher]
            ClassSubject.objects.create(
                school=school,
                class_group=class_group,
                subject=subject,
                teacher=pool[class_index % len(pool)],
                coefficient=subject.default_coefficient,
            )
        homeroom = [t for group in teachers.values() for t in group]
        class_group.class_teacher = homeroom[class_index % len(homeroom)]
        class_group.save(update_fields=["class_teacher"])

    # The teacher demo account leads the first class so the "assigned classes only" rule is visible.
    linked = next((t for group in teachers.values() for t in group if t.user_id), None)
    if linked:
        classes[0][0].class_teacher = linked
        classes[0][0].save(update_fields=["class_teacher"])

    families: list = []
    count = 0
    for class_group, age in classes:
        for _ in range(data["per_class"]):
            gender = rnd.choice("MF")
            if families and rnd.random() < 0.15:
                last, guardians = rnd.choice(families)
            else:
                last, guardians = rnd.choice(data["last"]), None
            student = create_student(
                school,
                data={
                    "first_name": rnd.choice(data["first_m"] if gender == "M" else data["first_f"]),
                    "last_name": last,
                    "gender": gender,
                    "date_of_birth": date(year.start_date.year - age, 1, 1)
                    + timedelta(days=rnd.randint(0, 364)),
                    "place_of_birth": rnd.choice(data["places"]),
                    "nationality": data["nationality"],
                },
            )
            if guardians:
                for guardian, relationship in guardians:
                    link_guardian(student, guardian=guardian, relationship=relationship)
            else:
                links = []
                mother_last = rnd.choice(data["last"])
                links.append(
                    link_guardian(
                        student,
                        guardian_data={
                            "first_name": rnd.choice(data["first_f"]),
                            "last_name": mother_last,
                            "phone": data["phone"](rnd),
                            "occupation": rnd.choice(data["occupations"]),
                        },
                        relationship="mother",
                        is_primary=True,
                    )
                )
                if rnd.random() < 0.7:
                    links.append(
                        link_guardian(
                            student,
                            guardian_data={
                                "first_name": rnd.choice(data["first_m"]),
                                "last_name": last,
                                "phone": data["phone"](rnd),
                                "occupation": rnd.choice(data["occupations"]),
                            },
                            relationship="father",
                        )
                    )
                families.append((last, [(link.guardian, link.relationship) for link in links]))
            enroll(
                student,
                class_group,
                enrollment_date=year.start_date - timedelta(days=rnd.randint(1, 40)),
                previous_school="",
            )
            count += 1
    out.write(f"  {school.name}: {len(classes)} classes, {len(subjects)} subjects, {count} students")
