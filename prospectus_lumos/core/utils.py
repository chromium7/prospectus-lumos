import pathlib
from typing import Any
from uuid import uuid4

from django.http import HttpRequest
from django.template.defaultfilters import slugify
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from prospectus_lumos.apps.accounts.models import User


class TypedHttpRequest(HttpRequest):
    user: User


def create_submission_token(request: TypedHttpRequest, *, pending_key: str, limit: int = 20) -> str:
    """Issue a bounded session-backed token for an idempotent HTML form."""

    token = uuid4().hex
    pending = list(request.session.get(pending_key, []))
    pending.append(token)
    request.session[pending_key] = pending[-limit:]
    return token


def submission_token_status(
    request: TypedHttpRequest,
    token: str,
    *,
    pending_key: str,
    used_key: str,
) -> str:
    """Return whether a submitted form token is pending, used, or invalid."""

    if token in request.session.get(used_key, []):
        return "used"
    if token in request.session.get(pending_key, []):
        return "pending"
    return "invalid"


def consume_submission_token(
    request: TypedHttpRequest,
    token: str,
    *,
    pending_key: str,
    used_key: str,
    limit: int = 20,
) -> None:
    """Move a successful form token into a bounded replay-protection set."""

    pending = list(request.session.get(pending_key, []))
    if token in pending:
        pending.remove(token)
    used = list(request.session.get(used_key, []))
    used.append(token)
    request.session[pending_key] = pending
    request.session[used_key] = used[-limit:]


def safe_return_url(request: HttpRequest, *, fallback: str) -> str:
    """Return a same-origin GET/POST destination or a trusted fallback."""

    candidate = request.POST.get("next", "") or request.GET.get("next", "")
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return fallback


class FilenameGenerator:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix

    def __call__(self, instance: Any, filename: str) -> str:
        today = timezone.localdate()

        filepath = pathlib.Path(filename)
        path = pathlib.Path(
            self.prefix, str(today.year), str(today.month), str(today.day), slugify(filepath.stem)
        ).with_suffix(filepath.suffix)

        return path  # type: ignore
