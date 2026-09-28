from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class RequestContext:
    request_id: str = "-"
    ip: str | None = None
    user_agent: str = ""


_EMPTY = RequestContext()
_current: ContextVar[RequestContext | None] = ContextVar("request_context", default=None)


def get_request_context() -> RequestContext:
    return _current.get() or _EMPTY


def set_request_context(ctx: RequestContext):
    return _current.set(ctx)


def reset_request_context(token) -> None:
    _current.reset(token)
