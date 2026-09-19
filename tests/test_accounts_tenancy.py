from __future__ import annotations

import uuid

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase

from prospectus_lumos.apps.accounts.models import Membership, Workspace


class TenancyModelTests(TestCase):
    def setUp(self) -> None:
        self.owner = User.objects.create_user(username="owner", password="pass")
        self.workspace = Workspace.objects.create(name="Saputra Household", slug="saputra", created_by=self.owner)

    def test_workspace_defaults_are_personal_idr_jakarta(self) -> None:
        workspace = Workspace.objects.create(name="Solo", slug="solo", created_by=self.owner)
        self.assertEqual(workspace.kind, Workspace.Kind.PERSONAL)
        self.assertEqual(workspace.currency, "IDR")
        self.assertEqual(workspace.timezone, "Asia/Jakarta")
        self.assertIsNone(workspace.archived_at)
        self.assertIsInstance(workspace.pk, uuid.UUID)

    def test_workspace_slug_is_unique(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            Workspace.objects.create(name="Duplicate", slug="saputra", created_by=self.owner)

    def test_workspace_survives_creator_deletion(self) -> None:
        self.owner.delete()
        self.workspace.refresh_from_db()
        self.assertIsNone(self.workspace.created_by)

    def test_membership_is_unique_per_workspace_and_user(self) -> None:
        member = User.objects.create_user(username="member", password="pass")
        Membership.objects.create(workspace=self.workspace, user=self.owner, role=Membership.Role.OWNER)
        membership = Membership.objects.create(workspace=self.workspace, user=member)
        self.assertEqual(membership.role, Membership.Role.VIEWER)
        self.assertEqual(membership.status, Membership.Status.ACTIVE)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Membership.objects.create(workspace=self.workspace, user=member, role=Membership.Role.EDITOR)

        other = Workspace.objects.create(name="Other", slug="other", created_by=self.owner)
        self.assertIsNotNone(Membership.objects.create(workspace=other, user=member).pk)

    def test_membership_queries_resolve_by_user_and_by_workspace_role(self) -> None:
        editor = User.objects.create_user(username="editor", password="pass")
        suspended = User.objects.create_user(username="suspended", password="pass")
        Membership.objects.create(workspace=self.workspace, user=self.owner, role=Membership.Role.OWNER)
        Membership.objects.create(workspace=self.workspace, user=editor, role=Membership.Role.EDITOR)
        Membership.objects.create(
            workspace=self.workspace,
            user=suspended,
            role=Membership.Role.EDITOR,
            status=Membership.Status.SUSPENDED,
        )

        self.assertEqual(
            list(
                Membership.objects.filter(user=editor, status=Membership.Status.ACTIVE).values_list(
                    "workspace_id", flat=True
                )
            ),
            [self.workspace.pk],
        )
        active_editors = Membership.objects.filter(
            workspace=self.workspace, role=Membership.Role.EDITOR, status=Membership.Status.ACTIVE
        )
        self.assertEqual([m.user_id for m in active_editors], [editor.pk])
        self.assertEqual(self.workspace.memberships.count(), 3)
