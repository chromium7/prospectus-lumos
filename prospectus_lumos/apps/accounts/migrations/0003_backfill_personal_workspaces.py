"""Give every pre-existing user one personal workspace with an active owner membership.

The backfill is deterministic: workspace and membership primary keys are derived from the
user primary key with ``uuid.uuid5``, so re-running the migration finds the rows it created
last time instead of inserting duplicates. That same property makes it reversible -- the
reverse operation removes exactly the rows this migration is responsible for and leaves any
workspace created by a human or a later feature untouched. Legacy ownership fields are not
read or rewritten here.
"""

import uuid

from django.conf import settings
from django.db import migrations
from django.utils.text import slugify

# Fixed namespace so derived identifiers stay stable across databases and re-runs.
PERSONAL_WORKSPACE_NAMESPACE = uuid.UUID("2f5e6c8a-8f7e-4a6a-9f3b-1c6c9a0f4d21")


def workspace_id_for(user_pk: int) -> uuid.UUID:
    return uuid.uuid5(PERSONAL_WORKSPACE_NAMESPACE, f"workspace:{user_pk}")


def membership_id_for(user_pk: int) -> uuid.UUID:
    return uuid.uuid5(PERSONAL_WORKSPACE_NAMESPACE, f"membership:{user_pk}")


def workspace_name_for(user) -> str:
    display = (user.first_name or "").strip() or user.username
    return f"{display}'s Workspace"[:120]


def unique_slug(user, taken: set) -> str:
    base = slugify(user.username)[:120] or f"user-{user.pk}"
    slug = base
    suffix = 2
    while slug in taken:
        slug = f"{base}-{suffix}"[:140]
        suffix += 1
    taken.add(slug)
    return slug


def create_personal_workspaces(Workspace, Membership, User) -> int:
    """Create the missing personal workspaces/memberships. Returns how many users were backfilled."""
    already_owned = set(
        Membership.objects.filter(role="owner", status="active", workspace__kind="personal").values_list(
            "user_id", flat=True
        )
    )
    taken_slugs = set(Workspace.objects.values_list("slug", flat=True))
    workspaces = []
    memberships = []
    for user in User.objects.order_by("pk").iterator():
        if user.pk in already_owned:
            continue
        workspace_pk = workspace_id_for(user.pk)
        workspaces.append(
            Workspace(
                pk=workspace_pk,
                name=workspace_name_for(user),
                slug=unique_slug(user, taken_slugs),
                kind="personal",
                created_by=user,
            )
        )
        memberships.append(
            Membership(
                pk=membership_id_for(user.pk),
                workspace_id=workspace_pk,
                user=user,
                role="owner",
                status="active",
            )
        )
    Workspace.objects.bulk_create(workspaces, batch_size=500)
    Membership.objects.bulk_create(memberships, batch_size=500)
    return len(workspaces)


def delete_personal_workspaces(Workspace, Membership) -> int:
    """Remove only the workspaces whose primary key this migration derived from their creator.

    Memberships go with them through the cascade. A workspace someone created by hand, or one
    whose creator has since been deleted, never matches and is left alone.
    """
    generated = [
        workspace.pk
        for workspace in Workspace.objects.filter(kind="personal").only("id", "created_by")
        if workspace.created_by_id and workspace.pk == workspace_id_for(workspace.created_by_id)
    ]
    Membership.objects.filter(workspace_id__in=generated).delete()
    deleted, _ = Workspace.objects.filter(pk__in=generated).delete()
    return deleted


def forwards(apps, schema_editor) -> None:
    create_personal_workspaces(
        apps.get_model("accounts", "Workspace"),
        apps.get_model("accounts", "Membership"),
        apps.get_model(*settings.AUTH_USER_MODEL.split(".")),
    )


def backwards(apps, schema_editor) -> None:
    delete_personal_workspaces(
        apps.get_model("accounts", "Workspace"),
        apps.get_model("accounts", "Membership"),
    )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_workspace_membership"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
