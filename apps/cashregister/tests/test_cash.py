from datetime import date
from decimal import Decimal

import pytest

from apps.audit.models import AuditLog
from apps.cashregister import services as cash
from apps.cashregister.models import CashMovement, CashRegister, CashSession
from apps.enrollments import services as enrollment_services
from apps.finance import services as finance
from apps.finance.models import Expense, Invoice, Payment
from apps.finance.selectors import student_credit, with_balances

TODAY_ISH = date(2026, 9, 15)


@pytest.fixture
def enrolled(school, fees, make_class, make_student):
    """3 250 000 GNF due: 250 000 registration + 3 × 1 000 000 tuition."""
    return enrollment_services.enroll(make_student(), make_class(), enrollment_date=date(2026, 9, 2)).student


def _pay(student, amount, **extra):
    extra.setdefault("method", Payment.Method.CASH)
    extra.setdefault("payment_date", TODAY_ISH)
    return finance.record_payment(student, amount=Decimal(amount), **extra)


def _expected(session):
    return cash.totals(session)["expected"]


@pytest.mark.django_db
class TestSessions:
    def test_the_float_defaults_to_the_last_count(self, school):
        register = cash.default_register(school)
        assert register.name == "Caisse principale"
        first = cash.open_session(register, opening_balance=Decimal("50000"))
        cash.record_movement(first, direction="in", amount=Decimal("20000"), description="Top-up")
        cash.close_session(first, counted=Decimal("70000"))
        second = cash.open_session(register)
        assert second.opening_balance == Decimal("70000")

    def test_one_open_session_per_register(self, school):
        register = cash.default_register(school)
        cash.open_session(register)
        with pytest.raises(Exception, match="already open"):
            cash.open_session(register)

    def test_closing_records_the_difference_and_needs_a_reason_for_it(self, school):
        session = cash.open_session(cash.default_register(school), opening_balance=Decimal("100000"))
        cash.record_movement(session, direction="in", amount=Decimal("45000"), description="Sale of uniforms")
        with pytest.raises(Exception, match="Explain the difference"):
            cash.close_session(session, counted=Decimal("140000"))
        closed = cash.close_session(session, counted=Decimal("140000"), note="5 000 missing, counted twice")
        assert (closed.expected_closing, closed.counted_closing, closed.difference) == (
            Decimal("145000"),
            Decimal("140000"),
            Decimal("-5000"),
        )
        with pytest.raises(Exception, match="closed"):
            cash.record_movement(closed, direction="in", amount=Decimal("1"), description="Late")
        assert AuditLog.objects.filter(action="close", module="cash").exists()

    def test_money_out_never_exceeds_the_cash_in_the_register(self, school):
        session = cash.open_session(cash.default_register(school), opening_balance=Decimal("10000"))
        with pytest.raises(Exception, match="Only 10000"):
            cash.record_movement(
                session, direction="out", amount=Decimal("10001"), description="Bank deposit"
            )
        cash.record_movement(session, direction="out", amount=Decimal("10000"), description="Bank deposit")
        assert _expected(session) == 0


@pytest.mark.django_db
class TestCashPayments:
    def test_a_cash_payment_goes_into_the_open_session(self, school, enrolled, cash_session):
        payment = _pay(enrolled, "300000")
        movement = CashMovement.objects.get(payment=payment)
        assert (movement.session, movement.direction, movement.source) == (cash_session, "in", "payment")
        assert _expected(cash_session) == Decimal("300000")

    def test_other_methods_do_not_touch_the_register(self, school, enrolled, cash_session):
        _pay(enrolled, "300000", method=Payment.Method.MOBILE_MONEY)
        assert not CashMovement.objects.exists()

    def test_cash_needs_an_open_register(self, school, enrolled):
        with pytest.raises(Exception, match="Open the cash register"):
            _pay(enrolled, "300000")
        assert not Payment.objects.exists()

    def test_with_several_open_registers_the_session_is_chosen(self, school, enrolled, cash_session):
        second = cash.open_session(CashRegister.objects.create(school=school, name="Caisse annexe"))
        with pytest.raises(Exception, match="Several registers are open"):
            _pay(enrolled, "1000", payment_date=None)
        payment = _pay(enrolled, "1000", payment_date=None, cash_session=second)
        assert CashMovement.objects.get(payment=payment).session == second

    def test_cash_is_not_dated_before_its_session_opened(self, school, enrolled, cash_session):
        with pytest.raises(Exception, match="on or after 2026-09-01"):
            _pay(enrolled, "1000", payment_date=date(2026, 8, 31))

    def test_reversing_a_cash_payment_takes_the_money_out(self, school, enrolled, cash_session):
        payment = _pay(enrolled, "300000")
        finance.reverse_payment(payment, reason="Wrong student")
        out = CashMovement.objects.get(source="payment_reversal")
        assert (out.session, out.direction, out.amount) == (cash_session, "out", Decimal("300000"))
        assert _expected(cash_session) == 0

    def test_after_closing_the_money_goes_back_through_the_next_session(self, school, enrolled, cash_session):
        payment = _pay(enrolled, "300000")
        cash.close_session(cash_session, counted=Decimal("300000"))
        with pytest.raises(Exception, match="Open the register"):
            finance.reverse_payment(payment, reason="Cheque bounced")
        assert Payment.objects.get().status == Payment.Status.POSTED
        tomorrow = cash.open_session(cash_session.register)
        finance.reverse_payment(payment, reason="Cheque bounced")
        assert CashMovement.objects.get(source="payment_reversal").session == tomorrow


@pytest.mark.django_db
class TestExpensesAndRefunds:
    def test_a_cash_expense_leaves_the_register(self, school, enrolled, cash_session):
        _pay(enrolled, "300000")
        expense = finance.record_expense(
            school,
            amount=Decimal("120000"),
            category="supplies",
            method="cash",
            description="Chalk and markers",
        )
        assert expense.number.startswith("EXP-")
        assert CashMovement.objects.get(expense=expense).direction == "out"
        assert _expected(cash_session) == Decimal("180000")
        finance.cancel_expense(expense, reason="Recorded twice")
        assert CashMovement.objects.get(source="expense_cancellation").amount == Decimal("120000")
        assert _expected(cash_session) == Decimal("300000")
        with pytest.raises(Exception, match="already cancelled"):
            finance.cancel_expense(expense, reason="Again")

    def test_an_expense_cannot_take_more_cash_than_the_register_holds(self, school, cash_session):
        with pytest.raises(Exception, match="Only 0"):
            finance.record_expense(
                school, amount=Decimal("5000"), category="other", method="cash", description="Taxi"
            )
        assert not Expense.objects.exists()
        paid_by_bank = finance.record_expense(
            school,
            amount=Decimal("5000000"),
            category="salaries",
            method="bank_transfer",
            description="September",
        )
        assert not paid_by_bank.cash_movements.exists()

    def test_a_refund_gives_back_credit_only(self, school, enrolled, cash_session):
        _pay(enrolled, "3500000")  # 250 000 more than everything due
        with pytest.raises(Exception, match="Only 250000 GNF of credit"):
            finance.record_refund(enrolled, amount=Decimal("300000"), method="cash", reason="Leaving")
        refund = finance.record_refund(enrolled, amount=Decimal("250000"), method="cash", reason="Leaving")
        assert student_credit(enrolled) == 0
        assert CashMovement.objects.get(refund=refund).direction == "out"
        assert _expected(cash_session) == Decimal("3250000")

    def test_a_payment_whose_credit_was_refunded_cannot_be_reversed(self, school, enrolled, cash_session):
        payment = _pay(enrolled, "3500000")
        refund = finance.record_refund(enrolled, amount=Decimal("250000"), method="cash", reason="Overpaid")
        with pytest.raises(Exception, match="Cancel the refund first"):
            finance.reverse_payment(payment, reason="Mistake")
        finance.cancel_refund(refund, reason="Parent kept it as an advance")
        assert student_credit(enrolled) == Decimal("250000")
        assert CashMovement.objects.get(source="refund_cancellation").amount == Decimal("250000")
        finance.reverse_payment(payment, reason="Mistake")
        assert student_credit(enrolled) == 0

    def test_cancelled_refund_credit_pays_new_invoices(self, school, year, tuition, enrolled, cash_session):
        _pay(enrolled, "3500000")
        refund = finance.record_refund(enrolled, amount=Decimal("250000"), method="cash", reason="Overpaid")
        extra = finance.create_manual_invoice(
            enrolled,
            academic_year=year,
            lines=[
                {
                    "category": tuition,
                    "description": "Uniform",
                    "due_date": date(2026, 10, 1),
                    "amount": 75000,
                }
            ],
        )
        assert with_balances(Invoice.objects.filter(pk=extra.pk)).get().amount_paid == 0
        finance.cancel_refund(refund, reason="Not collected")
        assert with_balances(Invoice.objects.filter(pk=extra.pk)).get().payment_status == "paid"
        assert student_credit(enrolled) == Decimal("175000")


@pytest.mark.django_db
class TestCashApi:
    def test_accountant_runs_a_day(self, school, enrolled, make_member, client_for):
        client = client_for(make_member(school, "accountant"), school)
        registers = client.get("/api/v1/cash-registers/").data
        assert [r["name"] for r in registers] == ["Caisse principale"]
        assert registers[0]["open_session"] is None

        opened = client.post(
            "/api/v1/cash-sessions/",
            {"register": registers[0]["id"], "opening_balance": "50000"},
            format="json",
        )
        assert opened.status_code == 201, opened.data
        session_id = opened.data["id"]
        paid = client.post(
            "/api/v1/payments/", {"student": enrolled.pk, "amount": "250000", "method": "cash"}, format="json"
        )
        assert paid.status_code == 201, paid.data
        assert paid.data["cash_session"] == {"id": session_id, "register_name": "Caisse principale"}
        deposit = client.post(
            f"/api/v1/cash-sessions/{session_id}/movements/",
            {"direction": "out", "amount": "200000", "description": "Deposit at the bank"},
            format="json",
        )
        assert deposit.status_code == 201, deposit.data
        spent = client.post(
            "/api/v1/expenses/",
            {
                "category": "transport",
                "amount": "30000",
                "method": "cash",
                "description": "Fuel",
                "payee": "Total",
            },
            format="json",
        )
        assert spent.status_code == 201, spent.data

        detail = client.get(f"/api/v1/cash-sessions/{session_id}/").data
        assert (detail["money_in"], detail["money_out"], detail["expected"]) == (
            "250000.00",
            "230000.00",
            "70000.00",
        )
        assert {(row["source"], row["total"]) for row in detail["by_source"]} == {
            ("payment", "250000.00"),
            ("manual", "200000.00"),
            ("expense", "30000.00"),
        }
        assert [m["source"] for m in detail["movements"]] == ["payment", "manual", "expense"]
        assert detail["movements"][0]["student"] == enrolled.pk

        closed = client.post(
            f"/api/v1/cash-sessions/{session_id}/close/", {"counted_closing": "70000"}, format="json"
        )
        assert closed.status_code == 200, closed.data
        assert (closed.data["status"], closed.data["difference"]) == ("closed", "0.00")
        journal = client.get(f"/api/v1/cash-sessions/{session_id}/journal/")
        assert journal.status_code == 200
        assert journal.content.startswith(b"%PDF")
        assert client.get("/api/v1/cash-sessions/", {"status": "closed"}).data["count"] == 1

    def test_who_may_use_the_register(self, school, make_member, client_for):
        session = cash.open_session(cash.default_register(school))
        for role in ("teacher", "admin_staff"):
            client = client_for(make_member(school, role), school)
            assert client.get("/api/v1/cash-sessions/").status_code == 403
            assert client.get("/api/v1/cash-registers/").status_code == 403
            assert (
                client.post(
                    f"/api/v1/cash-sessions/{session.pk}/close/", {"counted_closing": "0"}
                ).status_code
                == 403
            )
            assert client.get("/api/v1/expenses/").status_code == 403

    def test_sessions_are_never_edited_or_deleted(self, school, make_member, client_for):
        session = cash.open_session(cash.default_register(school))
        client = client_for(make_member(school, "director"), school)
        assert client.patch(f"/api/v1/cash-sessions/{session.pk}/", {"opening_balance": "1"}).status_code in (
            403,
            405,
        )
        assert client.delete(f"/api/v1/cash-sessions/{session.pk}/").status_code in (403, 405)

    def test_only_the_school_own_registers_and_sessions(
        self, school, other_school, enrolled, make_member, client_for, make_student
    ):
        session = cash.open_session(cash.default_register(school))
        outsider = client_for(make_member(other_school, "accountant"), other_school)
        assert outsider.get(f"/api/v1/cash-sessions/{session.pk}/").status_code == 404
        assert outsider.get(f"/api/v1/cash-sessions/{session.pk}/journal/").status_code == 404
        assert (
            outsider.post(
                "/api/v1/cash-sessions/", {"register": session.register_id}, format="json"
            ).status_code
            == 400
        )
        their_student = make_student("Fanta", "Camara", target_school=other_school)
        response = outsider.post(
            "/api/v1/payments/",
            {"student": their_student.pk, "amount": "1000", "method": "cash", "cash_session": session.pk},
            format="json",
        )
        assert response.status_code == 400
        assert "cash_session" in response.data["fields"]
        assert CashSession.objects.get().movements.count() == 0

    def test_refunds_through_the_api(self, school, enrolled, cash_session, make_member, client_for):
        _pay(enrolled, "3500000")
        client = client_for(make_member(school, "accountant"), school)
        response = client.post(
            "/api/v1/refunds/",
            {"student": enrolled.pk, "amount": "250000", "method": "cash", "reason": "Leaving the school"},
            format="json",
        )
        assert response.status_code == 201, response.data
        assert response.data["cash_session"]["id"] == cash_session.pk
        assert client.get(f"/api/v1/student-accounts/{enrolled.pk}/").data["credit"] == "0.00"
        cancelled = client.post(
            f"/api/v1/refunds/{response.data['id']}/cancel/", {"reason": "Typo"}, format="json"
        )
        assert cancelled.data["status"] == "cancelled"
        teacher = client_for(make_member(school, "teacher"), school)
        assert teacher.post("/api/v1/refunds/", {}, format="json").status_code == 403
