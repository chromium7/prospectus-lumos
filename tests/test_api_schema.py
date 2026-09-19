from __future__ import annotations

from io import StringIO
from pathlib import Path

import yaml

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

SNAPSHOT = Path(settings.BASE_DIR) / "openapi.yaml"


class SchemaEndpointTests(TestCase):
    def test_schema_endpoint_serves_the_generated_document(self) -> None:
        response = self.client.get(reverse("api:schema"))

        self.assertEqual(response.status_code, 200)
        schema = yaml.safe_load(response.content)
        self.assertEqual(schema["info"]["version"], settings.API_VERSION)

    def test_schema_declares_the_error_and_pagination_components(self) -> None:
        response = self.client.get(reverse("api:schema"))
        components = yaml.safe_load(response.content)["components"]["schemas"]

        self.assertIn("Error", components)
        self.assertIn("CursorPage", components)
        error_properties = components["Error"]["properties"]["error"]["properties"]
        self.assertEqual(
            sorted(error_properties),
            ["code", "fields", "message", "request_id"],
        )
        self.assertEqual(
            sorted(components["CursorPage"]["properties"]),
            ["count", "next", "previous", "results"],
        )


class SchemaSnapshotTests(TestCase):
    def test_checked_in_snapshot_matches_the_generated_schema(self) -> None:
        """The same check CI runs, so drift is caught before review."""
        generated = StringIO()
        call_command("spectacular", "--fail-on-warn", stdout=generated)

        self.assertEqual(
            yaml.safe_load(SNAPSHOT.read_text()),
            yaml.safe_load(generated.getvalue()),
            "openapi.yaml is stale. Regenerate it with `python manage.py spectacular --file openapi.yaml`.",
        )
