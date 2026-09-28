import requests
from celery import shared_task

from .email import send_email


@shared_task(autoretry_for=(requests.RequestException,), retry_backoff=True, max_retries=5)
def send_email_task(to: str, subject: str, message: str) -> None:
    send_email(to=to, subject=subject, message=message)
