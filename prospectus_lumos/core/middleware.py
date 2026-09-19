from typing import Callable

from django.http import HttpRequest, HttpResponse

from .request_id import REQUEST_ID_HEADER, sanitize_request_id, set_request_id


class RequestIDMiddleware:
    """Assigns every request a correlation ID and echoes it on the response.

    The ID is reused from the inbound ``X-Request-ID`` header when the caller
    supplies a sane one, so a proxy or client trace survives into our logs.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request_id = sanitize_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.request_id = request_id  # type: ignore[attr-defined]
        # Deliberately not reset once the response is built: Django logs 4xx/5xx
        # responses from outside the middleware chain, and those lines are the
        # ones most worth correlating. Every request overwrites it on entry.
        set_request_id(request_id)

        response = self.get_response(request)
        response[REQUEST_ID_HEADER] = request_id
        return response
