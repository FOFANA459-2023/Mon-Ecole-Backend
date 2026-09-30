import pytest

from apps.enrollments.services import enroll
from apps.people.services import link_guardian


@pytest.mark.django_db
class TestGlobalSearch:
    def test_finds_students_by_name_in_any_order(
        self, school, make_class, make_student, make_member, client_for
    ):
        student = make_student("Awa", "Diallo")
        enroll(student, make_class())
        response = client_for(make_member(school, "admin_staff"), school).get(
            "/api/v1/search/", {"q": "diallo awa"}
        )
        assert [s["id"] for s in response.data["students"]] == [student.pk]
        assert "7ème A" in response.data["students"][0]["subtitle"]

    def test_nul_characters_do_not_crash_the_search(self, school, make_member, make_student, client_for):
        # Found by the ZAP scan: PostgreSQL refuses NUL in strings, which used to become a 500.
        make_student("Awa", "Diallo")
        admin = make_member(school, "director")
        response = client_for(admin, school).get("/api/v1/search/", {"q": "dial\x00lo"})
        assert response.status_code == 200
        assert [s["title"] for s in response.data["students"]] == ["Awa Diallo"]

    def test_finds_guardians_by_phone(self, school, make_student, make_member, client_for):
        link_guardian(
            make_student(), guardian_data={"first_name": "Mariama", "last_name": "Bah", "phone": "620445566"}
        )
        response = client_for(make_member(school, "admin_staff"), school).get(
            "/api/v1/search/", {"q": "620445"}
        )
        assert [g["title"] for g in response.data["guardians"]] == ["Mariama Bah"]

    def test_results_respect_permissions(self, school, make_student, make_staff, make_member, client_for):
        make_student("Awa", "Diallo")
        make_staff("Awa", "Camara")
        accountant = make_member(school, "accountant")
        response = client_for(accountant, school).get("/api/v1/search/", {"q": "awa"})
        assert len(response.data["students"]) == 1
        assert response.data["staff"] == []  # accountants cannot see staff records

    def test_finds_receipts_and_invoices_for_finance_users(
        self, school, fees, make_class, make_student, make_member, client_for
    ):
        from decimal import Decimal

        from apps.finance.services import record_payment

        student = make_student("Awa", "Diallo")
        enroll(student, make_class())
        record_payment(student, amount=Decimal("1000"), method="mobile_money", reference="OM-77120")
        accountant = client_for(make_member(school, "accountant"), school)
        response = accountant.get("/api/v1/search/", {"q": "rec-"})
        assert [r["subtitle"].split(" · ")[0] for r in response.data["receipts"]] == ["Awa Diallo"]
        assert (
            accountant.get("/api/v1/search/", {"q": "77120"}).data["receipts"][0]["title"].startswith("REC-")
        )
        assert len(accountant.get("/api/v1/search/", {"q": "inv-"}).data["invoices"]) == 1
        teacher = client_for(make_member(school, "teacher"), school)
        assert teacher.get("/api/v1/search/", {"q": "rec-"}).data["receipts"] == []

    def test_other_schools_are_never_searched(
        self, school, other_school, make_student, make_member, client_for
    ):
        make_student("Awa", "Diallo", target_school=other_school)
        response = client_for(make_member(school, "director"), school).get("/api/v1/search/", {"q": "awa"})
        assert response.data["students"] == []


@pytest.mark.django_db
def test_dashboard_summary_counts(school, make_class, make_student, make_staff, make_member, client_for):
    class_group = make_class(capacity=40)
    enroll(make_student("Awa", "Diallo"), class_group)
    enroll(make_student("Sékou", "Bah", gender="M"), class_group)
    make_staff(staff_type="teacher")
    make_staff("Aminata", "Sow", staff_type="administrative")
    response = client_for(make_member(school, "director"), school).get("/api/v1/dashboard/summary/")
    assert response.status_code == 200
    data = response.data
    assert (data["students"], data["students_female"], data["students_male"]) == (2, 1, 1)
    assert (data["classes"], data["capacity"], data["teachers"], data["staff"]) == (1, 40, 1, 2)
    assert data["by_level"] == [{"level_id": class_group.level_id, "level": "7ème année", "count": 2}]
    assert data["scope"] == "school"


@pytest.mark.django_db
def test_teachers_dashboard_counts_only_their_classes(
    school, make_class, make_student, make_staff, make_member, client_for
):
    teacher = make_member(school, "teacher")
    mine = make_class("7ème A", class_teacher=make_staff(user=teacher))
    enroll(make_student("Awa", "Diallo"), mine)
    other = make_class("7ème B")
    enroll(make_student("Sékou", "Bah", gender="M"), other)
    enroll(make_student("Binta", "Sow"), other)
    data = client_for(teacher, school).get("/api/v1/dashboard/summary/").data
    assert data["scope"] == "my_classes"
    assert (data["students"], data["classes"]) == (1, 1)
    assert data["by_level"][0]["count"] == 1
