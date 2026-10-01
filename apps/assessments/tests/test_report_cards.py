from datetime import date
from decimal import Decimal as D

import pytest

from apps.assessments import engine
from apps.assessments.models import Gradebook, GradingScale, ReportComment
from apps.assessments.reportcards import annual_cards, report_cards_pdf, term_cards
from apps.attendance.models import AttendanceRecord, ClassRegister
from apps.audit.models import AuditLog

from .conftest import URL, gradebook_of, list_gradebooks, save

pytestmark = pytest.mark.django_db

BANDS = [{"min": 16, "label": "Très bien"}, {"min": 14, "label": "Bien"}, {"min": 10, "label": "Passable"}]


def test_mention_and_mean():
    assert engine.mention(D("16.5"), BANDS) == "Très bien"
    assert engine.mention(D("14"), BANDS) == "Bien"
    assert engine.mention(D("9.99"), BANDS) == ""
    assert engine.mention(None, BANDS) == ""
    assert engine.mean([D("12"), None, D("15")], 2) == D("13.50")
    assert engine.mean([None], 2) is None


@pytest.fixture
def published(setup, term, maths_marked, school, make_member, client_for):
    """Maths published; French marked (Awa 18/20, Binta 12/20) but still in progress."""
    camara = client_for(setup["camara"], school)
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
    director = client_for(make_member(school, "director"), school)
    assert director.post(f"{URL}{maths_marked['gradebook'].pk}/publish/").status_code == 200
    GradingScale.objects.create(school=school, mentions=BANDS)
    return {"director": director, "camara": camara, "french": french}


class TestTermCards:
    def test_only_published_subjects_count(self, setup, term, published):
        data = term_cards(setup["class"], term)
        cards = {c["enrollment"].student.first_name: c for c in data["cards"]}
        awa = cards["Awa"]
        assert [line["subject"] for line in awa["lines"]] == ["Mathématiques"]
        maths = awa["lines"][0]
        assert (maths["mark"], maths["coefficient"], maths["weighted"]) == (D("15.17"), D("4.0"), D("60.680"))
        assert maths["rank"] == 2 and maths["appreciation"] == "Bien"
        assert awa["average"] == D("15.17") and awa["rank"] == 2 and awa["mention"] == "Bien"
        assert cards["Moussa"]["mention"] == "Très bien" and cards["Moussa"]["rank"] == 1
        assert data["ranked"] == 3 and data["class_size"] == 3

        published["director"].post(f"{URL}{published['french'].pk}/publish/")
        awa = next(
            c
            for c in term_cards(setup["class"], term)["cards"]
            if c["enrollment"].student.first_name == "Awa"
        )
        assert [line["subject"] for line in awa["lines"]] == ["Français", "Mathématiques"]
        assert awa["average"] == D("16.38") and awa["total_coefficients"] == D("7.0")

    def test_attendance_of_the_term_and_the_comment_are_on_the_card(self, setup, term, published, school):
        e = setup["enrollments"]
        register = ClassRegister.objects.create(
            school=school, class_group=setup["class"], date=date(2026, 10, 5)
        )
        for first, status in [("Awa", "absent"), ("Binta", "late"), ("Moussa", "excused")]:
            AttendanceRecord.objects.create(
                school=school, register=register, enrollment=e[first], status=status
            )
        ReportComment.objects.create(school=school, enrollment=e["Awa"], term=term, comment="Bon trimestre.")
        cards = {c["enrollment"].student.first_name: c for c in term_cards(setup["class"], term)["cards"]}
        assert cards["Awa"]["attendance"]["absent"] == 1 and cards["Awa"]["comment"] == "Bon trimestre."
        assert cards["Binta"]["attendance"]["late"] == 1
        assert cards["Moussa"]["attendance"] == {
            "enrollment": e["Moussa"].pk,
            "absent": 1,
            "excused": 1,
            "late": 0,
        }

    def test_the_pdf_has_one_page_per_student(self, setup, term, published):
        pdf = report_cards_pdf(term_cards(setup["class"], term))
        assert pdf.startswith(b"%PDF") and pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page ") >= 3


class TestAnnualCards:
    def test_the_year_is_the_average_of_the_term_averages(
        self, setup, term, year, published, client_for, school
    ):
        second = year.terms.order_by("order")[1]
        barry = client_for(setup["barry"], school)
        list_gradebooks(barry, second)
        maths2 = gradebook_of(setup["maths"], second)
        category = barry.post("/api/v1/grade-categories/", {"gradebook": maths2.pk, "name": "Compo"}).data
        compo = barry.post(
            "/api/v1/assessments/",
            {"gradebook": maths2.pk, "category": category["id"], "name": "Compo T2", "max_score": "20"},
        ).data
        e = setup["enrollments"]
        save(barry, maths2, [{"assessment": compo["id"], "enrollment": e["Awa"].pk, "score": "11"}])
        published["director"].post(f"{URL}{maths2.pk}/publish/")

        data = annual_cards(setup["class"])
        awa = next(c for c in data["cards"] if c["enrollment"].student.first_name == "Awa")
        # Term 1: 15.17, term 2: 11.00, term 3: nothing published → (15.17 + 11) / 2 = 13.085 → 13.09.
        assert awa["term_averages"] == [D("15.17"), D("11.00"), None]
        assert awa["average"] == D("13.09") and awa["passed"] is True and awa["mention"] == "Passable"
        assert awa["lines"][0]["terms"] == [D("15.17"), D("11.00"), None]
        assert report_cards_pdf(data).startswith(b"%PDF")


class TestEndpoints:
    def test_the_director_prints_a_class_or_one_student(self, setup, term, published):
        director = published["director"]
        params = {"class_group": setup["class"].pk, "term": term.pk}
        response = director.get("/api/v1/report-cards/", params)
        assert response.status_code == 200 and response["Content-Type"] == "application/pdf"
        assert response.content.startswith(b"%PDF")
        one = director.get("/api/v1/report-cards/", {**params, "enrollment": setup["enrollments"]["Awa"].pk})
        assert one.status_code == 200 and "awa-diallo" in one["Content-Disposition"]
        annual = director.get("/api/v1/report-cards/", {"class_group": setup["class"].pk})
        assert annual.status_code == 200 and "annuel" in annual["Content-Disposition"]
        assert AuditLog.objects.filter(module="grades", action="export").count() == 3

    def test_teachers_cannot_print_report_cards(self, setup, term, published, client_for, school):
        response = published["camara"].get(
            "/api/v1/report-cards/", {"class_group": setup["class"].pk, "term": term.pk}
        )
        assert response.status_code == 403

    def test_the_class_teacher_writes_the_comments(self, setup, term, published, client_for, school):
        camara = published["camara"]
        e = setup["enrollments"]
        params = {"class_group": setup["class"].pk, "term": term.pk}
        rows = camara.get("/api/v1/report-comments/", params).data
        assert [r["comment"] for r in rows] == ["", "", ""]
        response = camara.post(
            "/api/v1/report-comments/",
            {**params, "comments": [{"enrollment": e["Awa"].pk, "comment": "Très bon travail."}]},
            format="json",
        )
        assert response.status_code == 200
        assert {r["student_name"]: r["comment"] for r in response.data}["Awa Diallo"] == "Très bon travail."
        # Year comments are separate from term comments.
        assert all(
            r["comment"] == ""
            for r in camara.get("/api/v1/report-comments/", {"class_group": setup["class"].pk}).data
        )
        # Mr Barry teaches the class but does not lead it.
        barry = client_for(setup["barry"], school)
        assert barry.get("/api/v1/report-comments/", params).status_code == 404

    def test_a_student_s_results_this_year(self, setup, term, published, client_for, school):
        awa = setup["enrollments"]["Awa"]
        data = published["director"].get(f"/api/v1/student-results/{awa.student_id}/").data
        assert data["class_name"] == "7ème A"
        first, *_, year_row = data["terms"]
        assert (D(first["average"]), first["rank"], first["mention"], first["subjects"]) == (
            D("15.17"),
            2,
            "Bien",
            1,
        )
        assert year_row["term"] is None and D(year_row["average"]) == D("15.17")
        # Mr Barry teaches the class but is not its class teacher: class-wide results stay hidden.
        assert (
            client_for(setup["barry"], school).get(f"/api/v1/student-results/{awa.student_id}/").status_code
            == 404
        )

    def test_scales_keep_honours_bands_sorted_and_checked(self, school, make_member, client_for):
        director = client_for(make_member(school, "director"), school)
        response = director.post(
            "/api/v1/grading-scales/",
            {
                "max_mark": "20",
                "pass_mark": "10",
                "mentions": [{"min": 10, "label": "Passable"}, {"min": 16, "label": "Très bien"}],
            },
            format="json",
        )
        assert response.status_code == 201, response.data
        assert [m["label"] for m in response.data["mentions"]] == ["Très bien", "Passable"]
        bad = director.patch(
            f"/api/v1/grading-scales/{response.data['id']}/",
            {"mentions": [{"min": 25, "label": "Too high"}]},
            format="json",
        )
        assert bad.status_code == 400


def test_unpublished_gradebooks_never_reach_a_card(setup, term, maths_marked):
    Gradebook.objects.update(status=Gradebook.Status.SUBMITTED)
    cards = term_cards(setup["class"], term)["cards"]
    assert all(card["lines"] == [] and card["average"] is None for card in cards)
    assert report_cards_pdf(term_cards(setup["class"], term)).startswith(b"%PDF")


def test_last_year_s_results_survive_moving_up(setup, term, published, school, level, make_class):
    """Once the class has moved up, its enrolments are "completed": last year's results must still count."""
    from apps.academics.services import create_academic_year
    from apps.enrollments import services as enrollment_services

    next_year = create_academic_year(
        school, name="2027-2028", start_date=date(2027, 9, 1), end_date=date(2028, 6, 30), term_count=3
    )
    upper = make_class("8ème A", academic_year=next_year)
    enrollment_services.promote(setup["class"], upper)
    rows = term_cards(setup["class"], term)["cards"]
    assert len(rows) == 3 and {c["average"] for c in rows} != {None}
    sheet = published["director"].get(f"{URL}{gradebook_of(setup['maths'], term).pk}/sheet/").data
    assert sorted(r["rank"] for r in sheet["students"]) == [1, 2, 3]
    comments = (
        published["director"]
        .get("/api/v1/report-comments/", {"class_group": setup["class"].pk, "term": term.pk})
        .data
    )
    assert len(comments) == 3
