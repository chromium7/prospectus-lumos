from __future__ import annotations

from importlib import import_module

from django.contrib.auth.models import User
from django.test import TestCase

from prospectus_lumos.apps.accounts.models import Membership, Workspace

backfill = import_module("prospectus_lumos.apps.accounts.migrations.0003_backfill_personal_workspaces")


def run_forwards() -> int:
    return backfill.create_personal_workspaces(Workspace, Membership, User)


def run_backwards() -> int:
    return backfill.delete_personal_workspaces(Workspace, Membership)


class PersonalWorkspaceBackfillTests(TestCase):
    def setUp(self) -> None:
        self.alice = User.objects.create_user(username="alice", password="pass", first_name="Alice")
        self.bob = User.objects.create_user(username="bob", password="pass")

    def test_every_user_gets_one_personal_workspace_and_owner_membership(self) -> None:
        self.assertEqual(run_forwards(), 2)

        for user in (self.alice, self.bob):
            memberships = Membership.objects.filter(user=user, workspace__kind=Workspace.Kind.PERSONAL)
            self.assertEqual(memberships.count(), 1)
            membership = memberships.get()
            self.assertEqual(membership.role, Membership.Role.OWNER)
            self.assertEqual(membership.status, Membership.Status.ACTIVE)
            self.assertEqual(membership.workspace.created_by, user)

        self.assertEqual(Workspace.objects.get(created_by=self.alice).name, "Alice's Workspace")
        self.assertEqual(Workspace.objects.get(created_by=self.bob).name, "bob's Workspace")
        self.assertEqual(Workspace.objects.get(created_by=self.bob).slug, "bob")

    def test_rerunning_creates_no_duplicates(self) -> None:
        run_forwards()
        first_ids = set(Workspace.objects.values_list("id", flat=True))

        self.assertEqual(run_forwards(), 0)

        self.assertEqual(Workspace.objects.count(), 2)
        self.assertEqual(Membership.objects.count(), 2)
        self.assertEqual(set(Workspace.objects.values_list("id", flat=True)), first_ids)

    def test_identifiers_are_derived_from_the_user_primary_key(self) -> None:
        run_forwards()
        workspace = Workspace.objects.get(created_by=self.alice)
        self.assertEqual(workspace.pk, backfill.workspace_id_for(self.alice.pk))
        self.assertEqual(workspace.memberships.get().pk, backfill.membership_id_for(self.alice.pk))

    def test_slug_collision_with_an_existing_workspace_is_resolved(self) -> None:
        Workspace.objects.create(name="Taken", slug="alice")

        run_forwards()

        self.assertEqual(Workspace.objects.get(created_by=self.alice).slug, "alice-2")

    def test_user_with_an_existing_owner_membership_is_skipped(self) -> None:
        existing = Workspace.objects.create(name="Alice at home", slug="alice-home", created_by=self.alice)
        Membership.objects.create(workspace=existing, user=self.alice, role=Membership.Role.OWNER)

        self.assertEqual(run_forwards(), 1)

        self.assertEqual(Workspace.objects.filter(created_by=self.alice).count(), 1)
        self.assertEqual(Membership.objects.filter(user=self.alice).count(), 1)

    def test_reverse_removes_only_the_generated_rows(self) -> None:
        handmade = Workspace.objects.create(name="Shared", slug="shared", created_by=self.bob)
        Membership.objects.create(workspace=handmade, user=self.bob, role=Membership.Role.EDITOR)
        run_forwards()

        self.assertEqual(run_backwards(), 2)

        self.assertEqual(list(Workspace.objects.all()), [handmade])
        self.assertEqual(Membership.objects.get().workspace, handmade)

    def test_forward_is_restorable_after_reverse(self) -> None:
        run_forwards()
        before = set(Workspace.objects.values_list("id", "slug"))

        run_backwards()
        run_forwards()

        self.assertEqual(set(Workspace.objects.values_list("id", "slug")), before)
