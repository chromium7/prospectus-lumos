from __future__ import annotations

import uuid
from typing import Any

from django.conf import settings
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

    class SourceType(models.TextChoices):
        GOOGLE_DRIVE = "google_drive", "Google Drive"
        DIRECT_UPLOAD = "direct_upload", "Direct Upload"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="document_sources")
    source_type = models.CharField(max_length=20, choices=SourceType.choices)
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


class Workspace(models.Model):
    """A tenant boundary that owns every financial row belonging to one person or household."""

    class Kind(models.TextChoices):
        PERSONAL = "personal", "Personal"
        HOUSEHOLD = "household", "Household"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    slug = models.SlugField(
        max_length=140, unique=True, help_text="Stable URL identifier; renaming the workspace does not change it."
    )
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.PERSONAL)
    currency = models.CharField(max_length=3, default="IDR", help_text="ISO 4217 code.")
    timezone = models.CharField(max_length=64, default="Asia/Jakarta", help_text="IANA time zone name.")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_workspaces"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class Membership(models.Model):
    """A user's role in a workspace; the only path through which workspace data is authorized."""

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        EDITOR = "editor", "Editor"
        VIEWER = "viewer", "Viewer"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.VIEWER)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    joined_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("workspace", "user"), name="membership_workspace_user_uniq")]
        indexes = [
            models.Index(fields=("user", "status"), name="membership_user_status_idx"),
            models.Index(fields=("workspace", "role", "status"), name="membership_workspace_role_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.user.username} - {self.workspace.name} ({self.role})"
