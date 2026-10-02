import pathlib
from typing import Any

from django.http import HttpRequest
from django.template.defaultfilters import slugify
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from prospectus_lumos.apps.accounts.models import User


class TypedHttpRequest(HttpRequest):
    user: User


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
