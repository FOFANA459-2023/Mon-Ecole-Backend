"""Shared fixtures: 7ème A with three students, two teachers and Mr Barry's marked maths gradebook."""

from datetime import date

import pytest

from apps.academics.models import ClassSubject, Subject
from apps.assessments.models import Gradebook
from apps.enrollments import services as enrollment_services

URL = "/api/v1/gradebooks/"


@pytest.fixture
def term(year):
    return year.terms.order_by("order").first()


@pytest.fixture
def setup(school, make_class, make_student, make_staff, make_member):
    """7ème A with three students; Mr Barry teaches maths (coef 4), Mrs Camara French (coef 3) and leads the
    class. A third teacher teaches neither."""
    class_group = make_class()
    maths = Subject.objects.create(school=school, name="Mathématiques", code="MATH")
    french = Subject.objects.create(school=school, name="Français", code="FR")
    barry_user = make_member(school, "teacher", email="barry@test.local")
    camara_user = make_member(school, "teacher", email="camara@test.local")
    other_user = make_member(school, "teacher", email="other@test.local")
    barry = make_staff("Mamadou", "Barry", user=barry_user)
    camara = make_staff("Fatou", "Camara", user=camara_user)
    make_staff("Ibrahima", "Sow", user=other_user)
    class_group.class_teacher = camara
    class_group.save()
    cs_maths = ClassSubject.objects.create(
        school=school, class_group=class_group, subject=maths, teacher=barry, coefficient=4
    )
    cs_french = ClassSubject.objects.create(
        school=school, class_group=class_group, subject=french, teacher=camara, coefficient=3
    )
    students = [
        enrollment_services.enroll(make_student(first, last), class_group, enrollment_date=date(2026, 9, 2))
        for first, last in [("Awa", "Diallo"), ("Binta", "Bah"), ("Moussa", "Condé")]
    ]
    return {
        "class": class_group,
        "maths": cs_maths,
        "french": cs_french,
        "barry": barry_user,
        "camara": camara_user,
        "other": other_user,
        "enrollments": {e.student.first_name: e for e in students},
    }


def gradebook_of(class_subject, term):
    return Gradebook.objects.get(class_subject=class_subject, term=term)


def list_gradebooks(client, term, **params):
    response = client.get(URL, {"term": term.pk, **params})
    assert response.status_code == 200, response.data
    return response.data["results"]


def build_maths(client, gradebook):
    """Mr Barry's own rules: "Interrogations" ×1 (average) and "Composition" ×2, three assessments."""
    interros = client.post(
        "/api/v1/grade-categories/", {"gradebook": gradebook.pk, "name": "Interrogations", "weight": "1"}
    ).data
    compo = client.post(
        "/api/v1/grade-categories/", {"gradebook": gradebook.pk, "name": "Composition", "weight": "2"}
    ).data
    items = {}
    for name, category, max_score in [
        ("Interro 1 — fractions", interros, "10"),
        ("Interro surprise", interros, "20"),
        ("Composition du 1er trimestre", compo, "40"),
    ]:
        response = client.post(
            "/api/v1/assessments/",
            {"gradebook": gradebook.pk, "category": category["id"], "name": name, "max_score": max_score},
        )
        assert response.status_code == 201, response.data
        items[name] = response.data["id"]
    return items


def save(client, gradebook, rows):
    return client.post(f"{URL}{gradebook.pk}/grades/", {"grades": rows}, format="json")


@pytest.fixture
def maths_marked(setup, term, client_for):
    """Maths marked: Awa 8/10, 15/20, 30/40; Binta 5/10, 10/20, 24/40; Moussa excused on the surprise test."""
    client = client_for(setup["barry"], setup["class"].school)
    list_gradebooks(client, term)
    gradebook = gradebook_of(setup["maths"], term)
    items = build_maths(client, gradebook)
    e = setup["enrollments"]
    i1, i2, compo = items.values()
    rows = [
        {"assessment": i1, "enrollment": e["Awa"].pk, "score": "8"},
        {"assessment": i2, "enrollment": e["Awa"].pk, "score": "15"},
        {"assessment": compo, "enrollment": e["Awa"].pk, "score": "30"},
        {"assessment": i1, "enrollment": e["Binta"].pk, "score": "5"},
        {"assessment": i2, "enrollment": e["Binta"].pk, "score": "10"},
        {"assessment": compo, "enrollment": e["Binta"].pk, "score": "24"},
        {"assessment": i1, "enrollment": e["Moussa"].pk, "score": "6"},
        {"assessment": i2, "enrollment": e["Moussa"].pk, "score": None, "excused": True},
        {"assessment": compo, "enrollment": e["Moussa"].pk, "score": "36"},
    ]
    response = save(client, gradebook, rows)
    assert response.status_code == 200, response.data
    assert response.data == {"changed": 9}
    return {"client": client, "gradebook": gradebook, "items": items}
