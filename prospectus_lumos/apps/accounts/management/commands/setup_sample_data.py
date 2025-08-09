from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from prospectus_lumos.apps.accounts.models import UserProfile, DocumentSource


class Command(BaseCommand):
    help = "Set up sample data for testing the application"

    def add_arguments(self, parser):
        parser.add_argument(
            "--user", type=str, default="testuser", help="Username for the test user (default: testuser)"
        )

    def handle(self, *args, **options):
        username = options["user"]

        # Create test user if it doesn't exist
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "email": f"{username}@example.com",
                "first_name": "Test",
                "last_name": "User",
            },
        )

        if created:
            user.set_password("testpass123")
            user.save()
            self.stdout.write(self.style.SUCCESS(f"Created user: {username} (password: testpass123)"))
        else:
            self.stdout.write(f"User {username} already exists")

        # Create user profile
        profile, created = UserProfile.objects.get_or_create(user=user)
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created user profile for {username}"))

        # Note: Google Drive credentials would need actual service account file
        # For now, just show how to create a basic document source
        source, created = DocumentSource.objects.get_or_create(
            user=user,
            name="Manual Uploads",
            defaults={
                "source_type": "direct_upload",
                "is_active": True,
            },
        )

        if created:
            self.stdout.write(self.style.SUCCESS("Created document source: Manual Uploads"))

        self.stdout.write(
            self.style.SUCCESS(
                f"\nSample data setup complete!\n"
                f"You can now:\n"
                f"1. Login with username: {username}, password: testpass123\n"
                f"2. Access admin at /admin/ with superuser credentials\n"
                f"3. Test the application at http://localhost:8000/\n"
            )
        )
