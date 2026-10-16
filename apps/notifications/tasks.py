from celery import shared_task


@shared_task
def send_outbound_email(email_id: int) -> None:
    """Send one logged email; a failure is recorded on the email (status "failed")."""
    from .services import send

    send(email_id)


@shared_task
def dispatch_announcements() -> int:
    """Every few minutes: notify the audience of announcements whose publication time has come."""
    from .events import dispatch_due_announcements

    return dispatch_due_announcements()


@shared_task
def morning_round() -> None:
    """Every morning: exams coming up, payments due soon or overdue."""
    from .events import morning_round as run

    run()
