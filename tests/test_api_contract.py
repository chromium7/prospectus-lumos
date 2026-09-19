from __future__ import annotations

from django.test import TestCase, override_settings
from django.urls import reverse

from prospectus_lumos.core.request_id import REQUEST_ID_HEADER, sanitize_request_id


@override_settings(ROOT_URLCONF="tests.urls_api_contract")
class ErrorEnvelopeTests(TestCase):
    def test_validation_error_uses_the_envelope_with_field_messages(self) -> None:
        response = self.client.post("/validation", {"amount": 0}, content_type="application/json")

        self.assertEqual(response.status_code, 400)
        error = response.json()["error"]
        self.assertEqual(error["code"], "validation_error")
        self.assertEqual(error["message"], "Some fields need attention.")
        self.assertIn("amount", error["fields"])
        self.assertTrue(error["fields"]["amount"])

    def test_permission_denied_uses_the_envelope(self) -> None:
        response = self.client.get("/forbidden")

        self.assertEqual(response.status_code, 403)
        error = response.json()["error"]
        self.assertEqual(error["code"], "permission_denied")
        self.assertEqual(error["fields"], {})

    def test_not_found_uses_the_envelope(self) -> None:
        response = self.client.get("/missing")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "not_found")

    def test_error_envelope_carries_the_responses_request_id(self) -> None:
        response = self.client.get("/missing", headers={"x-request-id": "trace-abc-123"})

        self.assertEqual(response.headers[REQUEST_ID_HEADER], "trace-abc-123")
        self.assertEqual(response.json()["error"]["request_id"], "trace-abc-123")


class RequestIDMiddlewareTests(TestCase):
    def test_every_response_carries_a_request_id(self) -> None:
        response = self.client.get(reverse("api:ping"))

        self.assertTrue(response.headers[REQUEST_ID_HEADER])

    def test_a_sane_inbound_request_id_is_reused(self) -> None:
        response = self.client.get(reverse("api:ping"), headers={"x-request-id": "upstream-0001"})

        self.assertEqual(response.headers[REQUEST_ID_HEADER], "upstream-0001")

    def test_an_unusable_inbound_request_id_is_replaced(self) -> None:
        response = self.client.get(reverse("api:ping"), headers={"x-request-id": "no"})

        self.assertNotEqual(response.headers[REQUEST_ID_HEADER], "no")
        self.assertTrue(response.headers[REQUEST_ID_HEADER])


class SanitizeRequestIDTests(TestCase):
    def test_rejects_values_that_could_corrupt_a_log_line(self) -> None:
        for value in (None, "", "short", "x" * 65, "has spaces", "line\nbreak"):
            with self.subTest(value=value):
                self.assertNotEqual(sanitize_request_id(value), value)
