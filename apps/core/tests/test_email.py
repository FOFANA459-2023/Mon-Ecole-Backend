from unittest import mock

import pytest
import requests
from django.core import mail

from apps.core.email import EMAILJS_URL, send_email
from apps.core.tasks import send_email_task


def test_uses_django_mail_when_emailjs_is_not_configured():
    send_email(to="a@test.org", subject="Hello", message="Body")
    assert mail.outbox[0].subject == "Hello" and mail.outbox[0].to == ["a@test.org"]


@pytest.fixture
def emailjs(settings):
    settings.EMAILJS_SERVICE_ID = "svc"
    settings.EMAILJS_TEMPLATE_ID = "tpl"
    settings.EMAILJS_PUBLIC_KEY = "pub"
    settings.EMAILJS_PRIVATE_KEY = "priv"


def test_sends_through_emailjs_with_a_timeout(emailjs):
    with mock.patch("apps.core.email.requests.post") as post:
        send_email(to="a@test.org", subject="Hello", message="Body")
    url, kwargs = post.call_args.args[0], post.call_args.kwargs
    assert url == EMAILJS_URL
    assert kwargs["timeout"] <= 30
    assert kwargs["json"]["template_params"] == {
        "to_email": "a@test.org",
        "subject": "Hello",
        "message": "Body",
    }
    assert not mail.outbox


def test_emailjs_errors_are_raised_so_the_task_retries(emailjs):
    response = mock.Mock()
    response.raise_for_status.side_effect = requests.HTTPError("503")
    with (
        mock.patch("apps.core.email.requests.post", return_value=response),
        pytest.raises(requests.HTTPError),
    ):
        send_email(to="a@test.org", subject="Hello", message="Body")
    assert requests.RequestException in send_email_task.autoretry_for
