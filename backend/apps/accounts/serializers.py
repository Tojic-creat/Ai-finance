# backend/apps/accounts/serializers.py
"""
DRF serializers for the `accounts` app.

Includes:
- UserSerializer (read-only / public fields for users)
- UserRegistrationSerializer (for sign-up)
- ProfileSerializer (one-to-one extension)
- FamilySerializer (+ nested members count)
- FamilyMembershipSerializer
- InvitationSerializer
- Small helpers and validators where appropriate

These serializers are intentionally defensive: they check for optional fields
on the User/Profile models so they work with a plain Django User or with a
custom user model that uses email as the USERNAME_FIELD.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from .models import Family, FamilyMembership, Invitation, Profile

User = get_user_model()


# -------------------------
# Profile serializer
# -------------------------
class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Profile
        fields = (
            "display_name",
            "avatar",
            "bio",
            "timezone",
            "language",
            "opt_in_ml",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("created_at", "updated_at")


# -------------------------
# User serializer(s)
# -------------------------
class UserSerializer(serializers.ModelSerializer):
    """
    Public representation of a user.
    Includes a nested profile (if present) and safe fields only.
    """

    profile = serializers.SerializerMethodField()
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        # Keep fields minimal and safe for API exposure
        fields = (
            "id",
            getattr(User, "USERNAME_FIELD", "username"),
            "email",
            "is_active",
            "is_staff",
            "is_superuser",
            "date_joined",
            "profile",
            "display_name",
        )
        read_only_fields = ("id", "is_active", "is_staff", "is_superuser", "date_joined", "profile", "display_name")

    def get_profile(self, obj: User) -> Optional[Dict[str, Any]]:
        try:
            prof = getattr(obj, "profile", None)
            if prof is None:
                return None
            return ProfileSerializer(prof, context=self.context).data
        except Exception:
            return None

    def get_display_name(self, obj: User) -> str:
        # Prefer profile.display_name, fallback to first_name + last_name or USERNAME
        prof = getattr(obj, "profile", None)
        if prof and getattr(prof, "display_name", ""):
            return prof.display_name
        first = getattr(obj, "first_name", "") or ""
        last = getattr(obj, "last_name", "") or ""
        if first or last:
            return f"{first} {last}".strip()
        username_field = getattr(User, "USERNAME_FIELD", "username")
        return str(getattr(obj, username_field, obj.pk))


class UserRegistrationSerializer(serializers.ModelSerializer):
    """
    Serializer for user registration.
    Creates the user and sets password appropriately.
    Accepts email + password1/password2 (or password only depending on front-end).
    """

    password = serializers.CharField(write_only=True, required=True, style={"input_type": "password"})
    password2 = serializers.CharField(write_only=True, required=False, style={"input_type": "password"})
    # optional profile fields
    first_name = serializers.CharField(required=False, allow_blank=True)
    last_name = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = User
        fields = (
            getattr(User, "USERNAME_FIELD", "username"),
            "email",
            "first_name",
            "last_name",
            "password",
            "password2",
        )
        extra_kwargs = {
            getattr(User, "USERNAME_FIELD", "username"): {"required": False},
            "email": {"required": True},
        }

    def validate(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        pw = attrs.get("password")
        pw2 = attrs.get("password2")
        if pw2 is not None and pw != pw2:
            raise serializers.ValidationError({"password2": _("Passwords do not match.")})
        # email uniqueness
        email = attrs.get("email")
        if email and User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError({"email": _("A user with that email already exists.")})
        return attrs

    def create(self, validated_data: Dict[str, Any]) -> User:
        password = validated_data.pop("password")
        validated_data.pop("password2", None)
        username_field = getattr(User, "USERNAME_FIELD", "username")
        # Ensure we don't pass empty username if model requires it
        if username_field not in validated_data or not validated_data.get(username_field):
            # if the user model uses email as username, set it accordingly
            if username_field == "email":
                validated_data[username_field] = validated_data.get("email")
            else:
                # fallback: set username to email local-part
                email = validated_data.get("email", "")
                if email and "@" in email:
                    validated_data[username_field] = email.split("@")[0]
        user = User(**{k: v for k, v in validated_data.items() if k in {f.name for f in User._meta.fields}})
        user.set_password(password)
        user.full_clean()
        user.save()
        # Optionally create a Profile instance if Profile model exists via signal or here:
        try:
            Profile.objects.get_or_create(user=user)
        except Exception:
            pass
        return user


# -------------------------
# Family / Membership serializers
# -------------------------
class FamilyMembershipSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(write_only=True, source="user", queryset=User.objects.all(), required=False)

    class Meta:
        model = FamilyMembership
        fields = ("id", "family", "user", "user_id", "role", "added_by", "added_at", "updated_at")
        read_only_fields = ("id", "user", "added_by", "added_at", "updated_at")

    def create(self, validated_data: Dict[str, Any]) -> FamilyMembership:
        # family should be provided in validated_data (or from context)
        return super().create(validated_data)


class FamilySerializer(serializers.ModelSerializer):
    owner = UserSerializer(read_only=True)
    owner_id = serializers.PrimaryKeyRelatedField(write_only=True, source="owner", queryset=User.objects.all(), required=False)
    members_count = serializers.SerializerMethodField()
    memberships = FamilyMembershipSerializer(source="memberships", many=True, read_only=True)

    class Meta:
        model = Family
        fields = ("id", "name", "description", "owner", "owner_id", "members_count", "memberships", "created_at", "updated_at")
        read_only_fields = ("id", "owner", "members_count", "memberships", "created_at", "updated_at")

    def get_members_count(self, obj: Family) -> int:
        return obj.members.count()

    def create(self, validated_data: Dict[str, Any]) -> Family:
        request = self.context.get("request")
        owner = None
        if request and hasattr(request, "user") and request.user and request.user.is_authenticated:
            owner = request.user
        elif validated_data.get("owner"):
            owner = validated_data.pop("owner")
        if owner is None:
            raise serializers.ValidationError({"owner": _("Owner is required.")})
        family = Family.objects.create(owner=owner, **{k: v for k, v in validated_data.items() if k != "owner"})
        # add owner as membership with role OWNER
        FamilyMembership.objects.create(family=family, user=owner, role=FamilyMembership.Role.OWNER, added_by=owner)
        return family


# -------------------------
# Invitation serializer
# -------------------------
class InvitationSerializer(serializers.ModelSerializer):
    invited_by = UserSerializer(read_only=True)
    invited_by_id = serializers.PrimaryKeyRelatedField(write_only=True, source="invited_by", queryset=User.objects.all(), required=False)
    family = serializers.PrimaryKeyRelatedField(queryset=Family.objects.all())

    class Meta:
        model = Invitation
        fields = (
            "id",
            "family",
            "email",
            "invited_by",
            "invited_by_id",
            "token",
            "message",
            "status",
            "created_at",
            "accepted_at",
            "expires_at",
            "resend_count",
        )
        read_only_fields = ("token", "status", "created_at", "accepted_at", "resend_count")

    def create(self, validated_data: Dict[str, Any]) -> Invitation:
        request = self.context.get("request")
        invited_by = None
        if request and hasattr(request, "user") and request.user.is_authenticated:
            invited_by = request.user
        elif validated_data.get("invited_by"):
            invited_by = validated_data.pop("invited_by")
        invitation = Invitation.objects.create(invited_by=invited_by, **validated_data)
        return invitation

    def to_representation(self, instance: Invitation) -> Dict[str, Any]:
        # token is read-only but include it for the creator
        data = super().to_representation(instance)
        request = self.context.get("request")
        if request and hasattr(request, "user") and request.user and (request.user == instance.invited_by or request.user.is_staff):
            data["token"] = instance.token
        else:
            data.pop("token", None)
        return data
