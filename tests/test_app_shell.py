from pathlib import Path

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone


class AppShellTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="shell-user", password="pass")

    def test_app_home_requires_the_existing_session_login(self) -> None:
        response = self.client.get(reverse("app:home"))

        self.assertRedirects(response, f"{reverse('login')}?next={reverse('app:home')}", fetch_redirect_response=False)

    def test_app_home_renders_authenticated_shell_and_preserves_existing_tools(self) -> None:
        self.client.force_login(self.user)

        response = self.client.get(reverse("app:home"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "base.html")
        self.assertTemplateUsed(response, "app/home.html")
        self.assertEqual(response.context["selected_tab"], "app_home")
        self.assertContains(response, 'href="/app/" aria-current="page"', count=2)
        self.assertContains(response, f'href="{reverse("document_list")}"')
        self.assertContains(response, f'href="{reverse("income_analyzer")}"')
        self.assertContains(response, f'href="{reverse("expense_analyzer")}"')
        self.assertContains(response, f'href="{reverse("portfolio_analyzer")}"')
        self.assertContains(response, f'href="{reverse("category_analyzer")}"')
        self.assertContains(response, f'href="{reverse("freedom_plan_list")}"')
        self.assertContains(response, "data-offline-banner")
        self.assertContains(response, "data-app-live-region")
        self.assertContains(response, 'class="dropdown-menu dropdown-menu-end profile-menu"')

    def test_dashboard_renders_progressive_states_and_interaction_hooks(self) -> None:
        self.client.force_login(self.user)

        response = self.client.get(reverse("app:home"), {"month": "2026-09"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["month"], "2026-09")
        self.assertEqual(response.context["dashboard_api_url"], "/api/v1/dashboard")
        self.assertContains(response, 'name="month" type="month" value="2026-09"')
        self.assertContains(response, "data-dashboard-loading")
        self.assertContains(response, "data-dashboard-empty")
        self.assertContains(response, "data-dashboard-error")
        self.assertContains(response, "data-dashboard-retry")
        self.assertContains(response, 'src="/static/js/dashboard.js"')
        self.assertContains(response, 'href="/app/transactions/new"')
        self.assertContains(response, 'href="/app/budgets/2026-09"')

    def test_dashboard_rejects_an_invalid_month_without_loading_data(self) -> None:
        self.client.force_login(self.user)

        response = self.client.get(reverse("app:home"), {"month": "September"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.context["month"], timezone.localdate().strftime("%Y-%m"))
        self.assertContains(response, "That month does not look right", status_code=400)
        self.assertNotContains(response, "static/js/dashboard.js", status_code=400)

    def test_shell_styles_cover_dark_narrow_focus_and_reduced_motion_states(self) -> None:
        stylesheet = Path("static_files/css/theme.css").read_text(encoding="utf-8")

        self.assertIn(".dark .mobile-app-nav", stylesheet)
        self.assertIn(".navbar-profile .profile-menu", stylesheet)
        self.assertIn(":focus-visible", stylesheet)
        self.assertIn("@media (max-width: 22.5rem)", stylesheet)
        self.assertIn("env(safe-area-inset-bottom)", stylesheet)
        self.assertIn("@media (prefers-reduced-motion: reduce)", stylesheet)
