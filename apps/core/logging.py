import logging

from .context import get_request_context


class RequestContextFilter(logging.Filter):
    def filter(self, record):
        record.request_id = get_request_context().request_id
        return True
