# backend/apps/accounts/forms.py
"""
Forms for the `accounts` app.

Includes:
- UserRegistrationForm: sign-up (email + password) compatible with custom user models.
- UserUpdateForm: editing basic profile fields.
- LoginForm: thin wrapper around AuthenticationForm (keeps consistency).
- FamilyInviteForm: invite a user to a family by email.
- AttachmentValidator / FileFieldWithValidation: reusable validator for user-uploaded files
  (used elsewhere: adjustments, transactions, profile avatars).
"""

from __future__ import annotations

import imghdr
from typing import Iterable, Optional

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordResetForm,
    SetPasswordForm,
    UserCreationForm,
)
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

User = get_user_model()


# -------------------------
# Helpers / validators
# -------------------------
def _get_max_attachment_size() -> int:
    # fallback to 10 MB if not configured
    return int(getattr(settings, "MAX_ATTACHMENT_SIZE", 10 * 1024 * 1024))


ALLOWED_CONTENT_TYPES = ("image/jpeg", "image/png", "application/pdf")
ALLOWED_IMAGE_EXTS = ("jpeg", "png", "jpg")


def validate_attachment_file(file) -> None:
    """
    Validate uploaded file for allowed content types and size.

    Rules:
    - Content-Type must be one of ALLOWED_CONTENT_TYPES (best-effort check).
    - Size must be <= MAX_ATTACHMENT_SIZE.
    - For images, do a light validation using imghdr to detect actual image type.
    """
    max_size = _get_max_attachment_size()
    content_type = getattr(file, "content_type", None)
    size = getattr(file, "size", None)

    if size is not None and size > max_size:
        raise ValidationError(
            _("File is too large (max %(max)s bytes)."),
            code="file_too_large",
            params={"max": max_size},
        )

    if content_type:
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise ValidationError(
                _("Unsupported file type: %(type)s. Allowed: jpeg, png, pdf."),
                code="invalid_content_type",
                params={"type": content_type},
            )
    # Additional image check for images (imghdr)
    try:
        # read a bit of file to guess type; file may be InMemoryUploadedFile or TemporaryUploadedFile
        file.seek(0)
        header = file.read(512)
        file.seek(0)
        kind = imghdr.what(None, h=header)
        if kind is not None and kind not in ALLOWED_IMAGE_EXTS:
            raise ValidationError(_("Corrupt or unsupported image file."), code="invalid_image")
    except Exception:
        # Don't fail on imghdr errors; primary validation is content_type and size.
        pass


class AttachmentField(forms.FileField):
    default_validators = [validate_attachment_file]

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("required", False)
        super().__init__(*args, **kwargs)


# -------------------------
# User forms
# -------------------------
class UserRegistrationForm(UserCreationForm):
    """
    Registration form using the project's User model.
    Accepts email (required if the User model uses email as identifier),
    password1/password2 (from UserCreationForm) and optional profile fields.
    """

    email = forms.EmailField(label=_("Email"), required=True)
    first_name = forms.CharField(label=_("First name"), required=False)
    last_name = forms.CharField(label=_("Last name"), required=False)

    class Meta:
        model = User
        # include username only if the custom user model has it
        fields = ("email", "first_name", "last_name", "password1", "password2")
        # If your User model uses username as USERNAME_FIELD, the developer can add it back in project-specific code.

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if email and User.objects.filter(email__iexact=email).exists():
            raise ValidationError(_("A user with that email already exists."), code="email_exists")
        return email


class UserUpdateForm(forms.ModelForm):
    """
    Basic user update form. Use for profile editing.
    """

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email")
        # You can make email read-only depending on your auth strategy.
        widgets = {"email": forms.EmailInput(attrs={"autocomplete": "email"})}

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if not email:
            raise ValidationError(_("Email is required."))
        qs = User.objects.filter(email__iexact=email).exclude(pk=getattr(self.instance, "pk", None))
        if qs.exists():
            raise ValidationError(_("Email is already in use by another account."))
        return email


class SimpleLoginForm(AuthenticationForm):
    """
    Thin wrapper around Django's AuthenticationForm to allow future customizations.
    Keeps default behavior (username/password) but ensures nice labels.
    """

    username = forms.CharField(label=_("Email or username"), widget=forms.TextInput(attrs={"autofocus": True}))
    password = forms.CharField(label=_("Password"), strip=False, widget=forms.PasswordInput)


# -------------------------
# Password / Reset forms (optional wrappers)
# -------------------------
class UserPasswordResetForm(PasswordResetForm):
    """
    Optionally override if you want to restrict reset behavior (e.g. only allow confirmed emails).
    For now, use default behavior.
    """
    pass


class UserSetPasswordForm(SetPasswordForm):
    """
    Wrapper over SetPasswordForm for unified import surface.
    """
    pass


# -------------------------
# Family / Invitation forms
# -------------------------
class FamilyInviteForm(forms.Form):
    """
    Invite a user by email to a family/group.
    """
    email = forms.EmailField(label=_("Email"), required=True)
    message = forms.CharField(label=_("Message"), required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def clean_email(self):
        email = self.cleaned_data.get("email")
        # Optionally prevent inviting existing user/member - real logic depends on Family model
        return email


# -------------------------
# Optional ProfileForm (if a Profile model exists)
# -------------------------
try:
    # Avoid hard dependency on Profile model; import only if app/model exists.
    from django.apps import apps

    ProfileModel = apps.get_model("accounts", "Profile")
except Exception:
    ProfileModel = None


if ProfileModel is not None:  # pragma: no cover - auto-detect in runtime
    class ProfileForm(forms.ModelForm):
        avatar = AttachmentField(label=_("Avatar (jpg/png, <= %s MB)" % (_get_max_attachment_size() // (1024 * 1024))))
        bio = forms.CharField(label=_("Bio"), required=False, widget=forms.Textarea(attrs={"rows": 3}))

        class Meta:
            model = ProfileModel
            fields = ("avatar", "bio",)

else:
    ProfileForm = None  # type: ignore[assignment]
