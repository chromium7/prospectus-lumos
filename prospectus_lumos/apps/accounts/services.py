from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from .models import Invitation, Membership, Workspace

INVITATION_TTL = timedelta(days=7)
INVITATION_TOKEN_BYTES = 32


class MembershipNotFoundError(ValidationError):
    """Raised when a workspace has no membership row for the requested user."""


class LastOwnerError(ValidationError):
    """Raised when an operation would leave a workspace without an active owner."""


def resolve_role(*, user: User, workspace: Workspace) -> str | None:
    """Return the user's role in the workspace, or ``None`` without an active membership.

    The role is always read from the current membership row. A role copied into a JWT or a
    session may be stale and is never authoritative.
    """

    return (
        Membership.objects.filter(user=user, workspace=workspace, status=Membership.Status.ACTIVE)
        .values_list("role", flat=True)
        .first()
    )


def list_workspaces(*, user: User) -> QuerySet[Workspace]:
    """Return the workspaces the user can currently reach through an active membership."""

    return Workspace.objects.filter(memberships__user=user, memberships__status=Membership.Status.ACTIVE).order_by(
        "name"
    )


def list_memberships(*, workspace: Workspace, include_suspended: bool = True) -> QuerySet[Membership]:
    """Return the workspace's memberships, newest joiner last, with users preloaded."""

    memberships = Membership.objects.filter(workspace=workspace).select_related("user")
    if not include_suspended:
        memberships = memberships.filter(status=Membership.Status.ACTIVE)
    return memberships.order_by("joined_at", "pk")


@transaction.atomic
def change_role(*, workspace: Workspace, user: User, role: str) -> Membership:
    """Set the user's role in the workspace and return the updated membership.

    Raises ``MembershipNotFoundError`` when the user is not a member and ``LastOwnerError``
    when the change would demote the workspace's last active owner.
    """

    memberships = _locked_memberships(workspace)
    membership = _find_membership(memberships, user=user, workspace=workspace)
    if membership.role == role:
        return membership
    if role != Membership.Role.OWNER:
        _assert_another_active_owner_remains(memberships, membership=membership)
    membership.role = role
    membership.full_clean(exclude=("workspace", "user"))
    membership.save(update_fields=("role", "updated_at"))
    return membership


@transaction.atomic
def set_membership_status(*, workspace: Workspace, user: User, status: str) -> Membership:
    """Activate or suspend the user's membership and return the updated row.

    Suspending is guarded like removal: the last active owner cannot be suspended, because a
    suspended owner authorizes nothing.
    """

    memberships = _locked_memberships(workspace)
    membership = _find_membership(memberships, user=user, workspace=workspace)
    if membership.status == status:
        return membership
    if status != Membership.Status.ACTIVE:
        _assert_another_active_owner_remains(memberships, membership=membership)
    membership.status = status
    membership.full_clean(exclude=("workspace", "user"))
    membership.save(update_fields=("status", "updated_at"))
    return membership


@transaction.atomic
def remove_member(*, workspace: Workspace, user: User) -> None:
    """Delete the user's membership in the workspace.

    Raises ``MembershipNotFoundError`` when the user is not a member and ``LastOwnerError``
    when removal would leave the workspace without an active owner.
    """

    memberships = _locked_memberships(workspace)
    membership = _find_membership(memberships, user=user, workspace=workspace)
    _assert_another_active_owner_remains(memberships, membership=membership)
    membership.delete()


def _locked_memberships(workspace: Workspace) -> list[Membership]:
    """Lock every membership row of the workspace in primary-key order.

    Locking the whole small set in one deterministic order keeps the owner count stable for the
    duration of the transaction and keeps concurrent membership writes from deadlocking.
    """

    return list(Membership.objects.select_for_update().filter(workspace=workspace).order_by("pk"))


def _find_membership(memberships: list[Membership], *, user: User, workspace: Workspace) -> Membership:
    for membership in memberships:
        if membership.user_id == user.pk:
            return membership
    raise MembershipNotFoundError(f"{user.username} is not a member of {workspace.name}.")


def _assert_another_active_owner_remains(memberships: list[Membership], *, membership: Membership) -> None:
    if membership.role != Membership.Role.OWNER or membership.status != Membership.Status.ACTIVE:
        return
    for other in memberships:
        if (
            other.pk != membership.pk
            and other.role == Membership.Role.OWNER
            and other.status == Membership.Status.ACTIVE
        ):
            return
    raise LastOwnerError("A workspace must keep at least one active owner.")


class InvitationInvalidError(ValidationError):
    """Raised when an invitation token is unknown, expired, revoked, or already accepted."""


def normalize_invitation_email(email: str) -> str:
    """Return the address in the single form invitations are stored and looked up under."""

    return email.strip().lower()


def hash_invitation_token(token: str) -> str:
    """Return the digest stored for a raw invitation token."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@transaction.atomic
def create_invitation(
    *, workspace: Workspace, email: str, role: str, invited_by: User | None = None, ttl: timedelta = INVITATION_TTL
) -> tuple[Invitation, str]:
    """Issue an invitation and return it together with its raw token.

    The raw token is returned once, for the caller to put in the invitation link, and is never
    persisted. Re-inviting an address replaces the workspace's outstanding invitation for it, so
    the previous link stops working as soon as a new one is sent.
    """

    normalized = normalize_invitation_email(email)
    now = timezone.now()
    Invitation.objects.filter(
        workspace=workspace, email=normalized, accepted_at__isnull=True, revoked_at__isnull=True
    ).update(revoked_at=now, updated_at=now)
    token = secrets.token_urlsafe(INVITATION_TOKEN_BYTES)
    invitation = Invitation(
        workspace=workspace,
        email=normalized,
        role=role,
        token_hash=hash_invitation_token(token),
        invited_by=invited_by,
        expires_at=now + ttl,
    )
    invitation.full_clean(exclude=("workspace", "invited_by"))
    invitation.save()
    return invitation, token


def revoke_invitation(*, invitation: Invitation) -> Invitation:
    """Mark the invitation unusable. Revoking an already-revoked invitation keeps the first time."""

    if invitation.revoked_at is None:
        invitation.revoked_at = timezone.now()
        invitation.save(update_fields=("revoked_at", "updated_at"))
    return invitation


def verify_invitation(*, token: str, email: str | None = None) -> Invitation:
    """Return the usable invitation the raw token identifies.

    Raises ``InvitationInvalidError`` for an unknown, expired, revoked, or already-accepted
    token, and for an address that does not match the invited one. Every rejection uses the same
    message so a caller cannot probe which invitations exist.
    """

    invitation = Invitation.objects.filter(token_hash=hash_invitation_token(token)).select_related("workspace").first()
    if invitation is None or not invitation.is_usable():
        raise InvitationInvalidError("This invitation link is no longer valid.")
    if email is not None and normalize_invitation_email(email) != invitation.email:
        raise InvitationInvalidError("This invitation link is no longer valid.")
    return invitation
