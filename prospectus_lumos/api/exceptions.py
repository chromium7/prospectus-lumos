"""The single error envelope every API error response uses.

See `docs/pwa_budgeting/03_API_CATALOG.md` (Contract conventions):

    {"error": {"code": ..., "message": ..., "fields": {...}, "request_id": ...}}
"""

from typing import Any, Dict, List, Optional

from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from prospectus_lumos.core.request_id import get_request_id

NON_FIELD_ERRORS_KEY = "non_field_errors"

# Human-readable summaries keyed by the machine-readable code clients switch on.
DEFAULT_MESSAGES: Dict[str, str] = {
    "validation_error": "Some fields need attention.",
    "not_authenticated": "Authentication is required.",
    "authentication_failed": "Authentication credentials were not accepted.",
    "permission_denied": "You do not have permission to perform this action.",
    "not_found": "The requested resource was not found.",
    "method_not_allowed": "That method is not allowed on this endpoint.",
    "throttled": "Too many requests. Please retry later.",
    "parse_error": "The request body could not be parsed.",
    "server_error": "Something went wrong on our side.",
}


def _flatten(detail: Any) -> List[str]:
    """Reduce a nested DRF detail structure to a flat list of messages."""
    if isinstance(detail, dict):
        messages: List[str] = []
        for value in detail.values():
            messages.extend(_flatten(value))
        return messages
    if isinstance(detail, list):
        messages = []
        for value in detail:
            messages.extend(_flatten(value))
        return messages
    return [str(detail)]


def _build_fields(exc: exceptions.APIException) -> Dict[str, List[str]]:
    """Map a ValidationError's detail onto ``{field: [message, ...]}``."""
    if not isinstance(exc, exceptions.ValidationError):
        return {}

    detail = exc.detail
    if isinstance(detail, dict):
        return {str(field): _flatten(value) for field, value in detail.items()}
    return {NON_FIELD_ERRORS_KEY: _flatten(detail)}


def _build_code(exc: exceptions.APIException) -> str:
    if isinstance(exc, exceptions.ValidationError):
        return "validation_error"
    code = getattr(exc, "default_code", None) or "error"
    return str(code)


def _build_message(exc: exceptions.APIException, code: str) -> str:
    if code in DEFAULT_MESSAGES:
        return DEFAULT_MESSAGES[code]
    detail = getattr(exc, "detail", None)
    messages = _flatten(detail) if detail is not None else []
    return messages[0] if messages else DEFAULT_MESSAGES["server_error"]


def api_exception_handler(exc: Exception, context: Dict[str, Any]) -> Optional[Response]:
    """DRF ``EXCEPTION_HANDLER`` that rewrites errors into the envelope.

    Anything DRF does not recognise is left alone so it surfaces as an
    unhandled 500 and reaches the usual error reporting.
    """
    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    # drf_exception_handler has already normalised Http404 and Django's
    # PermissionDenied into APIException instances of the right status.
    if not isinstance(exc, exceptions.APIException):
        if response.status_code == status.HTTP_404_NOT_FOUND:
            exc = exceptions.NotFound()
        else:
            exc = exceptions.PermissionDenied()

    code = _build_code(exc)
    response.data = {
        "error": {
            "code": code,
            "message": _build_message(exc, code),
            "fields": _build_fields(exc),
            "request_id": get_request_id(),
        }
    }
    return response
