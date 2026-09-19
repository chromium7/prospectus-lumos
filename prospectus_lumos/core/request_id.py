"""Correlation ID shared between the request/response cycle and log records."""

import re
import uuid
from contextvars import ContextVar
from logging import Filter, LogRecord
from typing import Optional

REQUEST_ID_HEADER = "X-Request-ID"

# Accept a caller-supplied correlation ID only when it is short and opaque, so
# a client cannot inject newlines or arbitrary text into the log stream.
_SAFE_REQUEST_ID = re.compile(r"\A[A-Za-z0-9._-]{8,64}\Z")

_request_id: ContextVar[str] = ContextVar("request_id", default="")


def generate_request_id() -> str:
    return uuid.uuid4().hex


def sanitize_request_id(value: Optional[str]) -> str:
    """Return ``value`` when it is a usable correlation ID, else a fresh one."""
    if value and _SAFE_REQUEST_ID.match(value):
        return value
    return generate_request_id()


def set_request_id(request_id: str) -> None:
    _request_id.set(request_id)


def get_request_id() -> str:
    return _request_id.get()


class RequestIDFilter(Filter):
    """Makes ``%(request_id)s`` available to every formatter."""

    def filter(self, record: LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        return True
