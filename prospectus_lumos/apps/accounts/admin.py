from django.contrib import admin
from .models import UserProfile, GoogleDriveCredentials, DocumentSource, Membership, Workspace


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    """Admin for UserProfile model"""

    list_display = ["user", "created_at", "updated_at"]
    list_filter = ["created_at"]
    search_fields = ["user__username", "user__email"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(GoogleDriveCredentials)
class GoogleDriveCredentialsAdmin(admin.ModelAdmin):
    """Admin for Google Drive credentials"""

    list_display = ["user", "folder_id", "is_active", "created_at"]
    list_filter = ["is_active", "created_at"]
    search_fields = ["user__username", "folder_id"]
    readonly_fields = ["folder_id", "created_at", "updated_at"]

    fieldsets = (
        (None, {"fields": ("user", "service_account_file", "drive_folder_url", "folder_id", "is_active")}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )


@admin.register(DocumentSource)
class DocumentSourceAdmin(admin.ModelAdmin):
    """Admin for document sources"""

    list_display = ["user", "name", "source_type", "is_active", "last_sync", "created_at"]
    list_filter = ["source_type", "is_active", "created_at"]
    search_fields = ["user__username", "name"]
    readonly_fields = ["created_at", "updated_at"]

    fieldsets = (
        (None, {"fields": ("user", "name", "source_type", "google_credentials", "is_active")}),
        ("Sync Information", {"fields": ("last_sync",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    """Admin for workspaces"""

    list_display = ["name", "slug", "kind", "currency", "timezone", "archived_at", "created_at"]
    list_filter = ["kind", "currency", "created_at"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["created_at", "updated_at"]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    """Admin for workspace memberships"""

    list_display = ["workspace", "user", "role", "status", "joined_at"]
    list_filter = ["role", "status", "joined_at"]
    search_fields = ["workspace__name", "workspace__slug", "user__username", "user__email"]
    autocomplete_fields = ["workspace", "user"]
    readonly_fields = ["joined_at", "updated_at"]
