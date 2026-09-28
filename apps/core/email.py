import requests
from django.conf import settings
from django.core.mail import send_mail

EMAILJS_URL = "https://api.emailjs.com/api/v1.0/email/send"


def emailjs_configured() -> bool:
    return all(
        [
            settings.EMAILJS_SERVICE_ID,
            settings.EMAILJS_TEMPLATE_ID,
            settings.EMAILJS_PUBLIC_KEY,
            settings.EMAILJS_PRIVATE_KEY,
        ]
    )


def send_email(*, to: str, subject: str, message: str) -> None:
    """Send one email through EmailJS in production, or Django's mail backend locally.

    The EmailJS template must use the variables {{to_email}}, {{subject}} and {{message}}.
    """
    if emailjs_configured():
        response = requests.post(
            EMAILJS_URL,
            json={
                "service_id": settings.EMAILJS_SERVICE_ID,
                "template_id": settings.EMAILJS_TEMPLATE_ID,
                "user_id": settings.EMAILJS_PUBLIC_KEY,
                "accessToken": settings.EMAILJS_PRIVATE_KEY,
                "template_params": {"to_email": to, "subject": subject, "message": message},
            },
            timeout=10,
        )
        response.raise_for_status()
        return
    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [to])
