from io import BytesIO
import json
from typing import Optional

from rest_framework import renderers
from rest_framework.exceptions import ParseError
from rest_framework.parsers import BaseParser

from django.conf import settings


class JSONParser(BaseParser):
    """
    Parses JSON-serialized data.
    """

    media_type = "application/json"
    renderer_class = renderers.JSONRenderer

    def parse(self, stream: BytesIO, media_type: Optional[str] = None, parser_context: Optional[dict] = None) -> dict:
        parser_context = parser_context or {}
        encoding = parser_context.get("encoding", settings.DEFAULT_CHARSET)

        try:
            data = stream.read().decode(encoding, errors="replace")  # Replace malformed UTF-8
            return json.loads(data)
        except ValueError as exc:
            raise ParseError("JSON parse error - %s" % exc)
