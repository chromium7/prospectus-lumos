from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import RequestFactory, TestCase
from django.urls import reverse

from prospectus_lumos.apps.ai_analysis.models import AISettings
from prospectus_lumos.apps.ai_analysis.services import get_ai_insights, prepare_data_as_text
from prospectus_lumos.apps.accounts.models import DocumentSource
from prospectus_lumos.apps.documents.models import Document
from prospectus_lumos.apps.transactions.models import Transaction


class AISettingsModelTests(TestCase):
    def test_create_ai_settings(self) -> None:
        user = User.objects.create_user(username="tester", password="pass")
        settings = AISettings.objects.create(
            user=user,
            api_token="sk-test-token",
            model=AISettings.Model.GPT_4O,
        )
        self.assertEqual(settings.user, user)
        self.assertEqual(settings.api_token, "sk-test-token")
        self.assertEqual(settings.model, AISettings.Model.GPT_4O)
        self.assertEqual(str(settings), "tester - AI Settings")

    def test_ai_settings_one_to_one(self) -> None:
        user = User.objects.create_user(username="tester", password="pass")
        AISettings.objects.create(user=user, api_token="sk-token")
        with self.assertRaises(IntegrityError):
            AISettings.objects.create(user=user, api_token="sk-token-2")


class AISettingsViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="tester", password="pass")
        self.url = reverse("ai_settings")
        self.factory = RequestFactory()

    def test_view_requires_login(self) -> None:
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_get_ai_settings_page_with_factory(self) -> None:
        request = self.factory.get(self.url)
        request.user = self.user
        from prospectus_lumos.website.ai_analysis.views import ai_settings_view

        response = ai_settings_view(request)
        self.assertEqual(response.status_code, 200)

    def test_post_creates_ai_settings(self) -> None:
        self.client.login(username="tester", password="pass")
        self.assertFalse(AISettings.objects.filter(user=self.user).exists())

        response = self.client.post(
            self.url,
            {"api_token": "sk-new-token", "model": AISettings.Model.GPT_4O},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(AISettings.objects.filter(user=self.user).exists())
        settings = AISettings.objects.get(user=self.user)
        self.assertEqual(settings.api_token, "sk-new-token")
        self.assertEqual(settings.model, AISettings.Model.GPT_4O)

    def test_post_updates_existing_ai_settings(self) -> None:
        self.client.login(username="tester", password="pass")
        AISettings.objects.create(user=self.user, api_token="sk-old-token")

        response = self.client.post(
            self.url,
            {"api_token": "sk-new-token", "model": AISettings.Model.GPT_35_TURBO},
        )
        self.assertEqual(response.status_code, 302)
        settings = AISettings.objects.get(user=self.user)
        self.assertEqual(settings.api_token, "sk-new-token")
        self.assertEqual(settings.model, AISettings.Model.GPT_35_TURBO)

    def test_post_empty_token_is_invalid(self) -> None:
        request = self.factory.post(self.url, {"api_token": "", "model": AISettings.Model.GPT_4O})
        request.user = self.user
        from prospectus_lumos.website.ai_analysis.views import ai_settings_view

        response = ai_settings_view(request)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(AISettings.objects.filter(user=self.user).exists())


class AIServiceTests(TestCase):
    def test_get_ai_insights_openai(self) -> None:
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Analysis: Spending is normal."
        mock_client.chat.completions.create.return_value = mock_response

        with patch(
            "prospectus_lumos.apps.ai_analysis.services.OpenAI",
            return_value=mock_client,
        ):
            result = get_ai_insights(
                api_token="sk-test",
                model="gpt-4o",
                data_text="test data",
            )

        self.assertEqual(result, "Analysis: Spending is normal.")
        mock_client.chat.completions.create.assert_called_once()

    def test_get_ai_insights_anthropic(self) -> None:
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Anthropic analysis."
        mock_client.chat.completions.create.return_value = mock_response

        with patch(
            "prospectus_lumos.apps.ai_analysis.services.OpenAI",
            return_value=mock_client,
        ) as mock_openai:
            result = get_ai_insights(
                api_token="sk-test",
                model="claude-3-sonnet-20240229",
                data_text="test data",
            )

        self.assertEqual(result, "Anthropic analysis.")
        mock_openai.assert_called_once()


class PrepareDataAsTextTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="tester", password="pass")
        self.source = DocumentSource.objects.create(
            user=self.user,
            source_type="direct_upload",
            name="Manual",
            is_active=True,
        )

    def test_prepare_income_data_as_text(self) -> None:
        doc = Document.objects.create(
            user=self.user,
            source=self.source,
            month=6,
            year=2025,
        )
        Transaction.objects.create(
            document=doc,
            transaction_type="income",
            date="2025-06-15",
            amount=500000,
            description="Salary",
            category="Paycheck",
        )
        Transaction.objects.create(
            document=doc,
            transaction_type="expense",
            date="2025-06-16",
            amount=50000,
            description="Groceries",
            category="Food",
        )

        text = prepare_data_as_text([doc], "income")
        self.assertIn("Salary", text)
        self.assertIn("500000", text)
        self.assertIn("Paycheck", text)
        self.assertNotIn("Groceries", text)

    def test_prepare_expense_data_as_text(self) -> None:
        doc = Document.objects.create(
            user=self.user,
            source=self.source,
            month=6,
            year=2025,
        )
        Transaction.objects.create(
            document=doc,
            transaction_type="income",
            date="2025-06-15",
            amount=500000,
            description="Salary",
            category="Paycheck",
        )
        Transaction.objects.create(
            document=doc,
            transaction_type="expense",
            date="2025-06-16",
            amount=50000,
            description="Groceries",
            category="Food",
        )

        text = prepare_data_as_text([doc], "expense")
        self.assertIn("Groceries", text)
        self.assertIn("50000", text)
        self.assertIn("Food", text)
        self.assertNotIn("Salary", text)

    def test_prepare_portfolio_data_as_text(self) -> None:
        doc = Document.objects.create(
            user=self.user,
            source=self.source,
            month=6,
            year=2025,
        )
        Transaction.objects.create(
            document=doc,
            transaction_type="income",
            date="2025-06-15",
            amount=500000,
            description="Salary",
            category="Paycheck",
        )
        Transaction.objects.create(
            document=doc,
            transaction_type="expense",
            date="2025-06-16",
            amount=50000,
            description="Groceries",
            category="Food",
        )

        text = prepare_data_as_text([doc], "portfolio")
        self.assertIn("Salary", text)
        self.assertIn("Groceries", text)
