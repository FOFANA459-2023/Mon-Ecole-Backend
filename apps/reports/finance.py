"""Finance reports (ToR §12 and §20): payments, outstanding balances, cash register, expenses and the
financial summary. Each builder returns a `Report`; the renderers turn it into JSON, PDF, Excel or CSV.

Dates are grouped by calendar day in UTC, which is the local time of the schools served (Guinea, Liberia).
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Count, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from apps.academics.models import AcademicYear, ClassGroup
from apps.cashregister.models import CashMovement, CashRegister, CashSession
from apps.cashregister.selectors import with_totals
from apps.enrollments.models import Enrollment
from apps.finance.models import Expense, Invoice, Payment, PaymentAllocation, Refund
from apps.finance.money import ZERO
from apps.finance.selectors import with_balances
from apps.people.models import StudentGuardian

from .base import Column, Report, Section, period_label

LABELS: dict[str, dict] = {
    "fr": {
        "payments": "Rapport des paiements",
        "outstanding": "Rapport des soldes impayés",
        "cash": "Rapport de caisse",
        "expenses": "Rapport des dépenses",
        "summary": "Rapport financier",
        "receipt": "N° de reçu",
        "number": "N°",
        "date": "Date",
        "student": "Élève",
        "student_number": "Matricule",
        "class": "Classe",
        "method": "Mode",
        "reference": "Référence",
        "received_by": "Reçu par",
        "recorded_by": "Enregistré par",
        "amount": "Montant",
        "status": "Statut",
        "count": "Nombre",
        "total": "Total",
        "posted": "Enregistré",
        "reversed": "Annulé",
        "cancelled": "Annulée",
        "recorded": "Enregistrée",
        "all_payments": "Paiements",
        "by_method": "Par mode de paiement",
        "reversed_note": "Les paiements annulés sont listés mais ne comptent pas dans les totaux.",
        "guardian": "Parent / tuteur",
        "phone": "Téléphone",
        "invoiced": "Facturé",
        "paid": "Payé",
        "balance": "Reste à payer",
        "overdue": "En retard",
        "next_due": "Prochaine échéance",
        "balances_as_of": "Soldes au {date}",
        "students_owing": "Élèves ayant un solde impayé",
        "overdue_only": "retards seulement",
        "by_day": "Jour par jour",
        "sessions": "Sessions de caisse",
        "by_source": "Par nature de mouvement",
        "opened": "Ouverture",
        "register": "Caisse",
        "opened_by": "Ouverte par",
        "closed_by": "Clôturée par",
        "float": "Fonds",
        "money_in": "Entrées",
        "money_out": "Sorties",
        "expected": "Solde théorique",
        "counted": "Compté",
        "difference": "Écart",
        "open": "Ouverte",
        "closed": "Clôturée",
        "source": "Nature",
        "direction": "Sens",
        "in": "Entrée",
        "out": "Sortie",
        "category": "Catégorie",
        "description": "Libellé",
        "payee": "Payé à",
        "all_expenses": "Dépenses",
        "by_category": "Par catégorie",
        "cancelled_note": "Les dépenses annulées sont listées mais ne comptent pas dans les totaux.",
        "overview": "Synthèse",
        "item": "Poste",
        "received": "Paiements reçus",
        "refunds": "Remboursements",
        "spent": "Dépenses",
        "net": "Résultat (reçu − remboursé − dépensé)",
        "by_fee": "Paiements reçus par frais",
        "fee": "Frais",
        "advances": "Avances gardées en crédit",
        "by_month": "Mois par mois",
        "month": "Mois",
        "year_position": "Année scolaire {year}",
        "expected_revenue": "Recettes attendues (factures émises)",
        "collected": "Encaissé sur ces factures",
        "outstanding_total": "Reste à encaisser",
        "overdue_total": "Dont en retard",
        "collection_rate": "Taux de recouvrement",
        "students_invoiced": "Élèves facturés",
        "students_with_balance": "Élèves avec un solde impayé",
        "sources": {
            "payment": "Paiements",
            "payment_reversal": "Paiements annulés",
            "expense": "Dépenses",
            "expense_cancellation": "Dépenses annulées",
            "refund": "Remboursements",
            "refund_cancellation": "Remboursements annulés",
            "manual": "Autres mouvements",
        },
        "methods": {
            "cash": "Espèces",
            "mobile_money": "Mobile money",
            "bank_transfer": "Virement / versement",
            "cheque": "Chèque",
            "card": "Carte",
            "other": "Autre",
        },
        "categories": {
            "salaries": "Salaires et primes",
            "rent": "Loyer",
            "utilities": "Eau, électricité, internet",
            "supplies": "Fournitures",
            "maintenance": "Réparations et entretien",
            "transport": "Transport et carburant",
            "food": "Alimentation et cantine",
            "events": "Examens et événements",
            "taxes": "Impôts et taxes",
            "other": "Autre",
        },
        "months": [
            "janv.",
            "févr.",
            "mars",
            "avr.",
            "mai",
            "juin",
            "juil.",
            "août",
            "sept.",
            "oct.",
            "nov.",
            "déc.",
        ],
    },
    "en": {
        "payments": "Payments report",
        "outstanding": "Outstanding balances report",
        "cash": "Cash register report",
        "expenses": "Expenses report",
        "summary": "Financial report",
        "receipt": "Receipt no.",
        "number": "No.",
        "date": "Date",
        "student": "Student",
        "student_number": "Student no.",
        "class": "Class",
        "method": "Method",
        "reference": "Reference",
        "received_by": "Received by",
        "recorded_by": "Recorded by",
        "amount": "Amount",
        "status": "Status",
        "count": "Count",
        "total": "Total",
        "posted": "Recorded",
        "reversed": "Reversed",
        "cancelled": "Cancelled",
        "recorded": "Recorded",
        "all_payments": "Payments",
        "by_method": "By payment method",
        "reversed_note": "Reversed payments are listed but not counted in the totals.",
        "guardian": "Parent / guardian",
        "phone": "Phone",
        "invoiced": "Invoiced",
        "paid": "Paid",
        "balance": "Balance due",
        "overdue": "Overdue",
        "next_due": "Next due date",
        "balances_as_of": "Balances as of {date}",
        "students_owing": "Students with a balance due",
        "overdue_only": "overdue only",
        "by_day": "Day by day",
        "sessions": "Cash sessions",
        "by_source": "By kind of movement",
        "opened": "Opened",
        "register": "Register",
        "opened_by": "Opened by",
        "closed_by": "Closed by",
        "float": "Float",
        "money_in": "In",
        "money_out": "Out",
        "expected": "Expected",
        "counted": "Counted",
        "difference": "Difference",
        "open": "Open",
        "closed": "Closed",
        "source": "Kind",
        "direction": "Direction",
        "in": "In",
        "out": "Out",
        "category": "Category",
        "description": "Description",
        "payee": "Paid to",
        "all_expenses": "Expenses",
        "by_category": "By category",
        "cancelled_note": "Cancelled expenses are listed but not counted in the totals.",
        "overview": "Overview",
        "item": "Item",
        "received": "Payments received",
        "refunds": "Refunds",
        "spent": "Expenses",
        "net": "Result (received − refunded − spent)",
        "by_fee": "Payments received by fee",
        "fee": "Fee",
        "advances": "Advances kept as credit",
        "by_month": "Month by month",
        "month": "Month",
        "year_position": "School year {year}",
        "expected_revenue": "Expected revenue (invoices issued)",
        "collected": "Collected on these invoices",
        "outstanding_total": "Still to collect",
        "overdue_total": "Of which overdue",
        "collection_rate": "Collection rate",
        "students_invoiced": "Students invoiced",
        "students_with_balance": "Students with a balance due",
        "sources": {
            "payment": "Payments",
            "payment_reversal": "Payments reversed",
            "expense": "Expenses",
            "expense_cancellation": "Expenses cancelled",
            "refund": "Refunds",
            "refund_cancellation": "Refunds cancelled",
            "manual": "Other movements",
        },
        "methods": {
            "cash": "Cash",
            "mobile_money": "Mobile money",
            "bank_transfer": "Bank transfer / deposit",
            "cheque": "Cheque",
            "card": "Card",
            "other": "Other",
        },
        "categories": {
            "salaries": "Salaries and allowances",
            "rent": "Rent",
            "utilities": "Water, electricity, internet",
            "supplies": "Supplies",
            "maintenance": "Repairs and maintenance",
            "transport": "Transport and fuel",
            "food": "Food and canteen",
            "events": "Exams and events",
            "taxes": "Taxes and fees",
            "other": "Other",
        },
        "months": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    },
}


@dataclass
class Params:
    date_from: date
    date_to: date
    academic_year: AcademicYear | None = None
    class_group: ClassGroup | None = None
    register: CashRegister | None = None
    method: str = ""
    category: str = ""
    overdue_only: bool = False


def labels(language: str) -> dict:
    return LABELS["en" if language == "en" else "fr"]


def _money_sum(values) -> Decimal:
    return sum(values, ZERO)


def _name(user) -> str:
    return user.full_name if user else ""


def _class_students(class_group: ClassGroup):
    return Enrollment.objects.filter(class_group=class_group, status=Enrollment.Status.ACTIVE).values(
        "student_id"
    )


def _current_classes(student_ids) -> dict[int, str]:
    """Each student's class in their latest active enrolment."""
    enrollments = (
        Enrollment.objects.filter(student_id__in=student_ids, status=Enrollment.Status.ACTIVE)
        .select_related("class_group")
        .order_by("student_id", "-academic_year__start_date")
    )
    classes: dict[int, str] = {}
    for enrollment in enrollments:
        classes.setdefault(enrollment.student_id, enrollment.class_group.name)
    return classes


def _contacts(student_ids) -> dict[int, tuple[str, str]]:
    """Who to call about money: the financial contact, else the primary guardian, else any guardian."""
    links = (
        StudentGuardian.objects.filter(student_id__in=student_ids)
        .select_related("guardian")
        .order_by("student_id", "-is_financial_contact", "-is_primary", "id")
    )
    contacts: dict[int, tuple[str, str]] = {}
    for link in links:
        contacts.setdefault(link.student_id, (link.guardian.full_name, link.guardian.phone))
    return contacts


def _subtitle(L: dict, p: Params, language: str, *extra: str) -> str:
    parts = [period_label(p.date_from, p.date_to, language), *extra]
    if p.class_group:
        parts.append(p.class_group.name)
    if p.register:
        parts.append(p.register.name)
    if p.method:
        parts.append(L["methods"][p.method])
    if p.category:
        parts.append(L["categories"][p.category])
    return " · ".join(part for part in parts if part)


# --- Payments ----------------------------------------------------------------------------------------------


def payments_report(school, p: Params, language: str) -> Report:
    L = labels(language)
    queryset = Payment.objects.filter(school=school, date__range=(p.date_from, p.date_to)).select_related(
        "student", "created_by"
    )
    if p.method:
        queryset = queryset.filter(method=p.method)
    if p.class_group:
        queryset = queryset.filter(student__in=_class_students(p.class_group))
    payments = list(queryset.order_by("date", "number"))
    classes = _current_classes({payment.student_id for payment in payments})
    posted = [payment for payment in payments if payment.status == Payment.Status.POSTED]

    rows = [
        {
            "number": payment.number,
            "date": payment.date,
            "student": payment.student.full_name,
            "student_number": payment.student.student_number,
            "class": classes.get(payment.student_id, ""),
            "method": L["methods"][payment.method],
            "reference": payment.reference,
            "received_by": _name(payment.created_by),
            "amount": payment.amount,
            "status": L["posted"] if payment.status == Payment.Status.POSTED else L["reversed"],
        }
        for payment in payments
    ]
    by_method: dict[str, list[Decimal]] = defaultdict(list)
    for payment in posted:
        by_method[payment.method].append(payment.amount)
    return Report(
        key="payments",
        title=L["payments"],
        subtitle=_subtitle(L, p, language),
        sections=[
            Section(
                title=L["all_payments"],
                note=L["reversed_note"],
                columns=[
                    Column("number", L["receipt"]),
                    Column("date", L["date"], "date"),
                    Column("student", L["student"]),
                    Column("student_number", L["student_number"]),
                    Column("class", L["class"]),
                    Column("method", L["method"]),
                    Column("reference", L["reference"]),
                    Column("received_by", L["received_by"]),
                    Column("amount", L["amount"], "money"),
                    Column("status", L["status"]),
                ],
                rows=rows,
                totals={
                    "number": f"{L['total']} ({len(posted)})",
                    "amount": _money_sum(p.amount for p in posted),
                },
            ),
            Section(
                title=L["by_method"],
                columns=[
                    Column("method", L["method"]),
                    Column("count", L["count"], "number"),
                    Column("amount", L["amount"], "money"),
                ],
                rows=[
                    {"method": L["methods"][method], "count": len(amounts), "amount": _money_sum(amounts)}
                    for method, amounts in sorted(by_method.items(), key=lambda item: -_money_sum(item[1]))
                ],
                totals={
                    "method": L["total"],
                    "count": len(posted),
                    "amount": _money_sum(p.amount for p in posted),
                },
            ),
        ],
    )


# --- Outstanding balances ----------------------------------------------------------------------------------


def outstanding_report(school, p: Params, language: str) -> Report:
    """Every student who still owes money on issued invoices, and the guardian to call (as of today)."""
    L = labels(language)
    today = timezone.localdate()
    invoices = Invoice.objects.filter(school=school, status=Invoice.Status.ISSUED).select_related(
        "student", "enrollment__class_group"
    )
    if p.academic_year:
        invoices = invoices.filter(academic_year=p.academic_year)
    if p.class_group:
        invoices = invoices.filter(student__in=_class_students(p.class_group))

    students: dict[int, dict] = {}
    for invoice in with_balances(invoices, today):
        row = students.setdefault(
            invoice.student_id,
            {
                "student_number": invoice.student.student_number,
                "student": invoice.student.full_name,
                "last_name": invoice.student.last_name,
                "class": "",
                "invoiced": ZERO,
                "paid": ZERO,
                "balance": ZERO,
                "overdue": ZERO,
                "next_due": None,
            },
        )
        row["invoiced"] += invoice.total
        row["paid"] += invoice.amount_paid
        row["balance"] += invoice.balance
        row["overdue"] += invoice.overdue_amount
        if invoice.enrollment_id and not row["class"]:
            row["class"] = invoice.enrollment.class_group.name
        if (
            invoice.balance > 0
            and invoice.next_due_date
            and (row["next_due"] is None or invoice.next_due_date < row["next_due"])
        ):
            row["next_due"] = invoice.next_due_date

    owing = {
        student_id: row
        for student_id, row in students.items()
        if row["balance"] > 0 and (row["overdue"] > 0 or not p.overdue_only)
    }
    classes = _current_classes([sid for sid, row in owing.items() if not row["class"]])
    contacts = _contacts(owing.keys())
    rows = []
    for student_id, row in owing.items():
        guardian, phone = contacts.get(student_id, ("", ""))
        rows.append(
            {
                **row,
                "class": row["class"] or classes.get(student_id, ""),
                "guardian": guardian,
                "phone": phone,
            }
        )
    rows.sort(key=lambda row: (row["class"], row.pop("last_name"), row["student"]))

    return Report(
        key="outstanding",
        title=L["outstanding"],
        subtitle=" · ".join(
            part
            for part in [
                L["balances_as_of"].format(date=period_label(today, today, language)),
                p.academic_year.name if p.academic_year else "",
                p.class_group.name if p.class_group else "",
                L["overdue_only"] if p.overdue_only else "",
            ]
            if part
        ),
        sections=[
            Section(
                title=L["students_owing"],
                columns=[
                    Column("student_number", L["student_number"]),
                    Column("student", L["student"]),
                    Column("class", L["class"]),
                    Column("guardian", L["guardian"]),
                    Column("phone", L["phone"]),
                    Column("invoiced", L["invoiced"], "money"),
                    Column("paid", L["paid"], "money"),
                    Column("balance", L["balance"], "money"),
                    Column("overdue", L["overdue"], "money"),
                    Column("next_due", L["next_due"], "date"),
                ],
                rows=rows,
                totals={
                    "student_number": f"{L['total']} ({len(rows)})",
                    **{
                        key: _money_sum(row[key] for row in rows)
                        for key in ("invoiced", "paid", "balance", "overdue")
                    },
                },
            )
        ],
    )


# --- Cash register -----------------------------------------------------------------------------------------


def cash_report(school, p: Params, language: str) -> Report:
    """Daily, weekly or monthly cash: sessions opened in the period, day by day and by kind of movement."""
    L = labels(language)
    queryset = CashSession.objects.filter(
        school=school, opened_at__date__range=(p.date_from, p.date_to)
    ).select_related("register", "created_by", "closed_by")
    if p.register:
        queryset = queryset.filter(register=p.register)
    sessions = list(with_totals(queryset).order_by("opened_at", "id"))

    session_rows = [
        {
            "opened": session.opened_at,
            "register": session.register.name,
            "opened_by": _name(session.created_by),
            "float": session.opening_balance,
            "money_in": session.money_in,
            "money_out": session.money_out,
            "expected": session.expected_closing
            if session.expected_closing is not None
            else session.expected,
            "counted": session.counted_closing,
            "difference": session.difference,
            "status": L["open"] if session.status == CashSession.Status.OPEN else L["closed"],
            "closed_by": _name(session.closed_by),
        }
        for session in sessions
    ]
    days: dict[date, dict] = {}
    for session, row in zip(sessions, session_rows, strict=True):
        day = days.setdefault(
            timezone.localtime(session.opened_at).date(),
            {"float": ZERO, "money_in": ZERO, "money_out": ZERO, "counted": ZERO, "difference": ZERO},
        )
        day["float"] += row["float"]
        day["money_in"] += row["money_in"]
        day["money_out"] += row["money_out"]
        day["counted"] += row["counted"] or ZERO
        day["difference"] += row["difference"] or ZERO
    movements = (
        CashMovement.objects.filter(session__in=[session.pk for session in sessions])
        .values("source", "direction")
        .annotate(count=Count("id"), total=Sum("amount"))
        .order_by("direction", "source")
    )
    money_in = _money_sum(row["money_in"] for row in session_rows)
    money_out = _money_sum(row["money_out"] for row in session_rows)
    difference = _money_sum(row["difference"] or ZERO for row in session_rows)
    return Report(
        key="cash",
        title=L["cash"],
        subtitle=_subtitle(L, p, language),
        sections=[
            Section(
                title=L["by_day"],
                columns=[
                    Column("date", L["date"], "date"),
                    Column("float", L["float"], "money"),
                    Column("money_in", L["money_in"], "money"),
                    Column("money_out", L["money_out"], "money"),
                    Column("counted", L["counted"], "money"),
                    Column("difference", L["difference"], "money"),
                ],
                rows=[{"date": day, **values} for day, values in sorted(days.items())],
                totals={
                    "date": L["total"],
                    "money_in": money_in,
                    "money_out": money_out,
                    "difference": difference,
                },
            ),
            Section(
                title=L["sessions"],
                columns=[
                    Column("opened", L["opened"], "datetime"),
                    Column("register", L["register"]),
                    Column("opened_by", L["opened_by"]),
                    Column("float", L["float"], "money"),
                    Column("money_in", L["money_in"], "money"),
                    Column("money_out", L["money_out"], "money"),
                    Column("expected", L["expected"], "money"),
                    Column("counted", L["counted"], "money"),
                    Column("difference", L["difference"], "money"),
                    Column("status", L["status"]),
                    Column("closed_by", L["closed_by"]),
                ],
                rows=session_rows,
                totals={
                    "opened": f"{L['total']} ({len(session_rows)})",
                    "money_in": money_in,
                    "money_out": money_out,
                    "difference": difference,
                },
            ),
            Section(
                title=L["by_source"],
                columns=[
                    Column("source", L["source"]),
                    Column("direction", L["direction"]),
                    Column("count", L["count"], "number"),
                    Column("amount", L["amount"], "money"),
                ],
                rows=[
                    {
                        "source": L["sources"][row["source"]],
                        "direction": L[row["direction"]],
                        "count": row["count"],
                        "amount": row["total"],
                    }
                    for row in movements
                ],
            ),
        ],
    )


# --- Expenses ----------------------------------------------------------------------------------------------


def expenses_report(school, p: Params, language: str) -> Report:
    L = labels(language)
    queryset = Expense.objects.filter(school=school, date__range=(p.date_from, p.date_to)).select_related(
        "created_by"
    )
    if p.category:
        queryset = queryset.filter(category=p.category)
    if p.method:
        queryset = queryset.filter(method=p.method)
    expenses = list(queryset.order_by("date", "number"))
    recorded = [expense for expense in expenses if expense.status == Expense.Status.RECORDED]
    by_category: dict[str, list[Decimal]] = defaultdict(list)
    for expense in recorded:
        by_category[expense.category].append(expense.amount)
    total = _money_sum(expense.amount for expense in recorded)
    return Report(
        key="expenses",
        title=L["expenses"],
        subtitle=_subtitle(L, p, language),
        sections=[
            Section(
                title=L["all_expenses"],
                note=L["cancelled_note"],
                columns=[
                    Column("number", L["number"]),
                    Column("date", L["date"], "date"),
                    Column("category", L["category"]),
                    Column("description", L["description"]),
                    Column("payee", L["payee"]),
                    Column("method", L["method"]),
                    Column("reference", L["reference"]),
                    Column("recorded_by", L["recorded_by"]),
                    Column("amount", L["amount"], "money"),
                    Column("status", L["status"]),
                ],
                rows=[
                    {
                        "number": expense.number,
                        "date": expense.date,
                        "category": L["categories"][expense.category],
                        "description": expense.description,
                        "payee": expense.payee,
                        "method": L["methods"][expense.method],
                        "reference": expense.reference,
                        "recorded_by": _name(expense.created_by),
                        "amount": expense.amount,
                        "status": L["recorded"]
                        if expense.status == Expense.Status.RECORDED
                        else L["cancelled"],
                    }
                    for expense in expenses
                ],
                totals={"number": f"{L['total']} ({len(recorded)})", "amount": total},
            ),
            Section(
                title=L["by_category"],
                columns=[
                    Column("category", L["category"]),
                    Column("count", L["count"], "number"),
                    Column("amount", L["amount"], "money"),
                ],
                rows=[
                    {
                        "category": L["categories"][category],
                        "count": len(amounts),
                        "amount": _money_sum(amounts),
                    }
                    for category, amounts in sorted(
                        by_category.items(), key=lambda item: -_money_sum(item[1])
                    )
                ],
                totals={"category": L["total"], "count": len(recorded), "amount": total},
            ),
        ],
    )


# --- Financial summary -------------------------------------------------------------------------------------


def _months(date_from: date, date_to: date) -> list[date]:
    months, current = [], date_from.replace(day=1)
    while current <= date_to:
        months.append(current)
        current = (
            current.replace(year=current.year + 1, month=1)
            if current.month == 12
            else current.replace(month=current.month + 1)
        )
    return months


def _by_month(queryset) -> dict[date, Decimal]:
    rows = queryset.annotate(month=TruncMonth("date")).values("month").annotate(total=Sum("amount"))
    return {row["month"]: row["total"] for row in rows}


def summary_report(school, p: Params, language: str) -> Report:
    """Money received, refunded and spent over the period, and where the school year stands."""
    L = labels(language)
    period = {"school": school, "date__range": (p.date_from, p.date_to)}
    payments = Payment.objects.filter(**period, status=Payment.Status.POSTED)
    refunds = Refund.objects.filter(**period, status=Refund.Status.POSTED)
    expenses = Expense.objects.filter(**period, status=Expense.Status.RECORDED)

    received = payments.aggregate(s=Sum("amount"))["s"] or ZERO
    refunded = refunds.aggregate(s=Sum("amount"))["s"] or ZERO
    spent = expenses.aggregate(s=Sum("amount"))["s"] or ZERO
    by_method = payments.values("method").annotate(count=Count("id"), total=Sum("amount")).order_by("-total")
    by_fee = (
        PaymentAllocation.objects.filter(payment__in=payments)
        .values("invoice_line__category__name")
        .annotate(total=Sum("amount"))
        .order_by("-total")
    )
    allocated = _money_sum(row["total"] for row in by_fee)
    by_category = (
        expenses.values("category").annotate(count=Count("id"), total=Sum("amount")).order_by("-total")
    )

    months_received, months_refunded, months_spent = (
        _by_month(payments),
        _by_month(refunds),
        _by_month(expenses),
    )
    month_rows = []
    for month in _months(p.date_from, p.date_to):
        r, f, s = (
            months_received.get(month, ZERO),
            months_refunded.get(month, ZERO),
            months_spent.get(month, ZERO),
        )
        month_rows.append(
            {
                "month": f"{L['months'][month.month - 1]} {month.year}",
                "received": r,
                "refunds": f,
                "spent": s,
                "net": r - f - s,
            }
        )

    sections = [
        Section(
            title=L["overview"],
            columns=[Column("item", L["item"]), Column("amount", L["amount"], "money")],
            rows=[
                {"item": L["received"], "amount": received},
                {"item": L["refunds"], "amount": -refunded},
                {"item": L["spent"], "amount": -spent},
            ],
            totals={"item": L["net"], "amount": received - refunded - spent},
        ),
        Section(
            title=L["by_method"],
            columns=[
                Column("method", L["method"]),
                Column("count", L["count"], "number"),
                Column("amount", L["amount"], "money"),
            ],
            rows=[
                {"method": L["methods"][row["method"]], "count": row["count"], "amount": row["total"]}
                for row in by_method
            ],
            totals={"method": L["total"], "amount": received},
        ),
        Section(
            title=L["by_fee"],
            columns=[Column("fee", L["fee"]), Column("amount", L["amount"], "money")],
            rows=[{"fee": row["invoice_line__category__name"], "amount": row["total"]} for row in by_fee]
            + ([{"fee": L["advances"], "amount": received - allocated}] if received > allocated else []),
            totals={"fee": L["total"], "amount": received},
        ),
        Section(
            title=L["by_category"],
            columns=[
                Column("category", L["category"]),
                Column("count", L["count"], "number"),
                Column("amount", L["amount"], "money"),
            ],
            rows=[
                {"category": L["categories"][row["category"]], "count": row["count"], "amount": row["total"]}
                for row in by_category
            ],
            totals={"category": L["total"], "amount": spent},
        ),
        Section(
            title=L["by_month"],
            columns=[
                Column("month", L["month"]),
                Column("received", L["received"], "money"),
                Column("refunds", L["refunds"], "money"),
                Column("spent", L["spent"], "money"),
                Column("net", L["net"], "money"),
            ],
            rows=month_rows,
            totals={
                "month": L["total"],
                "received": received,
                "refunds": refunded,
                "spent": spent,
                "net": received - refunded - spent,
            },
        ),
    ]
    if p.academic_year:
        invoices = list(
            with_balances(
                Invoice.objects.filter(
                    school=school, academic_year=p.academic_year, status=Invoice.Status.ISSUED
                )
            )
        )
        expected = _money_sum(invoice.total for invoice in invoices)
        collected = _money_sum(invoice.amount_paid for invoice in invoices)
        owing: dict[int, Decimal] = defaultdict(Decimal)
        for invoice in invoices:
            owing[invoice.student_id] += invoice.balance
        rate = (collected * 100 / expected).quantize(Decimal("0.1")) if expected else ZERO
        sections.append(
            Section(
                title=L["year_position"].format(year=p.academic_year.name),
                # Each row says what its value is: an amount, a percentage or a count.
                columns=[Column("item", L["item"]), Column("value", "", "auto")],
                rows=[
                    {"item": L["expected_revenue"], "value": expected, "kind": "money"},
                    {"item": L["collected"], "value": collected, "kind": "money"},
                    {
                        "item": L["outstanding_total"],
                        "value": _money_sum(i.balance for i in invoices),
                        "kind": "money",
                    },
                    {
                        "item": L["overdue_total"],
                        "value": _money_sum(i.overdue_amount for i in invoices),
                        "kind": "money",
                    },
                    {"item": L["collection_rate"], "value": rate, "kind": "percent"},
                    {"item": L["students_invoiced"], "value": len(owing), "kind": "number"},
                    {
                        "item": L["students_with_balance"],
                        "value": sum(1 for balance in owing.values() if balance > 0),
                        "kind": "number",
                    },
                ],
            )
        )
    return Report(
        key="summary",
        title=L["summary"],
        subtitle=_subtitle(L, p, language),
        sections=sections,
        landscape=False,
    )


BUILDERS = {
    "payments": payments_report,
    "outstanding": outstanding_report,
    "cash": cash_report,
    "expenses": expenses_report,
    "summary": summary_report,
}
