from datetime import date
from decimal import Decimal

import pytest

from apps.enrollments import services as enrollment_services
from apps.enrollments.models import Enrollment
from apps.finance.models import FeeCategory, FeeSchedule, Invoice, StudentDiscount
from apps.finance.money import split, to_money
from apps.finance.selectors import with_balances


@pytest.fixture
def tuition(school):
    return FeeCategory.objects.create(school=school, name="Scolarité", kind="tuition")


@pytest.fixture
def registration(school):
    return FeeCategory.objects.create(school=school, name="Inscription", kind="registration")


@pytest.fixture
def fees(school, year, level, tuition, registration):
    """Tuition 3 000 000 GNF in three installments, plus 250 000 GNF registration for new students."""
    FeeSchedule.objects.create(
        school=school,
        academic_year=year,
        level=level,
        category=tuition,
        amount=Decimal("3000000"),
        installments=[
            {"label": "", "due_date": "2026-10-01", "amount": "1000000"},
            {"label": "", "due_date": "2027-01-10", "amount": "1000000"},
            {"label": "", "due_date": "2027-04-01", "amount": "1000000"},
        ],
    )
    FeeSchedule.objects.create(
        school=school,
        academic_year=year,
        level=level,
        category=registration,
        applies_to=FeeSchedule.AppliesTo.NEW,
        amount=Decimal("250000"),
        installments=[{"label": "", "due_date": "2026-09-01", "amount": "250000"}],
    )


def test_money_rounds_to_the_currency_unit():
    assert to_money(Decimal("1000.5"), "GNF") == Decimal("1001")
    assert to_money(Decimal("10.005"), "LRD") == Decimal("10.01")


def test_split_always_adds_up():
    shares = split(Decimal("100000"), [Decimal("1"), Decimal("1"), Decimal("1")], "GNF")
    assert shares == [Decimal("33333"), Decimal("33333"), Decimal("33334")]
    assert sum(shares) == Decimal("100000")


@pytest.mark.django_db
class TestFeeSetup:
    def test_accountant_creates_a_schedule_with_installments(
        self, school, year, level, tuition, make_member, client_for
    ):
        client = client_for(make_member(school, "accountant"), school)
        response = client.post(
            "/api/v1/fee-schedules/",
            {
                "academic_year": year.pk,
                "level": level.pk,
                "category": tuition.pk,
                "amount": "900000",
                "installments": [
                    {"due_date": "2027-01-10", "amount": "450000"},
                    {"due_date": "2026-10-01", "amount": "450000"},
                ],
            },
            format="json",
        )
        assert response.status_code == 201, response.data
        assert [i["due_date"] for i in response.data["installments"]] == ["2026-10-01", "2027-01-10"]

    def test_installments_must_add_up(self, school, year, level, tuition, make_member, client_for):
        client = client_for(make_member(school, "accountant"), school)
        response = client.post(
            "/api/v1/fee-schedules/",
            {
                "academic_year": year.pk,
                "level": level.pk,
                "category": tuition.pk,
                "amount": "900000",
                "installments": [{"due_date": "2026-10-01", "amount": "400000"}],
            },
            format="json",
        )
        assert response.status_code == 400
        assert "installments" in response.data["fields"]

    def test_without_installments_the_fee_is_due_at_the_start_of_the_year(
        self, school, year, level, tuition, make_member, client_for
    ):
        client = client_for(make_member(school, "accountant"), school)
        response = client.post(
            "/api/v1/fee-schedules/",
            {"academic_year": year.pk, "level": level.pk, "category": tuition.pk, "amount": "500000"},
            format="json",
        )
        assert response.status_code == 201, response.data
        assert response.data["installments"] == [
            {"label": "", "due_date": "2026-09-01", "amount": "500000.00"}
        ]

    def test_teachers_cannot_see_or_change_fees(self, school, make_member, client_for):
        client = client_for(make_member(school, "teacher"), school)
        assert client.get("/api/v1/fee-schedules/").status_code == 403
        assert client.post("/api/v1/fee-categories/", {"name": "Cantine"}, format="json").status_code == 403

    def test_fixed_discount_needs_a_category(self, school, year, make_student, make_member, client_for):
        client = client_for(make_member(school, "accountant"), school)
        response = client.post(
            "/api/v1/student-discounts/",
            {"student": make_student().pk, "academic_year": year.pk, "kind": "fixed", "value": "50000"},
            format="json",
        )
        assert response.status_code == 400
        assert "category" in response.data["fields"]


@pytest.mark.django_db
class TestInvoicesFromEnrolment:
    def test_enrolling_issues_the_year_fees_by_installment(self, school, fees, make_class, make_student):
        enrollment = enrollment_services.enroll(
            make_student(), make_class(), enrollment_date=date(2026, 9, 2)
        )
        invoice = Invoice.objects.get(enrollment=enrollment)
        assert invoice.source == Invoice.Source.ENROLMENT
        assert invoice.number == "INV-2026-000001"
        assert invoice.total == Decimal("3250000")
        assert [line.due_date for line in invoice.lines.all()] == [
            date(2026, 9, 1),
            date(2026, 10, 1),
            date(2027, 1, 10),
            date(2027, 4, 1),
        ]
        # Labels follow the school's language (French here), whatever the user's.
        assert [line.description for line in invoice.lines.all()][1:] == [
            "Scolarité — tranche 1/3",
            "Scolarité — tranche 2/3",
            "Scolarité — tranche 3/3",
        ]

    def test_discount_is_spread_over_the_installments(
        self, school, year, fees, tuition, make_class, make_student
    ):
        student = make_student()
        StudentDiscount.objects.create(
            school=school, student=student, academic_year=year, category=tuition, kind="percent", value=10
        )
        enrollment = enrollment_services.enroll(student, make_class())
        invoice = Invoice.objects.get(enrollment=enrollment)
        tuition_lines = invoice.lines.filter(category=tuition)
        assert [line.discount for line in tuition_lines] == [Decimal("100000")] * 3
        assert invoice.discount_total == Decimal("300000")
        assert invoice.total == Decimal("2950000")

    def test_returning_students_do_not_pay_new_student_fees(self, school, fees, make_class, make_student):
        enrollment = enrollment_services.enroll(
            make_student(), make_class(), kind=Enrollment.Kind.RE_ENROLMENT
        )
        assert Invoice.objects.get(enrollment=enrollment).total == Decimal("3000000")

    def test_no_invoice_without_fee_schedules(self, school, make_class, make_student):
        enrollment_services.enroll(make_student(), make_class())
        assert not Invoice.objects.exists()

    def test_class_change_does_not_charge_twice(self, school, fees, make_class, make_student):
        enrollment = enrollment_services.enroll(make_student(), make_class())
        enrollment_services.change_class(enrollment, make_class("7ème B"), reason="Room")
        assert Invoice.objects.count() == 1

    def test_cancelling_a_mistaken_enrolment_cancels_its_fees(self, school, fees, make_class, make_student):
        enrollment = enrollment_services.enroll(make_student(), make_class())
        enrollment_services.cancel(enrollment, reason="Wrong student")
        assert Invoice.objects.get().status == Invoice.Status.CANCELLED

    def test_generate_for_a_class_skips_students_already_invoiced(
        self, school, year, level, tuition, make_class, make_student, make_member, client_for
    ):
        class_group = make_class()
        enrollment_services.enroll(make_student("Awa"), class_group)
        enrollment_services.enroll(make_student("Binta"), class_group)
        assert not Invoice.objects.exists()  # the fees were set up after the students were enrolled
        FeeSchedule.objects.create(
            school=school,
            academic_year=year,
            level=level,
            category=tuition,
            amount=Decimal("600000"),
            installments=[{"label": "", "due_date": "2026-10-01", "amount": "600000"}],
        )
        from apps.academics.models import Level

        other_level = Level.objects.create(school=school, name="8ème année", order=8)
        enrollment_services.enroll(make_student("Codou"), make_class("8ème A", class_level=other_level))
        client = client_for(make_member(school, "accountant"), school)
        payload = {"academic_year": year.pk}
        assert client.post("/api/v1/invoices/generate/", payload, format="json").data == {
            "created": 2,
            "skipped": 0,
            "without_fees": 1,
        }
        assert client.post("/api/v1/invoices/generate/", payload, format="json").data == {
            "created": 0,
            "skipped": 2,
            "without_fees": 1,
        }


@pytest.mark.django_db
class TestInvoiceApi:
    def test_manual_invoice_and_cancellation(
        self, school, year, tuition, make_student, make_member, client_for
    ):
        client = client_for(make_member(school, "accountant"), school)
        response = client.post(
            "/api/v1/invoices/",
            {
                "student": make_student().pk,
                "academic_year": year.pk,
                "issue_date": "2026-11-05",
                "lines": [
                    {
                        "category": tuition.pk,
                        "description": "Uniforme",
                        "due_date": "2026-11-30",
                        "amount": "75000",
                    }
                ],
            },
            format="json",
        )
        assert response.status_code == 201, response.data
        assert response.data["total"] == "75000.00"
        assert response.data["payment_status"] in ("unpaid", "overdue")
        invoice_id = response.data["id"]

        assert client.post(f"/api/v1/invoices/{invoice_id}/cancel/", {}, format="json").status_code == 400
        cancelled = client.post(
            f"/api/v1/invoices/{invoice_id}/cancel/", {"reason": "Duplicate"}, format="json"
        )
        assert cancelled.status_code == 200
        assert cancelled.data["payment_status"] == "cancelled"
        again = client.post(f"/api/v1/invoices/{invoice_id}/cancel/", {"reason": "Again"}, format="json")
        assert again.status_code == 400

    def test_invoices_cannot_be_edited_or_deleted(
        self, school, fees, make_class, make_student, make_member, client_for
    ):
        invoice = Invoice.objects.get(enrollment=enrollment_services.enroll(make_student(), make_class()))
        client = client_for(make_member(school, "director"), school)
        assert client.patch(f"/api/v1/invoices/{invoice.pk}/", {"notes": "x"}, format="json").status_code in (
            403,
            405,
        )
        assert client.delete(f"/api/v1/invoices/{invoice.pk}/").status_code in (403, 405)

    def test_overdue_status_and_filter(self, school, fees, make_class, make_student, make_member, client_for):
        enrollment_services.enroll(make_student(), make_class())
        invoice = with_balances(Invoice.objects.all(), today=date(2026, 10, 15)).get()
        assert invoice.payment_status == "overdue"
        assert invoice.overdue_amount == Decimal("1250000")  # registration + first tuition installment
        assert invoice.next_due_date == date(2027, 1, 10)
        client = client_for(make_member(school, "accountant"), school)
        assert client.get("/api/v1/invoices/", {"payment_status": "paid"}).data["count"] == 0

    def test_only_the_school_own_invoices(
        self, school, other_school, fees, make_class, make_student, make_member, client_for
    ):
        invoice = Invoice.objects.get(enrollment=enrollment_services.enroll(make_student(), make_class()))
        outsider = client_for(make_member(other_school, "accountant"), other_school)
        assert outsider.get(f"/api/v1/invoices/{invoice.pk}/").status_code == 404
        assert outsider.get("/api/v1/invoices/").data["count"] == 0

    def test_pdf(self, school, fees, make_class, make_student, make_member, client_for):
        invoice = Invoice.objects.get(enrollment=enrollment_services.enroll(make_student(), make_class()))
        response = client_for(make_member(school, "accountant"), school).get(
            f"/api/v1/invoices/{invoice.pk}/pdf/"
        )
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        assert response.content.startswith(b"%PDF")
