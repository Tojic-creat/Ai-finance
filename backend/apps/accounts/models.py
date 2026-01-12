# backend/apps/accounts/models.py
"""
Models for the `accounts` app.

Contains:
- Profile: one-to-one extension for the User model (avatar, preferences).
- Family: a group owned by a user; supports invitations and memberships.
- FamilyMembership: through model linking users to a Family with roles.
- Invitation: email invitation to join a family (simple token-based flow).

Notes:
- This app intentionally does NOT define a custom User model. It uses the project's
  configured user via get_user_model(). If you want a custom user, define it at
  project-level and keep these models working with get_user_model().
- Some helper methods are lightweight (e.g. resend) and are expected to be wired to
  real email sending in your application code / tasks (Celery).
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import validators
from django.core.mail import send_mail
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

User = get_user_model()


# -------------------------
# Helpers / validators
# -------------------------
def _max_attachment_size() -> int:
    return int(getattr(settings, "MAX_ATTACHMENT_SIZE", 10 * 1024 * 1024))


def validate_file_size(file):
    size = getattr(file, "size", None)
    if size is not None and size > _max_attachment_size():
        raise validators.ValidationError(
            _("File is too large (max %(max)s bytes)."),
            code="file_too_large",
            params={"max": _max_attachment_size()},
        )


def avatar_upload_to(instance, filename):
    # upload path: avatars/user_<id>/<uuid>_<filename>
    ext = filename.split(".")[-1]
    name = f"{uuid.uuid4().hex}.{ext}"
    return f"avatars/user_{instance.user.id}/{name}"


# -------------------------
# Profile model
# -------------------------
class Profile(models.Model):
    """
    Optional profile for the User.
    Keeps preferences (timezone, language, opt_in_ml), avatar and a short bio.
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    display_name = models.CharField(_("display name"), max_length=150, blank=True)
    avatar = models.ImageField(
        _("avatar"),
        upload_to=avatar_upload_to,
        null=True,
        blank=True,
        validators=[validate_file_size],
        help_text=_("Allowed: jpg/png/pdf; max size configured by MAX_ATTACHMENT_SIZE"),
    )
    bio = models.TextField(_("bio"), blank=True)
    timezone = models.CharField(_("timezone"), max_length=64, default=getattr(settings, "TIME_ZONE", "UTC"))
    language = models.CharField(_("language"), max_length=10, default=getattr(settings, "LANGUAGE_CODE", "en-us"))
    opt_in_ml = models.BooleanField(_("opt in ML processing"), default=True)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("Profile")
        verbose_name_plural = _("Profiles")

    def __str__(self) -> str:
        return self.display_name or getattr(self.user, getattr(User, "USERNAME_FIELD", "username"), str(self.user))


# -------------------------
# Family and membership
# -------------------------
class Family(models.Model):
    """
    Family / group that can contain multiple users.
    One user is designated as owner (full rights).
    """

    name = models.CharField(_("name"), max_length=200)
    description = models.TextField(_("description"), blank=True)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="owned_families")
    members = models.ManyToManyField(User, through="FamilyMembership", related_name="families")
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        verbose_name = _("Family")
        verbose_name_plural = _("Families")

    def __str__(self) -> str:
        return f"{self.name} (owner={self.owner})"

    def add_member(self, user: User, role: str = "participant", added_by: Optional[User] = None) -> "FamilyMembership":
        """
        Add a user to the family. If membership exists, returns it (and updates role).
        Role should be one of FamilyMembership.Role values.
        """
        if role not in FamilyMembership.Role.values:
            raise ValueError("Invalid role")

        with transaction.atomic():
            membership, created = FamilyMembership.objects.get_or_create(
                family=self, user=user, defaults={"role": role, "added_by": added_by}
            )
            if not created and membership.role != role:
                membership.role = role
                membership.save(update_fields=["role", "updated_at"])
            return membership

    def remove_member(self, user: User):
        """
        Remove a membership (if exists). Owner cannot be removed via this method.
        """
        try:
            membership = FamilyMembership.objects.get(family=self, user=user)
        except FamilyMembership.DoesNotExist:
            return
        if membership.role == FamilyMembership.Role.OWNER:
            raise PermissionError("Cannot remove owner from family")
        membership.delete()


class FamilyMembership(models.Model):
    """
    Through model linking User <-> Family with a role.
    """

    class Role:
        OWNER = "owner"
        PARTICIPANT = "participant"
        VIEWER = "viewer"

        choices = (
            (OWNER, _("Owner")),
            (PARTICIPANT, _("Participant")),
            (VIEWER, _("Viewer")),
        )

        values = {OWNER, PARTICIPANT, VIEWER}

    family = models.ForeignKey(Family, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="family_memberships")
    role = models.CharField(_("role"), max_length=20, choices=Role.choices, default=Role.PARTICIPANT)
    added_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    added_at = models.DateTimeField(_("added at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        unique_together = (("family", "user"),)
        verbose_name = _("Family membership")
        verbose_name_plural = _("Family memberships")

    def __str__(self) -> str:
        return f"{self.user} in {self.family} as {self.role}"


# -------------------------
# Invitation
# -------------------------
class Invitation(models.Model):
    """
    Invitation to join a Family (by email). Simple token flow.

    Workflow:
    - create Invitation(email, family, invited_by)
    - send email with token link (Invitation.resend can be used to trigger send)
    - when user follows link, call Invitation.accept(user) to create membership
    """

    class Status:
        PENDING = "pending"
        ACCEPTED = "accepted"
        EXPIRED = "expired"
        REVOKED = "revoked"

        choices = (
            (PENDING, _("Pending")),
            (ACCEPTED, _("Accepted")),
            (EXPIRED, _("Expired")),
            (REVOKED, _("Revoked")),
        )

    family = models.ForeignKey(Family, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField(_("email"))
    invited_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="sent_invitations")
    token = models.CharField(_("token"), max_length=64, default=lambda: uuid.uuid4().hex, db_index=True)
    message = models.TextField(_("message"), blank=True)
    status = models.CharField(_("status"), max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    accepted_at = models.DateTimeField(_("accepted at"), null=True, blank=True)
    expires_at = models.DateTimeField(_("expires at"), null=True, blank=True)
    resend_count = models.PositiveIntegerField(_("resend count"), default=0)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = _("Invitation")
        verbose_name_plural = _("Invitations")

    def __str__(self) -> str:
        return f"Invite {self.email} -> {self.family.name} (status={self.status})"

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return timezone.now() >= self.expires_at

    def mark_expired_if_needed(self):
        if self.is_expired() and self.status == self.Status.PENDING:
            self.status = self.Status.EXPIRED
            self.save(update_fields=["status"])

    def resend(self, send_email_func: Optional[callable] = None) -> bool:
        """
        Resend the invitation email. If `send_email_func` provided, call it; otherwise
        attempt a minimal send_mail using Django email settings (this is a best-effort).
        Returns True on success, False otherwise.
        """
        if self.status != self.Status.PENDING:
            return False
        if self.is_expired():
            self.status = self.Status.EXPIRED
            self.save(update_fields=["status"])
            return False

        subject = f"Invitation to join family: {self.family.name}"
        link = f"{getattr(settings, 'SITE_URL', '').rstrip('/')}/invite/{self.token}/" if getattr(settings, "SITE_URL", None) else f"/invite/{self.token}/"
        body = f"You have been invited to join the family '{self.family.name}'.\n\nMessage:\n{self.message}\n\nAccept: {link}"

        try:
            if callable(send_email_func):
                send_email_func(subject=subject, message=body, to=[self.email])
            else:
                # best-effort: use Django send_mail
                send_mail(subject, body, getattr(settings, "DEFAULT_FROM_EMAIL", None), [self.email])
            self.resend_count = models.F("resend_count") + 1
            # update resend_count in DB
            self.save(update_fields=["resend_count"])
            return True
        except Exception:
            # swallowing exception here; callers can log if needed
            return False

    def accept(self, user: User, role: str = FamilyMembership.Role.PARTICIPANT) -> Optional[FamilyMembership]:
        """
        Accept the invitation and create membership for `user`.
        Returns the new FamilyMembership on success, or None if cannot accept.
        """
        if self.status != self.Status.PENDING or self.is_expired():
            return None

        with transaction.atomic():
            membership = self.family.add_member(user=user, role=role, added_by=self.invited_by)
            self.status = self.Status.ACCEPTED
            self.accepted_at = timezone.now()
            self.save(update_fields=["status", "accepted_at"])
            return membership
