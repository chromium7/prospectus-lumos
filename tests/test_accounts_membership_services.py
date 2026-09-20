from __future__ import annotations

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from prospectus_lumos.apps.accounts.models import Membership, Workspace
from prospectus_lumos.apps.accounts.services import (
    LastOwnerError,
    MembershipNotFoundError,
    change_role,
    list_memberships,
    list_workspaces,
    remove_member,
    resolve_role,
    set_membership_status,
)


class MembershipServiceTests(TestCase):
    def setUp(self) -> None:
        self.owner = User.objects.create_user(username="owner", password="pass")
        self.editor = User.objects.create_user(username="editor", password="pass")
        self.outsider = User.objects.create_user(username="outsider", password="pass")
        self.workspace = Workspace.objects.create(name="Household", slug="household", created_by=self.owner)
        self.other_workspace = Workspace.objects.create(name="Side", slug="side", created_by=self.owner)
        self.owner_membership = Membership.objects.create(
            workspace=self.workspace, user=self.owner, role=Membership.Role.OWNER
        )
        self.editor_membership = Membership.objects.create(
            workspace=self.workspace, user=self.editor, role=Membership.Role.EDITOR
        )

    def test_resolve_role_reads_the_current_membership_row(self) -> None:
        self.assertEqual(resolve_role(user=self.owner, workspace=self.workspace), Membership.Role.OWNER)
        self.assertIsNone(resolve_role(user=self.outsider, workspace=self.workspace))
        self.assertIsNone(resolve_role(user=self.owner, workspace=self.other_workspace))

        self.editor_membership.role = Membership.Role.VIEWER
        self.editor_membership.save(update_fields=("role",))
        self.assertEqual(resolve_role(user=self.editor, workspace=self.workspace), Membership.Role.VIEWER)

        self.editor_membership.status = Membership.Status.SUSPENDED
        self.editor_membership.save(update_fields=("status",))
        self.assertIsNone(resolve_role(user=self.editor, workspace=self.workspace))

    def test_listings_cover_active_workspaces_and_workspace_members(self) -> None:
        Membership.objects.create(
            workspace=self.other_workspace,
            user=self.editor,
            role=Membership.Role.VIEWER,
            status=Membership.Status.SUSPENDED,
        )
        self.assertEqual(list(list_workspaces(user=self.editor)), [self.workspace])
        self.assertEqual(list(list_workspaces(user=self.outsider)), [])

        self.editor_membership.status = Membership.Status.SUSPENDED
        self.editor_membership.save(update_fields=("status",))
        self.assertEqual([m.user.username for m in list_memberships(workspace=self.workspace)], ["owner", "editor"])
        self.assertEqual(
            [m.user.username for m in list_memberships(workspace=self.workspace, include_suspended=False)],
            ["owner"],
        )

    def test_change_role_promotes_demotes_and_rejects_unknown_members(self) -> None:
        with self.assertRaises(MembershipNotFoundError):
            change_role(workspace=self.workspace, user=self.outsider, role=Membership.Role.EDITOR)

        with self.assertRaises(ValidationError):
            change_role(workspace=self.workspace, user=self.editor, role="admin")

        promoted = change_role(workspace=self.workspace, user=self.editor, role=Membership.Role.OWNER)
        self.assertEqual(promoted.role, Membership.Role.OWNER)

        demoted = change_role(workspace=self.workspace, user=self.owner, role=Membership.Role.VIEWER)
        demoted.refresh_from_db()
        self.assertEqual(demoted.role, Membership.Role.VIEWER)

        unchanged = change_role(workspace=self.workspace, user=self.editor, role=Membership.Role.OWNER)
        self.assertEqual(unchanged.role, Membership.Role.OWNER)

    def test_last_active_owner_cannot_be_demoted_suspended_or_removed(self) -> None:
        with self.assertRaises(LastOwnerError):
            change_role(workspace=self.workspace, user=self.owner, role=Membership.Role.EDITOR)
        with self.assertRaises(LastOwnerError):
            set_membership_status(workspace=self.workspace, user=self.owner, status=Membership.Status.SUSPENDED)
        with self.assertRaises(LastOwnerError):
            remove_member(workspace=self.workspace, user=self.owner)

        self.owner_membership.refresh_from_db()
        self.assertEqual(self.owner_membership.role, Membership.Role.OWNER)
        self.assertEqual(self.owner_membership.status, Membership.Status.ACTIVE)

    def test_a_suspended_owner_does_not_satisfy_the_last_owner_guard(self) -> None:
        Membership.objects.create(
            workspace=self.workspace,
            user=self.outsider,
            role=Membership.Role.OWNER,
            status=Membership.Status.SUSPENDED,
        )
        with self.assertRaises(LastOwnerError):
            remove_member(workspace=self.workspace, user=self.owner)

        set_membership_status(workspace=self.workspace, user=self.outsider, status=Membership.Status.ACTIVE)
        remove_member(workspace=self.workspace, user=self.owner)
        self.assertFalse(Membership.objects.filter(workspace=self.workspace, user=self.owner).exists())

    def test_remove_member_deletes_only_the_named_membership(self) -> None:
        with self.assertRaises(MembershipNotFoundError):
            remove_member(workspace=self.other_workspace, user=self.editor)

        remove_member(workspace=self.workspace, user=self.editor)
        self.assertEqual([m.user_id for m in Membership.objects.filter(workspace=self.workspace)], [self.owner.pk])
