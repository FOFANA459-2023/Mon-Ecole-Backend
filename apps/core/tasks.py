import requests
from celery import shared_task

from .email import send_email


@shared_task(autoretry_for=(requests.RequestException,), retry_backoff=True, max_retries=5)
def send_email_task(to: str, subject: str, message: str) -> None:
    send_email(to=to, subject=subject, message=message)


@shared_task
def run_job(job_id: int) -> None:
    from .jobs import run

    run(job_id)


@shared_task
def purge_job_results() -> int:
    from .jobs import purge_old_results

    return purge_old_results()
