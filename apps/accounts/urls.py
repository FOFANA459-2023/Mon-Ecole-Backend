from django.urls import path
from rest_framework.routers import SimpleRouter

from . import api

router = SimpleRouter()
router.register("roles", api.RoleViewSet, basename="role")
router.register("users", api.MemberViewSet, basename="member")

urlpatterns = [
    path("auth/login/", api.LoginView.as_view(), name="auth-login"),
    path("auth/refresh/", api.RefreshView.as_view(), name="auth-refresh"),
    path("auth/logout/", api.LogoutView.as_view(), name="auth-logout"),
    path("auth/password/change/", api.ChangePasswordView.as_view(), name="auth-password-change"),
    path("auth/password/reset/", api.PasswordResetRequestView.as_view(), name="auth-password-reset"),
    path(
        "auth/password/reset/confirm/",
        api.PasswordResetConfirmView.as_view(),
        name="auth-password-reset-confirm",
    ),
    path("me/", api.MeView.as_view(), name="me"),
    path("permissions/", api.PermissionRegistryView.as_view(), name="permission-registry"),
    *router.urls,
]
