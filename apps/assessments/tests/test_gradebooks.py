from datetime import date
from decimal import Decimal

import pytest

from apps.academics.models import ClassSubject, Subject
from apps.assessments.models import Assessment, Grade, Gradebook, GradingScale
from apps.audit.models import AuditLog
from apps.enrollments import services as enrollment_services

pytestmark = pytest.mark.django_db

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


class TestGradebookList:
    def test_listing_a_term_creates_the_gradebooks_and_a_teacher_sees_only_theirs(
        self, setup, term, client_for
    ):
        rows = list_gradebooks(client_for(setup["barry"], setup["class"].school), term)
        assert [(r["subject_name"], r["is_mine"], r["student_count"]) for r in rows] == [
            ("Mathématiques", True, 3)
        ]
        assert Gradebook.objects.filter(term=term).count() == 2

    def test_the_class_teacher_sees_every_subject_of_their_class(self, setup, term, client_for):
        client = client_for(setup["camara"], setup["class"].school)
        assert {r["subject_name"] for r in list_gradebooks(client, term)} == {"Mathématiques", "Français"}
        assert [r["subject_name"] for r in list_gradebooks(client, term, mine="1")] == ["Français"]

    def test_a_teacher_of_other_classes_sees_nothing(self, setup, term, client_for):
        assert list_gradebooks(client_for(setup["other"], setup["class"].school), term) == []

    def test_a_term_is_required(self, setup, client_for):
        response = client_for(setup["barry"], setup["class"].school).get(URL)
        assert response.status_code == 400

    def test_staff_without_grade_rights_are_refused(self, school, setup, term, make_member, client_for):
        response = client_for(make_member(school, "accountant"), school).get(URL, {"term": term.pk})
        assert response.status_code == 403


class TestTeachersOwnRules:
    def test_teacher_builds_rules_with_their_own_names_and_marks_the_class(self, setup, term, maths_marked):
        client, gradebook = maths_marked["client"], maths_marked["gradebook"]
        detail = client.get(f"{URL}{gradebook.pk}/").data
        assert [c["name"] for c in detail["categories"]] == ["Interrogations", "Composition"]
        assert [a["name"] for a in detail["assessments"]] == [
            "Interro 1 — fractions",
            "Interro surprise",
            "Composition du 1er trimestre",
        ]
        assert detail["can"] == {
            "edit": True,
            "submit": True,
            "send_back": False,
            "publish": False,
            "reopen": False,
        }
        assert detail["scale"]["max_mark"] == "20.00"

        sheet = client.get(f"{URL}{gradebook.pk}/sheet/").data
        by_name = {row["student_name"]: row for row in sheet["students"]}
        # Awa: interros (80 % + 75 %) / 2 = 77.5 %, compo 75 % → (77.5 + 2 × 75) / 3 = 75.83 % → 15.17 / 20.
        assert Decimal(by_name["Awa Diallo"]["mark"]) == Decimal("15.17")
        # Binta: interros 50 %, compo 60 % → (50 + 120) / 3 = 56.67 % → 11.33.
        assert Decimal(by_name["Binta Bah"]["mark"]) == Decimal("11.33")
        # Moussa is excused from the surprise test: interros 60 %, compo 90 % → 80 % → 16.00.
        assert Decimal(by_name["Moussa Condé"]["mark"]) == Decimal("16.00")
        assert [by_name[n]["rank"] for n in ("Moussa Condé", "Awa Diallo", "Binta Bah")] == [1, 2, 3]
        categories = {c["category"]: c["mark"] for c in by_name["Moussa Condé"]["categories"]}
        assert sorted(Decimal(m) for m in categories.values()) == [Decimal("12.00"), Decimal("18.00")]
        assert sheet["stats"]["counted"] == 3 and sheet["stats"]["passed"] == 3

    def test_marks_are_audited_and_can_be_changed_or_cleared(self, setup, maths_marked):
        client, gradebook, items = maths_marked["client"], maths_marked["gradebook"], maths_marked["items"]
        awa = setup["enrollments"]["Awa"].pk
        i1 = items["Interro 1 — fractions"]
        response = save(
            client,
            gradebook,
            [
                {"assessment": i1, "enrollment": awa, "score": "9"},
                {"assessment": items["Interro surprise"], "enrollment": awa, "score": None},
            ],
        )
        assert response.data == {"changed": 2}
        assert Grade.objects.get(assessment_id=i1, enrollment_id=awa).score == Decimal("9")
        assert not Grade.objects.filter(assessment_id=items["Interro surprise"], enrollment_id=awa).exists()
        entry = AuditLog.objects.filter(module="grades", entity_id=str(gradebook.pk)).latest("id")
        assert entry.old_values[f"{i1}:{awa}"] == "8.00"
        assert entry.new_values[f"{i1}:{awa}"] == "9.00"
        # Saving the same values again changes nothing.
        assert save(client, gradebook, [{"assessment": i1, "enrollment": awa, "score": "9"}]).data == {
            "changed": 0
        }

    def test_marks_are_checked(self, setup, school, term, maths_marked, make_class, make_student):
        client, gradebook, items = maths_marked["client"], maths_marked["gradebook"], maths_marked["items"]
        awa = setup["enrollments"]["Awa"].pk
        too_high = save(
            client,
            gradebook,
            [{"assessment": items["Interro 1 — fractions"], "enrollment": awa, "score": "11"}],
        )
        assert too_high.status_code == 400
        elsewhere = enrollment_services.enroll(make_student("Kadi", "Touré"), make_class("7ème B"))
        response = save(
            client,
            gradebook,
            [{"assessment": items["Interro 1 — fractions"], "enrollment": elsewhere.pk, "score": "5"}],
        )
        assert response.status_code == 400

    def test_missing_marks_can_count_as_zero(self, setup, maths_marked):
        client, gradebook, items = maths_marked["client"], maths_marked["gradebook"], maths_marked["items"]
        binta = setup["enrollments"]["Binta"].pk
        save(
            client, gradebook, [{"assessment": items["Interro surprise"], "enrollment": binta, "score": None}]
        )
        sheet = client.get(f"{URL}{gradebook.pk}/sheet/").data
        assert next(r for r in sheet["students"] if r["enrollment"] == binta)["missing"] == 1
        response = client.patch(f"{URL}{gradebook.pk}/", {"missing_policy": "zero"})
        assert response.status_code == 200 and response.data["missing_policy"] == "zero"
        sheet = client.get(f"{URL}{gradebook.pk}/sheet/").data
        # Interros (50 % + 0) / 2 = 25 %, compo 60 % → (25 + 120) / 3 = 48.33 % → 9.67.
        assert Decimal(next(r for r in sheet["students"] if r["enrollment"] == binta)["mark"]) == Decimal(
            "9.67"
        )

    def test_an_assessment_cannot_be_marked_out_of_less_than_a_score_it_has(self, maths_marked):
        client, items = maths_marked["client"], maths_marked["items"]
        response = client.patch(
            f"/api/v1/assessments/{items['Composition du 1er trimestre']}/", {"max_score": "20"}
        )
        assert response.status_code == 400
        response = client.patch(
            f"/api/v1/assessments/{items['Composition du 1er trimestre']}/",
            {"max_score": "50", "name": "Compo"},
        )
        assert response.status_code == 200 and response.data["name"] == "Compo"

    def test_category_names_are_unique_and_used_categories_stay(self, maths_marked):
        client, gradebook = maths_marked["client"], maths_marked["gradebook"]
        response = client.post(
            "/api/v1/grade-categories/", {"gradebook": gradebook.pk, "name": "composition"}
        )
        assert response.status_code == 400
        used = gradebook.categories.get(name="Composition")
        assert client.delete(f"/api/v1/grade-categories/{used.pk}/").status_code == 400
        spare = client.post(
            "/api/v1/grade-categories/", {"gradebook": gradebook.pk, "name": "Devoirs maison"}
        ).data
        assert client.delete(f"/api/v1/grade-categories/{spare['id']}/").status_code == 204

    def test_deleting_an_assessment_deletes_its_marks(self, maths_marked):
        client, items = maths_marked["client"], maths_marked["items"]
        assert client.delete(f"/api/v1/assessments/{items['Interro surprise']}/").status_code == 204
        assert not Grade.objects.filter(assessment_id=items["Interro surprise"]).exists()

    def test_a_category_of_another_gradebook_is_refused(self, setup, term, maths_marked, client_for):
        camara = client_for(setup["camara"], setup["class"].school)
        french = gradebook_of(setup["french"], term)
        theirs = camara.post("/api/v1/grade-categories/", {"gradebook": french.pk, "name": "Dictées"}).data
        response = maths_marked["client"].post(
            "/api/v1/assessments/",
            {"gradebook": maths_marked["gradebook"].pk, "category": theirs["id"], "name": "Quiz"},
        )
        assert response.status_code == 400


class TestWhoMayChangeWhat:
    def test_the_class_teacher_can_read_but_not_change_another_subject(
        self, setup, term, maths_marked, client_for
    ):
        camara = client_for(setup["camara"], setup["class"].school)
        gradebook = maths_marked["gradebook"]
        detail = camara.get(f"{URL}{gradebook.pk}/").data
        assert detail["can"]["edit"] is False
        assert camara.get(f"{URL}{gradebook.pk}/sheet/").status_code == 200
        awa = setup["enrollments"]["Awa"].pk
        response = save(
            camara,
            gradebook,
            [{"assessment": maths_marked["items"]["Interro surprise"], "enrollment": awa, "score": "20"}],
        )
        assert response.status_code == 403
        response = camara.post("/api/v1/grade-categories/", {"gradebook": gradebook.pk, "name": "Bonus"})
        assert response.status_code == 403

    def test_other_teachers_and_schools_cannot_see_the_gradebook(
        self, setup, maths_marked, other_school, make_member, client_for
    ):
        gradebook = maths_marked["gradebook"]
        assert (
            client_for(setup["other"], setup["class"].school).get(f"{URL}{gradebook.pk}/").status_code == 404
        )
        outsider = make_member(other_school, "director")
        assert client_for(outsider, other_school).get(f"{URL}{gradebook.pk}/").status_code == 404

    def test_the_director_can_change_any_gradebook(
        self, school, setup, maths_marked, make_member, client_for
    ):
        director = client_for(make_member(school, "director"), school)
        gradebook = maths_marked["gradebook"]
        assert director.get(f"{URL}{gradebook.pk}/").data["can"]["edit"] is True
        awa = setup["enrollments"]["Awa"].pk
        response = save(
            director,
            gradebook,
            [{"assessment": maths_marked["items"]["Interro surprise"], "enrollment": awa, "score": "19"}],
        )
        assert response.data == {"changed": 1}


class TestWorkflow:
    def test_submit_send_back_publish_and_reopen(self, school, setup, maths_marked, make_member, client_for):
        teacher, gradebook, items = maths_marked["client"], maths_marked["gradebook"], maths_marked["items"]
        director = client_for(make_member(school, "director"), school)
        awa = setup["enrollments"]["Awa"].pk
        one_mark = [{"assessment": items["Interro surprise"], "enrollment": awa, "score": "12"}]

        response = teacher.post(f"{URL}{gradebook.pk}/submit/")
        assert response.status_code == 200 and response.data["status"] == "submitted"
        assert response.data["can"]["edit"] is False
        assert save(teacher, gradebook, one_mark).status_code == 400
        assert teacher.post(f"{URL}{gradebook.pk}/publish/").status_code == 403

        assert director.post(f"{URL}{gradebook.pk}/send-back/", {}).status_code == 400  # a reason is required
        response = director.post(
            f"{URL}{gradebook.pk}/send-back/", {"reason": "Composition marks missing for Moussa"}
        )
        assert response.data["status"] == "open"
        assert response.data["status_note"] == "Composition marks missing for Moussa"
        assert save(teacher, gradebook, one_mark).status_code == 200

        teacher.post(f"{URL}{gradebook.pk}/submit/")
        response = director.post(f"{URL}{gradebook.pk}/publish/")
        assert response.data["status"] == "published" and response.data["published_by_name"]
        assert teacher.post(f"{URL}{gradebook.pk}/reopen/", {"reason": "typo"}).status_code == 403
        response = director.post(f"{URL}{gradebook.pk}/reopen/", {"reason": "Wrong mark for Awa"})
        assert response.data["status"] == "open" and response.data["published_at"] is None
        actions = list(
            AuditLog.objects.filter(
                module="grades",
                entity_id=str(gradebook.pk),
                action__in=["submit", "return", "publish", "reopen"],
            )
            .order_by("id")
            .values_list("action", flat=True)
        )
        assert actions == ["submit", "return", "submit", "publish", "reopen"]

    def test_an_empty_gradebook_cannot_be_submitted(self, setup, term, client_for):
        client = client_for(setup["barry"], setup["class"].school)
        list_gradebooks(client, term)
        gradebook = gradebook_of(setup["maths"], term)
        assert client.post(f"{URL}{gradebook.pk}/submit/").status_code == 400


class TestCopySetup:
    def test_rules_and_assessments_are_reused_without_marks(
        self, setup, term, year, maths_marked, client_for
    ):
        client = maths_marked["client"]
        second_term = year.terms.order_by("order")[1]
        list_gradebooks(client, second_term)
        target = gradebook_of(setup["maths"], second_term)
        response = client.post(
            f"{URL}{target.pk}/copy-setup/",
            {"source": maths_marked["gradebook"].pk, "with_assessments": True},
            format="json",
        )
        assert response.status_code == 200, response.data
        assert [c["name"] for c in response.data["categories"]] == ["Interrogations", "Composition"]
        assert len(response.data["assessments"]) == 3
        assert not Grade.objects.filter(assessment__gradebook=target).exists()
        # Copying again is refused once the gradebook has assessments.
        again = client.post(f"{URL}{target.pk}/copy-setup/", {"source": maths_marked["gradebook"].pk})
        assert again.status_code == 400


class TestScalesAndClassResults:
    def test_class_results_weigh_subjects_by_their_coefficient(self, setup, term, maths_marked, client_for):
        camara = client_for(setup["camara"], setup["class"].school)
        french = gradebook_of(setup["french"], term)
        category = camara.post("/api/v1/grade-categories/", {"gradebook": french.pk, "name": "Dictées"}).data
        dictee = camara.post(
            "/api/v1/assessments/",
            {"gradebook": french.pk, "category": category["id"], "name": "Dictée 1", "max_score": "20"},
        ).data
        e = setup["enrollments"]
        save(
            camara,
            french,
            [
                {"assessment": dictee["id"], "enrollment": e["Awa"].pk, "score": "18"},
                {"assessment": dictee["id"], "enrollment": e["Binta"].pk, "score": "12"},
            ],
        )
        response = camara.get("/api/v1/class-results/", {"class_group": setup["class"].pk, "term": term.pk})
        assert response.status_code == 200, response.data
        assert [s["subject_name"] for s in response.data["subjects"]] == ["Français", "Mathématiques"]
        rows = {r["student_name"]: r for r in response.data["students"]}
        # Awa: (18 × 3 + 15.17 × 4) / 7 = 16.38; Binta: (12 × 3 + 11.33 × 4) / 7 = 11.62.
        # Moussa has no French mark yet: maths alone → 16.
        assert Decimal(rows["Awa Diallo"]["average"]) == Decimal("16.38")
        assert Decimal(rows["Binta Bah"]["average"]) == Decimal("11.62")
        assert Decimal(rows["Moussa Condé"]["average"]) == Decimal("16.00")
        assert [rows[n]["rank"] for n in ("Awa Diallo", "Moussa Condé", "Binta Bah")] == [1, 2, 3]

    def test_only_the_class_teacher_and_all_class_users_see_class_results(self, setup, term, client_for):
        params = {"class_group": setup["class"].pk, "term": term.pk}
        school = setup["class"].school
        assert client_for(setup["barry"], school).get("/api/v1/class-results/", params).status_code == 404
        assert client_for(setup["camara"], school).get("/api/v1/class-results/", params).status_code == 200

    def test_the_school_and_a_level_choose_their_scale(
        self, school, level, setup, term, maths_marked, make_member, client_for
    ):
        director = client_for(make_member(school, "director"), school)
        response = director.post(
            "/api/v1/grading-scales/", {"max_mark": "100", "pass_mark": "50", "decimals": 1}
        )
        assert response.status_code == 201, response.data
        assert (
            director.post("/api/v1/grading-scales/", {"max_mark": "10", "pass_mark": "5"}).status_code == 400
        )
        response = director.post(
            "/api/v1/grading-scales/",
            {"level": level.pk, "max_mark": "10", "pass_mark": "5", "decimals": 2, "rank_method": "dense"},
        )
        assert response.status_code == 201, response.data
        too_high = {"max_mark": "10", "pass_mark": "12", "level": None}
        assert director.post("/api/v1/grading-scales/", too_high, format="json").status_code == 400

        sheet = maths_marked["client"].get(f"{URL}{maths_marked['gradebook'].pk}/sheet/").data
        assert sheet["scale"]["max_mark"] == "10.00"
        assert Decimal(
            next(r for r in sheet["students"] if r["student_name"] == "Moussa Condé")["mark"]
        ) == Decimal("8.00")
        GradingScale.objects.filter(level=level).delete()
        sheet = maths_marked["client"].get(f"{URL}{maths_marked['gradebook'].pk}/sheet/").data
        assert Decimal(
            next(r for r in sheet["students"] if r["student_name"] == "Moussa Condé")["mark"]
        ) == Decimal("80.0")

    def test_teachers_cannot_change_the_scale(self, setup, client_for):
        teacher = client_for(setup["barry"], setup["class"].school)
        assert teacher.get("/api/v1/grading-scales/").status_code == 200
        assert (
            teacher.post("/api/v1/grading-scales/", {"max_mark": "100", "pass_mark": "50"}).status_code == 403
        )


def test_assessments_keep_any_name(setup, term, client_for):
    client = client_for(setup["barry"], setup["class"].school)
    list_gradebooks(client, term)
    gradebook = gradebook_of(setup["maths"], term)
    category = client.post(
        "/api/v1/grade-categories/", {"gradebook": gradebook.pk, "name": "1st Period"}
    ).data
    for name in ["Quiz #3 (pop)", "Devoir surveillé n°2", "Mid-term exam — Section B"]:
        client.post(
            "/api/v1/assessments/", {"gradebook": gradebook.pk, "category": category["id"], "name": name}
        )
    assert sorted(Assessment.objects.filter(gradebook=gradebook).values_list("name", flat=True)) == sorted(
        ["Quiz #3 (pop)", "Devoir surveillé n°2", "Mid-term exam — Section B"]
    )


def test_an_empty_gradebook_offers_no_submit_or_publish(school, setup, term, make_member, client_for):
    client = client_for(setup["barry"], school)
    list_gradebooks(client, term)
    gradebook = gradebook_of(setup["maths"], term)
    assert client.get(f"{URL}{gradebook.pk}/").data["can"]["submit"] is False
    director = client_for(make_member(school, "director"), school)
    assert director.get(f"{URL}{gradebook.pk}/").data["can"]["publish"] is False
