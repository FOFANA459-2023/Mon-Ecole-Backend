import re
import uuid

from django.conf import settings

from .context import RequestContext, reset_request_context, set_request_context

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def client_ip(request) -> str | None:
    if settings.CLIENT_IP_META_KEY:
        value = request.META.get(settings.CLIENT_IP_META_KEY, "")
        if value:
            return value.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


class HealthCheckMiddleware:
    """Answer /healthz before host validation: load-balancer probes use the task IP as Host."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/healthz":
            from .views import healthz

            return healthz(request)
        return self.get_response(request)


class RequestContextMiddleware:
    """Attach a request id, client IP and user agent to the current context for logging and audit."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        token = set_request_context(
            RequestContext(
                request_id=request_id,
                ip=client_ip(request),
                user_agent=request.headers.get("User-Agent", "")[:255],
            )
        )
        try:
            response = self.get_response(request)
        finally:
            reset_request_context(token)
        response["X-Request-ID"] = request_id
        return response
