"""Cash register rules: one open session per register, cash moves only through an open session, and the
register never holds less than nothing."""

from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework.exceptions import ValidationError

from apps.audit import services as audit
from apps.finance.money import ZERO, to_money

from .models import CashMovement, CashRegister, CashSession

DEFAULT_REGISTER_NAMES = {"fr": "Caisse principale", "en": "Main cash register"}


def _user(request):
    user = getattr(request, "user", None)
    return user if user is not None and user.is_authenticated else None


def default_register(school) -> CashRegister:
    """The register a school starts with; created the first time it is needed."""
    register = CashRegister.objects.filter(school=school).order_by("id").first()
    if register is None:
        name = DEFAULT_REGISTER_NAMES.get(school.default_language, DEFAULT_REGISTER_NAMES["fr"])
        register = CashRegister.objects.create(school=school, name=name)
    return register


def totals(session: CashSession) -> dict[str, Decimal]:
    """Money in, money out and the cash expected in the register right now."""
    sums = session.movements.aggregate(
        money_in=Sum("amount", filter=Q(direction=CashMovement.Direction.IN)),
        money_out=Sum("amount", filter=Q(direction=CashMovement.Direction.OUT)),
    )
    money_in, money_out = sums["money_in"] or ZERO, sums["money_out"] or ZERO
    return {
        "money_in": money_in,
        "money_out": money_out,
        "expected": session.opening_balance + money_in - money_out,
    }


def last_counted(register: CashRegister) -> Decimal:
    """The cash counted when the register was last closed: what the next session starts with."""
    last = register.sessions.filter(status=CashSession.Status.CLOSED).order_by("-closed_at", "-id").first()
    return last.counted_closing if last and last.counted_closing is not None else ZERO


def _locked_open(session: CashSession) -> CashSession:
    session = CashSession.objects.select_for_update().select_related("register", "school").get(pk=session.pk)
    if session.status != CashSession.Status.OPEN:
        raise ValidationError({"cash_session": [_("This cash session is closed.")]})
    return session


@transaction.atomic
def open_session(
    register: CashRegister, *, opening_balance: Decimal | None = None, note: str = "", request=None
) -> CashSession:
    """Open a register. The float defaults to the cash counted at the last closing."""
    register = CashRegister.objects.select_for_update().get(pk=register.pk)
    if not register.is_active:
        raise ValidationError({"register": [_("This register is no longer in use.")]})
    if register.sessions.filter(status=CashSession.Status.OPEN).exists():
        raise ValidationError({"register": [_("This register is already open. Close it first.")]})
    currency = register.school.currency
    balance = last_counted(register) if opening_balance is None else to_money(opening_balance, currency)
    if balance < 0:
        raise ValidationError({"opening_balance": [_("The float cannot be negative.")]})
    session = CashSession.objects.create(
        school=register.school,
        register=register,
        opening_balance=balance,
        opening_note=note.strip(),
        created_by=_user(request),
    )
    audit.record(
        "open",
        request=request,
        school=register.school,
        instance=session,
        module="cash",
        summary=f"{register.name} opened with {balance} {currency}",
        new={"opening_balance": str(balance)},
    )
    return session


@transaction.atomic
def close_session(session: CashSession, *, counted: Decimal, note: str = "", request=None) -> CashSession:
    """Close with the cash counted. A difference with what was expected must be explained."""
    session = _locked_open(session)
    currency = session.school.currency
    counted = to_money(counted, currency)
    if counted < 0:
        raise ValidationError({"counted_closing": [_("The cash counted cannot be negative.")]})
    expected = totals(session)["expected"]
    difference = counted - expected
    note = note.strip()
    if difference and not note:
        raise ValidationError(
            {"closing_note": [_("Explain the difference between the cash counted and expected.")]}
        )
    session.status = CashSession.Status.CLOSED
    session.closed_at = timezone.now()
    session.closed_by = _user(request)
    session.expected_closing = expected
    session.counted_closing = counted
    session.difference = difference
    session.closing_note = note
    session.save()
    audit.record(
        "close",
        request=request,
        school=session.school,
        instance=session,
        module="cash",
        summary=f"{session.register.name} closed: counted {counted}, expected {expected} {currency}",
        new={"expected": str(expected), "counted": str(counted), "difference": str(difference)},
    )
    return session


@transaction.atomic
def session_for_cash(school, session: CashSession | None = None) -> CashSession:
    """The open session cash goes into: the one chosen, or the school's only open session."""
    if session is not None:
        if session.school_id != school.pk:
            raise ValidationError({"cash_session": [_("Choose a cash session of this school.")]})
        return _locked_open(session)
    open_sessions = list(CashSession.objects.filter(school=school, status=CashSession.Status.OPEN)[:2])
    if not open_sessions:
        raise ValidationError({"cash_session": [_("Open the cash register before recording cash.")]})
    if len(open_sessions) > 1:
        raise ValidationError(
            {"cash_session": [_("Several registers are open: choose the one the cash goes through.")]}
        )
    return _locked_open(open_sessions[0])


@transaction.atomic
def session_for_return(original: CashSession) -> CashSession:
    """Where cash goes back: the original session if still open, else its register's open one."""
    if original.status == CashSession.Status.OPEN:
        return _locked_open(original)
    current = original.register.sessions.filter(status=CashSession.Status.OPEN).first()
    if current is None:
        raise ValidationError(
            {
                "cash_session": [
                    _("Open the register “%(register)s” first: the cash goes back through it.")
                    % {"register": original.register.name}
                ]
            }
        )
    return _locked_open(current)


def check_cash_date(session: CashSession, day) -> None:
    """Cash is recorded on or after the day its session was opened, so the session journal stays true."""
    opened_on = timezone.localtime(session.opened_at).date()
    if day < opened_on:
        raise ValidationError(
            {
                "date": [
                    _("Cash going through this session must be dated on or after %(day)s.")
                    % {"day": opened_on}
                ]
            }
        )


@transaction.atomic
def record_movement(
    session: CashSession,
    *,
    direction: str,
    amount: Decimal,
    description: str,
    source: str = CashMovement.Source.MANUAL,
    payment=None,
    expense=None,
    refund=None,
    request=None,
) -> CashMovement:
    """Add cash to or take cash from an open session; money out can never exceed the cash expected."""
    session = _locked_open(session)
    currency = session.school.currency
    amount = to_money(amount, currency)
    if amount <= 0:
        raise ValidationError({"amount": [_("Enter an amount greater than zero.")]})
    if direction == CashMovement.Direction.OUT:
        available = to_money(totals(session)["expected"], currency)
        if amount > available:
            raise ValidationError(
                {
                    "amount": [
                        _("Only %(amount)s %(currency)s is in the register.")
                        % {"amount": available, "currency": currency}
                    ]
                }
            )
    movement = CashMovement.objects.create(
        school=session.school,
        session=session,
        direction=direction,
        source=source,
        amount=amount,
        description=description.strip()[:255],
        payment=payment,
        expense=expense,
        refund=refund,
        created_by=_user(request),
    )
    if source == CashMovement.Source.MANUAL:
        # Payments, expenses and refunds are audited by their own services.
        audit.record(
            "create",
            request=request,
            school=session.school,
            instance=movement,
            module="cash",
            summary=f"{direction.upper()} {amount} {currency} ({session.register}): {movement.description}",
            new={"direction": direction, "amount": str(amount), "description": movement.description},
        )
    return movement
