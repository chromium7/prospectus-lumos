from __future__ import annotations

from typing import Any

from django.db import models
from django.contrib.auth.models import User
from django.core.validators import FileExtensionValidator


class UserProfile(models.Model):
    """User profile to extend default User model"""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.user.username} Profile"


class GoogleDriveCredentials(models.Model):
    """Store Google Drive credentials for users"""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="google_drive_credentials")
    service_account_file = models.FileField(
        upload_to="credentials/",
        validators=[FileExtensionValidator(allowed_extensions=["json"])],
        help_text="Google service account credentials JSON file",
    )
    drive_folder_url = models.URLField(blank=True, help_text="Google Drive folder URL containing the budget sheets")
    folder_id = models.CharField(max_length=255, blank=True, help_text="Extracted Google Drive folder ID")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Google Drive Credentials"

    def __str__(self) -> str:
        return f"{self.user.username} - Google Drive"

    def save(self, *args: Any, **kwargs: Any) -> None:
        # Extract folder ID from URL if provided
        if self.drive_folder_url and not self.folder_id:
            # Extract folder ID from Google Drive URL
            if "/folders/" in self.drive_folder_url:
                self.folder_id = self.drive_folder_url.split("/folders/")[-1].split("?")[0]
        super().save(*args, **kwargs)


class DocumentSource(models.Model):
    """Track different sources of documents"""

    SOURCE_TYPES = [
        ("google_drive", "Google Drive"),
        ("direct_upload", "Direct Upload"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="document_sources")
    source_type = models.CharField(max_length=20, choices=SOURCE_TYPES)
    name = models.CharField(max_length=255, help_text="Friendly name for this source")
    google_credentials = models.ForeignKey(
        GoogleDriveCredentials,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Required for Google Drive sources",
    )
    is_active = models.BooleanField(default=True)
    last_sync = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["user", "name"]

    def __str__(self) -> str:
        return f"{self.user.username} - {self.name}"
