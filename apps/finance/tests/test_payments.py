from datetime import date
from decimal import Decimal

import pytest

from apps.audit.models import AuditLog
from apps.enrollments import services as enrollment_services
from apps.finance import services
from apps.finance.models import Invoice, Payment
from apps.finance.selectors import student_account, student_credit, with_balances
from apps.finance.words import amount_in_words, english, french

PAID_ON = date(2026, 9, 15)


@pytest.fixture
def enrolled(school, fees, make_class, make_student):
    """A new student enrolled in 7ème A: 250 000 registration + 3 × 1 000 000 tuition = 3 250 000 GNF."""
    enrollment = enrollment_services.enroll(make_student(), make_class(), enrollment_date=date(2026, 9, 2))
    return enrollment.student


@pytest.fixture
def invoice(enrolled):
    return Invoice.objects.get(student=enrolled)


def _balances(invoice):
    return with_balances(Invoice.objects.filter(pk=invoice.pk), today=date(2026, 9, 20)).get()


def _pay(student, amount, **extra):
    extra.setdefault("method", Payment.Method.CASH)
    extra.setdefault("payment_date", PAID_ON)
    return services.record_payment(student, amount=Decimal(amount), **extra)


class TestAmountInWords:
    @pytest.mark.parametrize(
        ("number", "words"),
        [
            (21, "vingt et un"),
            (71, "soixante et onze"),
            (80, "quatre-vingts"),
            (81, "quatre-vingt-un"),
            (99, "quatre-vingt-dix-neuf"),
            (200, "deux cents"),
            (201, "deux cent un"),
            (1000, "mille"),
            (80000, "quatre-vingt mille"),
            (250000, "deux cent cinquante mille"),
            (80000000, "quatre-vingts millions"),
            (1250000, "un million deux cent cinquante mille"),
        ],
    )
    def test_french(self, number, words):
        assert french(number) == words

    def test_english(self):
        assert english(1250) == "one thousand two hundred fifty"
        assert english(2_000_021) == "two million twenty-one"

    def test_with_the_currency(self):
        assert (
            amount_in_words(Decimal("2500000"), "GNF", "fr")
            == "Deux millions cinq cent mille francs guinéens"
        )
        assert amount_in_words(Decimal("2000000"), "GNF", "fr") == "Deux millions de francs guinéens"
        assert (
            amount_in_words(Decimal("1250.50"), "LRD", "en")
            == "One thousand two hundred fifty Liberian dollars and fifty cents"
        )


@pytest.mark.django_db
class TestRecordingPayments:
    def test_the_oldest_due_lines_are_paid_first(self, school, enrolled, invoice):
        payment = _pay(enrolled, "600000")
        assert payment.number == "REC-2026-000001"
        paid = [(a.invoice_line.description, a.amount) for a in payment.allocations.all()]
        assert paid == [("Inscription", Decimal("250000")), ("Scolarité — tranche 1/3", Decimal("350000"))]
        invoice = _balances(invoice)
        assert invoice.amount_paid == Decimal("600000")
        assert invoice.balance == Decimal("2650000")
        assert invoice.payment_status == "partial"
        assert AuditLog.objects.filter(action="create", entity_type="finance.payment").exists()

    def test_paying_everything_marks_the_invoice_paid(self, school, enrolled, invoice):
        _pay(enrolled, "1250000")
        _pay(enrolled, "2000000")
        assert _balances(invoice).payment_status == "paid"
        assert Payment.objects.order_by("id").last().number == "REC-2026-000002"

    def test_amounts_are_rounded_to_the_currency(self, school, enrolled):
        assert _pay(enrolled, "1000.4").amount == Decimal("1000")
        with pytest.raises(Exception, match="greater than zero"):
            _pay(enrolled, "0.4")

    def test_a_payment_cannot_be_dated_in_the_future(self, school, enrolled):
        with pytest.raises(Exception, match="future"):
            _pay(enrolled, "1000", payment_date=date(2999, 1, 1))

    def test_overpayment_is_kept_as_credit_and_used_by_the_next_invoice(
        self, school, year, tuition, enrolled, invoice
    ):
        payment = _pay(enrolled, "3500000")
        assert _balances(invoice).payment_status == "paid"
        assert student_credit(enrolled) == Decimal("250000")
        extra = services.create_manual_invoice(
            enrolled,
            academic_year=year,
            lines=[
                {
                    "category": tuition,
                    "description": "Uniforme",
                    "due_date": date(2026, 10, 1),
                    "amount": 75000,
                }
            ],
        )
        assert _balances(extra).payment_status == "paid"
        assert student_credit(enrolled) == Decimal("175000")
        assert payment.allocations.filter(invoice_line__invoice=extra).get().amount == Decimal("75000")

    def test_the_accountant_chooses_the_lines_paid(self, school, enrolled, invoice):
        last = invoice.lines.order_by("due_date").last()
        payment = _pay(
            enrolled, "1200000", allocations=[{"invoice_line": last, "amount": Decimal("1000000")}]
        )
        assert [(a.invoice_line_id, a.amount) for a in payment.allocations.all()] == [
            (last.pk, Decimal("1000000"))
        ]
        assert student_credit(enrolled) == Decimal("200000")

    def test_chosen_lines_are_checked(self, school, enrolled, invoice, make_student, make_class):
        first = invoice.lines.order_by("due_date").first()
        with pytest.raises(Exception, match="only"):
            _pay(enrolled, "500000", allocations=[{"invoice_line": first, "amount": Decimal("300000")}])
        with pytest.raises(Exception, match="more than the payment"):
            _pay(enrolled, "100000", allocations=[{"invoice_line": first, "amount": Decimal("200000")}])
        other = make_student("Binta", "Bah")
        enrollment_services.enroll(other, make_class("7ème B"))
        their_line = Invoice.objects.get(student=other).lines.first()
        with pytest.raises(Exception, match="unpaid lines of this student"):
            _pay(enrolled, "100000", allocations=[{"invoice_line": their_line, "amount": Decimal("100000")}])
        assert not Payment.objects.exists()

    def test_an_empty_allocation_keeps_everything_as_credit(self, school, enrolled, invoice):
        _pay(enrolled, "500000", allocations=[])
        assert _balances(invoice).amount_paid == 0
        assert student_credit(enrolled) == Decimal("500000")


@pytest.mark.django_db
class TestReversals:
    def test_a_reversed_payment_stops_counting(self, school, enrolled, invoice):
        payment = _pay(enrolled, "1250000")
        services.reverse_payment(payment, reason="Wrong student")
        payment.refresh_from_db()
        assert payment.status == Payment.Status.REVERSED
        assert payment.reversal_reason == "Wrong student"
        assert _balances(invoice).amount_paid == 0
        with pytest.raises(Exception, match="already reversed"):
            services.reverse_payment(payment, reason="Again")

    def test_credit_from_another_payment_fills_the_reopened_lines(self, school, enrolled, invoice):
        first = _pay(enrolled, "1250000")
        _pay(enrolled, "2500000")  # pays the rest (2 000 000) and leaves 500 000 of credit
        assert student_credit(enrolled) == Decimal("500000")
        services.reverse_payment(first, reason="Cheque bounced")
        assert student_credit(enrolled) == 0
        assert _balances(invoice).amount_paid == Decimal("2500000")

    def test_an_invoice_with_payments_is_cancelled_only_after_reversing_them(self, school, enrolled, invoice):
        payment = _pay(enrolled, "100000")
        with pytest.raises(Exception, match="Reverse them"):
            services.cancel_invoice(invoice, reason="Duplicate")
        services.reverse_payment(payment, reason="Duplicate invoice")
        assert services.cancel_invoice(invoice, reason="Duplicate").status == Invoice.Status.CANCELLED


@pytest.mark.django_db
class TestPaymentApi:
    def test_accountant_records_and_reverses_a_payment(
        self, school, enrolled, invoice, make_member, client_for
    ):
        client = client_for(make_member(school, "accountant"), school)
        response = client.post(
            "/api/v1/payments/",
            {
                "student": enrolled.pk,
                "amount": "300000",
                "date": "2026-09-15",
                "method": "mobile_money",
                "reference": "OM-4411",
                "payer_name": "Mariama Diallo",
            },
            format="json",
        )
        assert response.status_code == 201, response.data
        data = response.data
        assert data["number"] == "REC-2026-000001"
        assert data["allocated"] == "300000.00"
        assert data["unallocated"] == "0.00"
        assert data["received_by_name"] == "Test accountant"
        assert [a["amount"] for a in data["allocations"]] == ["250000.00", "50000.00"]

        detail = client.get(f"/api/v1/invoices/{invoice.pk}/").data
        assert detail["amount_paid"] == "300000.00"
        assert detail["payments"] == [
            {
                "id": data["id"],
                "number": "REC-2026-000001",
                "date": "2026-09-15",
                "method": "mobile_money",
                "status": "posted",
                "amount": "300000.00",
            }
        ]
        assert [(line["paid"], line["balance"]) for line in detail["lines"]][:2] == [
            ("250000.00", "0.00"),
            ("50000.00", "950000.00"),
        ]
        assert client.get("/api/v1/payments/", {"invoice": invoice.pk}).data["count"] == 1

        reversed_ = client.post(f"/api/v1/payments/{data['id']}/reverse/", {"reason": "Typo"}, format="json")
        assert reversed_.status_code == 200
        assert reversed_.data["status"] == "reversed"
        assert reversed_.data["unallocated"] == "0.00"
        assert client.get(f"/api/v1/invoices/{invoice.pk}/").data["amount_paid"] == "0.00"

    def test_chosen_lines_through_the_api(self, school, enrolled, invoice, make_member, client_for):
        line = invoice.lines.order_by("due_date").last()
        client = client_for(make_member(school, "accountant"), school)
        response = client.post(
            "/api/v1/payments/",
            {
                "student": enrolled.pk,
                "amount": "1000000",
                "method": "cash",
                "allocations": [{"invoice_line": line.pk, "amount": "1000000"}],
            },
            format="json",
        )
        assert response.status_code == 201, response.data
        assert [a["invoice_line"] for a in response.data["allocations"]] == [line.pk]

    def test_payments_cannot_be_edited_or_deleted(self, school, enrolled, make_member, client_for):
        payment = _pay(enrolled, "1000")
        client = client_for(make_member(school, "director"), school)
        assert client.patch(
            f"/api/v1/payments/{payment.pk}/", {"amount": "1"}, format="json"
        ).status_code in (
            403,
            405,
        )
        assert client.delete(f"/api/v1/payments/{payment.pk}/").status_code in (403, 405)

    def test_who_may_see_record_and_reverse(self, school, enrolled, make_member, client_for):
        payment = _pay(enrolled, "1000")
        payload = {"student": enrolled.pk, "amount": "1000", "method": "cash"}
        for role in ("teacher", "admin_staff"):
            client = client_for(make_member(school, role), school)
            assert client.get("/api/v1/payments/").status_code == 403
            assert client.post("/api/v1/payments/", payload, format="json").status_code == 403
            assert client.post(f"/api/v1/payments/{payment.pk}/reverse/", {"reason": "x"}).status_code == 403
        director = client_for(make_member(school, "director"), school)
        assert director.post("/api/v1/payments/", payload, format="json").status_code == 201

    def test_only_the_school_own_payments_and_lines(
        self, school, other_school, enrolled, invoice, make_member, client_for, make_student
    ):
        payment = _pay(enrolled, "1000")
        outsider = client_for(make_member(other_school, "accountant"), other_school)
        assert outsider.get(f"/api/v1/payments/{payment.pk}/").status_code == 404
        assert outsider.get(f"/api/v1/payments/{payment.pk}/receipt/").status_code == 404
        assert outsider.get("/api/v1/payments/").data["count"] == 0
        assert outsider.get(f"/api/v1/student-accounts/{enrolled.pk}/").status_code == 404
        their_student = make_student("Fanta", "Camara", target_school=other_school)
        response = outsider.post(
            "/api/v1/payments/",
            {
                "student": their_student.pk,
                "amount": "1000",
                "method": "cash",
                "allocations": [{"invoice_line": invoice.lines.first().pk, "amount": "1000"}],
            },
            format="json",
        )
        assert response.status_code == 400
        assert any(key.startswith("allocations") for key in response.data["fields"])

    def test_student_account(self, school, enrolled, make_member, client_for):
        _pay(enrolled, "1500000")
        data = (
            client_for(make_member(school, "accountant"), school)
            .get(f"/api/v1/student-accounts/{enrolled.pk}/")
            .data
        )
        assert data["invoiced"] == "3250000.00"
        assert data["paid"] == "1500000.00"
        assert data["balance"] == "1750000.00"
        assert data["credit"] == "0.00"
        assert [(line["description"], line["balance"]) for line in data["open_lines"]] == [
            ("Scolarité — tranche 2/3", "750000.00"),
            ("Scolarité — tranche 3/3", "1000000.00"),
        ]
        assert student_account(enrolled)["balance"] == Decimal("1750000")

    def test_receipt_pdf(self, school, enrolled, make_member, client_for):
        payment = _pay(enrolled, "3500000", payer_name="Mariama Diallo")
        client = client_for(make_member(school, "accountant"), school)
        response = client.get(f"/api/v1/payments/{payment.pk}/receipt/")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        assert response.content.startswith(b"%PDF")
        services.reverse_payment(payment, reason="Duplicate")
        assert client.get(f"/api/v1/payments/{payment.pk}/receipt/").content.startswith(b"%PDF")
