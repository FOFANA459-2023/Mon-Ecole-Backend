from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


def find_user_by_login(login: str):
    User = get_user_model()
    login = (login or "").strip()
    if not login:
        return None
    matches = list(User.objects.filter(Q(username__iexact=login) | Q(email__iexact=login))[:2])
    return matches[0] if len(matches) == 1 else None


class EmailOrUsernameBackend(ModelBackend):
    """Log in with either the username or the email address."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        login = username or kwargs.get("email")
        if not login or not password:
            return None
        user = find_user_by_login(login)
        if user is None:
            # Hash anyway so response time does not reveal whether the account exists.
            get_user_model()().set_password(password)
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
