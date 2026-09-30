from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import update_last_login
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import (
    AuthenticationFailed,
    NotAuthenticated,
    PermissionDenied,
    ValidationError,
)
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.tokens import RefreshToken

from apps.audit import services as audit
from apps.core.permissions import HasSchoolPermission

from . import services
from .backends import find_user_by_login
from .models import Membership, Role
from .permissions_registry import MODULES
from .serializers import (
    ChangePasswordSerializer,
    LoginSerializer,
    MemberCreateSerializer,
    MemberSerializer,
    MemberUpdateSerializer,
    MeUpdateSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RoleSerializer,
    VerifyEmailSerializer,
    me_payload,
)

User = get_user_model()


# --- refresh-token cookie helpers -------------------------------------------------------------


def _set_refresh_cookie(response, refresh: RefreshToken) -> None:
    response.set_cookie(
        settings.REFRESH_COOKIE_NAME,
        str(refresh),
        max_age=int(jwt_settings.REFRESH_TOKEN_LIFETIME.total_seconds()),
        httponly=True,
        secure=settings.REFRESH_COOKIE_SECURE,
        samesite=settings.REFRESH_COOKIE_SAMESITE,
        path=settings.REFRESH_COOKIE_PATH,
        domain=settings.REFRESH_COOKIE_DOMAIN,
    )


def _clear_refresh_cookie(response) -> None:
    response.delete_cookie(
        settings.REFRESH_COOKIE_NAME,
        path=settings.REFRESH_COOKIE_PATH,
        domain=settings.REFRESH_COOKIE_DOMAIN,
        samesite=settings.REFRESH_COOKIE_SAMESITE,
    )


def _check_origin(request) -> None:
    """Cookie-authenticated endpoints only accept browser requests from our own frontend."""
    origin = request.headers.get("Origin")
    if not origin:
        return
    allowed = set(settings.CORS_ALLOWED_ORIGINS) | {f"{request.scheme}://{request.get_host()}"}
    if origin not in allowed:
        raise PermissionDenied(_("Origin not allowed."))


def _session_response(user, request, *, status_code=status.HTTP_200_OK) -> Response:
    refresh = RefreshToken.for_user(user)
    response = Response(
        {"access": str(refresh.access_token), "user": me_payload(user, request)}, status=status_code
    )
    _set_refresh_cookie(response, refresh)
    return response


def _audit_for_user_schools(action: str, user, *, summary: str) -> None:
    schools = [m.school for m in user.memberships.filter(is_active=True).select_related("school")]
    for school in schools or [None]:
        audit.record(
            action,
            school=school,
            user=user,
            module="auth",
            entity_type="accounts.user",
            entity_id=user.pk,
            summary=summary,
        )


# --- authentication ---------------------------------------------------------------------------


class PublicAuthView(APIView):
    """Endpoints reached without an access token. Keeps 401 (not 403) for failed authentication."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get_authenticate_header(self, request):
        return 'Bearer realm="api"'


class LoginView(PublicAuthView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    @extend_schema(request=LoginSerializer, responses={200: dict})
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        login = serializer.validated_data["login"]
        user = authenticate(request, username=login, password=serializer.validated_data["password"])
        if user is None:
            known = find_user_by_login(login)
            if known is not None:
                _audit_for_user_schools("login_failed", known, summary="Failed login (wrong password)")
            else:
                audit.record(
                    "login_failed", module="auth", summary=f"Failed login for unknown account '{login[:80]}'"
                )
            raise AuthenticationFailed(_("Incorrect login or password."), code="invalid_credentials")
        # Only reached with the right password, so these answers never reveal whether an account exists.
        if user.email_verified_at is None:
            raise PermissionDenied(
                _("Confirm your email address first, with the link in the invitation email."),
                code="email_not_verified",
            )
        if (
            user.must_change_password
            and user.invitation_expires_at
            and user.invitation_expires_at < timezone.now()
        ):
            raise PermissionDenied(
                _("Your temporary password has expired. Ask your school to send the invitation again."),
                code="invitation_expired",
            )
        update_last_login(None, user)
        _audit_for_user_schools("login", user, summary="Signed in")
        return _session_response(user, request)


class RefreshView(PublicAuthView):
    @extend_schema(request=None, responses={200: dict})
    def post(self, request):
        _check_origin(request)
        raw = request.COOKIES.get(settings.REFRESH_COOKIE_NAME)
        if not raw:
            raise NotAuthenticated(_("Your session has ended. Please sign in again."), code="session_expired")
        try:
            old = RefreshToken(raw)
            user = User.objects.filter(pk=old[jwt_settings.USER_ID_CLAIM], is_active=True).first()
            if user is None:
                raise TokenError("inactive user")
            old.blacklist()
        except TokenError:
            response = Response(
                {
                    "code": "session_expired",
                    "message": str(_("Your session has ended. Please sign in again.")),
                    "fields": {},
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )
            _clear_refresh_cookie(response)
            return response
        refresh = RefreshToken.for_user(user)
        response = Response({"access": str(refresh.access_token)})
        _set_refresh_cookie(response, refresh)
        return response


class LogoutView(PublicAuthView):
    @extend_schema(request=None, responses={204: None})
    def post(self, request):
        _check_origin(request)
        raw = request.COOKIES.get(settings.REFRESH_COOKIE_NAME)
        if raw:
            try:
                token = RefreshToken(raw)
                user = User.objects.filter(pk=token[jwt_settings.USER_ID_CLAIM]).first()
                token.blacklist()
                if user is not None:
                    _audit_for_user_schools("logout", user, summary="Signed out")
            except TokenError:
                pass
        response = Response(status=status.HTTP_204_NO_CONTENT)
        _clear_refresh_cookie(response)
        return response


class MeView(APIView):
    @extend_schema(responses={200: dict})
    def get(self, request):
        return Response(me_payload(request.user, request))

    @extend_schema(request=MeUpdateSerializer, responses={200: dict})
    def patch(self, request):
        serializer = MeUpdateSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(me_payload(request.user, request))


class ChangePasswordView(APIView):
    @extend_schema(request=ChangePasswordSerializer, responses={200: dict})
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = request.user
        user.set_password(serializer.validated_data["new_password"])
        user.must_change_password = False
        user.invitation_expires_at = None
        user.save(update_fields=["password", "must_change_password", "invitation_expires_at"])
        services.revoke_refresh_tokens(user)
        _audit_for_user_schools("password_change", user, summary="Password changed")
        return _session_response(user, request)


class PasswordResetRequestView(PublicAuthView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    @extend_schema(request=PasswordResetRequestSerializer, responses={204: None})
    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(email__iexact=serializer.validated_data["email"], is_active=True).first()
        if user is not None:
            services.send_password_email(user)
            _audit_for_user_schools("password_reset_request", user, summary="Password reset requested")
        # Same answer whether or not the account exists.
        return Response(status=status.HTTP_204_NO_CONTENT)


class PasswordResetConfirmView(PublicAuthView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    @extend_schema(request=PasswordResetConfirmSerializer, responses={204: None})
    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        invalid = ValidationError({"token": [_("This link is invalid or has expired.")]})
        try:
            user = User.objects.get(pk=force_str(urlsafe_base64_decode(data["uid"])), is_active=True)
        except (User.DoesNotExist, ValueError, TypeError, OverflowError):
            raise invalid from None
        if not default_token_generator.check_token(user, data["token"]):
            raise invalid
        try:
            validate_password(data["new_password"], user)
        except DjangoValidationError as exc:
            raise ValidationError({"new_password": list(exc.messages)}) from None
        user.set_password(data["new_password"])
        user.must_change_password = False
        user.invitation_expires_at = None
        # The link arrived by email, so the address is confirmed too.
        user.email_verified_at = user.email_verified_at or timezone.now()
        user.save(
            update_fields=["password", "must_change_password", "invitation_expires_at", "email_verified_at"]
        )
        services.revoke_refresh_tokens(user)
        _audit_for_user_schools("password_reset", user, summary="Password reset completed")
        return Response(status=status.HTTP_204_NO_CONTENT)


class VerifyEmailView(PublicAuthView):
    """The link in an invitation email: confirms the address, then the person signs in with the temporary
    password and chooses their own."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    @extend_schema(request=VerifyEmailSerializer, responses={200: dict})
    def post(self, request):
        serializer = VerifyEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = services.user_for_verification_token(serializer.validated_data["token"])
        if user is None:
            message = _("This link is invalid or has expired. Ask your school to send the invitation again.")
            raise ValidationError({"token": [message]})
        if user.email_verified_at is None:
            user.email_verified_at = timezone.now()
            user.save(update_fields=["email_verified_at"])
            _audit_for_user_schools("email_verified", user, summary="Email address confirmed")
        return Response({"email": user.email})


# --- users, roles and permissions (per school) ------------------------------------------------


class PermissionRegistryView(APIView):
    permission_classes = [IsAuthenticated, HasSchoolPermission]
    required_permissions = {"get": ["users.manage"]}

    @extend_schema(responses={200: dict})
    def get(self, request):
        return Response(
            [
                {
                    "module": module,
                    "label": label,
                    "permissions": [{"code": c, "label": lbl} for c, lbl in perms],
                }
                for module, label, perms in MODULES
            ]
        )


class RoleViewSet(viewsets.ModelViewSet):
    serializer_class = RoleSerializer
    permission_classes = [IsAuthenticated, HasSchoolPermission]
    required_permissions = {"*": ["users.manage"]}
    pagination_class = None
    http_method_names = ["get", "post", "patch", "delete"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Role.objects.none()
        # Built-in roles first, in order of authority; then the school's own roles.
        return (
            Role.objects.filter(school=self.request.school)
            .annotate(member_count=Count("memberships", filter=Q(memberships__is_active=True)))
            .order_by("-is_system", "id")
        )

    def perform_create(self, serializer):
        serializer.instance = services.save_role(
            self.request.school, role=None, data=dict(serializer.validated_data), request=self.request
        )

    def perform_update(self, serializer):
        serializer.instance = services.save_role(
            self.request.school,
            role=serializer.instance,
            data=dict(serializer.validated_data),
            request=self.request,
        )

    def perform_destroy(self, instance):
        services.delete_role(instance, request=self.request)


class MemberViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """People who can sign in to the current school, with their roles."""

    serializer_class = MemberSerializer
    permission_classes = [IsAuthenticated, HasSchoolPermission]
    required_permissions = {"*": ["users.manage"]}
    filterset_fields = ["is_active", "roles"]
    search_fields = ["user__first_name", "user__last_name", "user__email", "user__username", "user__phone"]
    ordering_fields = ["user__last_name", "created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Membership.objects.none()
        return (
            Membership.objects.filter(school=self.request.school)
            .select_related("user")
            .prefetch_related("roles")
            .distinct()
        )

    @extend_schema(request=MemberCreateSerializer, responses={201: MemberSerializer})
    def create(self, request):
        serializer = MemberCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        membership = services.add_member(request.school, roles=data.pop("role_ids"), request=request, **data)
        return Response(MemberSerializer(membership).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=MemberUpdateSerializer, responses={200: MemberSerializer})
    def partial_update(self, request, pk=None):
        membership = self.get_object()
        serializer = MemberUpdateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        membership = services.update_member(
            membership,
            roles=data.pop("role_ids", None),
            is_active=data.pop("is_active", None),
            user_data=data,
            request=request,
        )
        return Response(MemberSerializer(membership).data)

    @extend_schema(request=None, responses={204: None})
    @action(detail=True, methods=["post"], url_path="send-invite")
    def send_invite(self, request, pk=None):
        services.resend_invitation(self.get_object(), request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)
