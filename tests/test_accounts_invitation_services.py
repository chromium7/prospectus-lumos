from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from prospectus_lumos.apps.accounts.models import Invitation, Membership, Workspace
from prospectus_lumos.apps.accounts.services import (
    InvitationInvalidError,
    create_invitation,
    hash_invitation_token,
    revoke_invitation,
    verify_invitation,
)


class InvitationServiceTests(TestCase):
    def setUp(self) -> None:
        self.inviter = User.objects.create_user(username="owner", password="pass")
        self.workspace = Workspace.objects.create(name="Household", slug="household", created_by=self.inviter)

    def test_create_stores_only_the_hash_and_verify_accepts_the_token(self) -> None:
        invitation, token = create_invitation(
            workspace=self.workspace,
            email="  Person@Example.COM ",
            role=Membership.Role.EDITOR,
            invited_by=self.inviter,
        )

        self.assertEqual(invitation.email, "person@example.com")
        self.assertEqual(invitation.token_hash, hash_invitation_token(token))
        self.assertNotEqual(invitation.token_hash, token)
        self.assertFalse(any(field.name == "token" for field in Invitation._meta.fields))
        self.assertEqual(verify_invitation(token=token, email="PERSON@example.com"), invitation)

    def test_reinviting_revokes_the_previous_token(self) -> None:
        previous, previous_token = create_invitation(
            workspace=self.workspace, email="person@example.com", role=Membership.Role.VIEWER
        )
        current, current_token = create_invitation(
            workspace=self.workspace, email="Person@Example.com", role=Membership.Role.EDITOR
        )

        previous.refresh_from_db()
        self.assertIsNotNone(previous.revoked_at)
        with self.assertRaises(InvitationInvalidError):
            verify_invitation(token=previous_token)
        self.assertEqual(verify_invitation(token=current_token), current)

    def test_revoked_expired_accepted_unknown_and_mismatched_tokens_are_rejected(self) -> None:
        revoked, revoked_token = create_invitation(
            workspace=self.workspace, email="revoked@example.com", role=Membership.Role.VIEWER
        )
        revoke_invitation(invitation=revoked)

        expired, expired_token = create_invitation(
            workspace=self.workspace,
            email="expired@example.com",
            role=Membership.Role.VIEWER,
            ttl=timedelta(seconds=-1),
        )
        accepted, accepted_token = create_invitation(
            workspace=self.workspace, email="accepted@example.com", role=Membership.Role.VIEWER
        )
        accepted.accepted_at = timezone.now()
        accepted.save(update_fields=("accepted_at", "updated_at"))
        _, mismatched_token = create_invitation(
            workspace=self.workspace, email="matched@example.com", role=Membership.Role.VIEWER
        )

        invalid_attempts = (
            (revoked_token, None),
            (expired_token, None),
            (accepted_token, None),
            ("unknown", None),
            (mismatched_token, "different@example.com"),
        )
        for token, email in invalid_attempts:
            with self.subTest(token=token, email=email), self.assertRaises(InvitationInvalidError):
                verify_invitation(token=token, email=email)

        self.assertFalse(expired.is_usable())
