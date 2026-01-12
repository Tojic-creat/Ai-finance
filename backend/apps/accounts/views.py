# backend/apps/accounts/views.py
"""
Views for the `accounts` app (DRF).

Provides:
- RegistrationView       : POST /api/accounts/register/       (create user + token)
- UserDetailView         : GET/PUT/PATCH /api/accounts/me/    (current user)
- ProfileView            : GET/PUT/PATCH /api/accounts/profile/
- FamilyViewSet          : /api/accounts/families/             (CRUD; scoped to user's families)
- InvitationViewSet      : /api/accounts/invitations/         (invite flow)
- MembershipViewSet      : /api/accounts/memberships/         (manage memberships)

Permissions / rules are intentionally conservative and simple:
- Most endpoints require authentication.
- Family create -> owner=request.user.
- Only family owner may delete family or create invitations for that family.
- Membership creation/removal constrained to family owner (or via Invitation.accept).
"""

from __future__ import annotations

from typing import Any

from django.shortcuts import get_object_or_404
from rest_framework import (
    decorators,
    exceptions,
    generics,
    mixins,
    permissions,
    response,
    status,
    viewsets,
)
from rest_framework.authtoken.models import Token

from .models import Family, FamilyMembership, Invitation, Profile
from .serializers import (
    FamilyMembershipSerializer,
    FamilySerializer,
    InvitationSerializer,
    ProfileSerializer,
    UserRegistrationSerializer,
    UserSerializer,
)

# Use Django's configured user model
from django.contrib.auth import get_user_model

User = get_user_model()


# -------------------------
# Registration
# -------------------------
class RegistrationView(generics.CreateAPIView):
    """
    Register a new user.

    Returns:
    - 201 Created with serialized user data and token (if token auth enabled).
    """
    serializer_class = UserRegistrationSerializer
    permission_classes = (permissions.AllowAny,)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        # create token if token auth is enabled
        token = None
        try:
            token_obj, _ = Token.objects.get_or_create(user=user)
            token = token_obj.key
        except Exception:
            token = None

        user_data = UserSerializer(user, context={"request": request}).data
        data = {"user": user_data}
        if token:
            data["token"] = token

        headers = self.get_success_headers(serializer.data)
        return response.Response(data, status=status.HTTP_201_CREATED, headers=headers)


# -------------------------
# Current user
# -------------------------
class UserDetailView(generics.RetrieveUpdateAPIView):
    """
    Retrieve or update current authenticated user's basic info.
    """
    serializer_class = UserSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def get_object(self) -> User:
        return self.request.user

    def patch(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)


# -------------------------
# Profile
# -------------------------
class ProfileView(generics.RetrieveUpdateAPIView):
    """
    Retrieve or update the profile for the current user.
    Creates a Profile instance if missing.
    """
    serializer_class = ProfileSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def get_object(self) -> Profile:
        user = self.request.user
        profile, _ = Profile.objects.get_or_create(user=user)
        return profile


# -------------------------
# Family ViewSet
# -------------------------
class IsFamilyOwnerOrReadOnly(permissions.BasePermission):
    """
    Permission: allow full access to the owner, read-only to other authenticated members.
    """

    def has_object_permission(self, request, view, obj: Family) -> bool:
        # allow safe methods for authenticated members
        if request.method in permissions.SAFE_METHODS:
            # member if user is in family.members
            return obj.members.filter(pk=request.user.pk).exists() or obj.owner == request.user
        # write methods only allowed for owner
        return obj.owner == request.user


class FamilyViewSet(viewsets.ModelViewSet):
    """
    CRUD for Family.
    - list: families where the user is owner or member
    - create: user becomes owner
    - retrieve: allowed if user is member or owner
    - update/partial_update: owner only
    - destroy: owner only
    """
    serializer_class = FamilySerializer
    permission_classes = (permissions.IsAuthenticated, IsFamilyOwnerOrReadOnly)

    def get_queryset(self):
        user = self.request.user
        # families where user is owner OR a member
        return Family.objects.filter(models_Q := (Family.owner == user) | (Family.members.through.objects.filter(user_id=user.id).exists()) )  # type: ignore

    # The above Q expression using .through().exists() is tricky in ORM; use a safer approach:
    def get_queryset(self):
        user = self.request.user
        return Family.objects.filter(models_Q := (Family.owner == user) | Family.members.filter(pk=user.pk)).distinct()

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    def perform_destroy(self, instance: Family):
        if instance.owner != self.request.user:
            raise exceptions.PermissionDenied("Only family owner can delete the family.")
        instance.delete()


# -------------------------
# Invitation ViewSet
# -------------------------
class InvitationViewSet(viewsets.ModelViewSet):
    """
    Manage invitations. Only family owners can create invitations for their family.
    List returns invitations related to families the user owns or invitations sent to the user's email.
    """
    serializer_class = InvitationSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def get_queryset(self):
        user = self.request.user
        # invitations for families user owns OR invitations sent to the user's email OR invitations created by user
        qs = Invitation.objects.filter(family__owner=user) | Invitation.objects.filter(email__iexact=getattr(user, "email", ""))
        return qs.distinct()

    def perform_create(self, serializer):
        # Ensure request.user is owner of the family to invite into
        family = serializer.validated_data.get("family")
        if family.owner != self.request.user:
            raise exceptions.PermissionDenied("Only family owner can invite members.")
        serializer.save(invited_by=self.request.user)

    @decorators.action(detail=True, methods=["post"], permission_classes=[permissions.IsAuthenticated])
    def resend(self, request, pk=None):
        invitation = self.get_object()
        if invitation.family.owner != request.user and not request.user.is_staff:
            raise exceptions.PermissionDenied("Only owner or staff can resend invitation.")
        ok = invitation.resend()
        if ok:
            return response.Response({"detail": "Invitation resent."}, status=status.HTTP_200_OK)
        return response.Response({"detail": "Resend failed."}, status=status.HTTP_400_BAD_REQUEST)

    @decorators.action(detail=True, methods=["post"], permission_classes=[permissions.IsAuthenticated])
    def accept(self, request, pk=None):
        """
        Accept invitation as the authenticated user (if token matches or user email matches).
        """
        invitation = self.get_object()
        # Basic checks: email match OR staff
        if getattr(request.user, "email", None) and invitation.email.lower() != request.user.email.lower():
            return response.Response({"detail": "Invitation not for this user."}, status=status.HTTP_403_FORBIDDEN)
        membership = invitation.accept(request.user)
        if membership is None:
            return response.Response({"detail": "Invitation cannot be accepted."}, status=status.HTTP_400_BAD_REQUEST)
        return response.Response({"detail": "Invitation accepted."}, status=status.HTTP_200_OK)


# -------------------------
# Membership ViewSet
# -------------------------
class MembershipViewSet(viewsets.ModelViewSet):
    """
    Manage FamilyMemberships.
    Typically only family owner or the member themselves may change membership (role).
    Creation through API is restricted: use Family.add_member (owner action) or Invitation.accept.
    """
    serializer_class = FamilyMembershipSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def get_queryset(self):
        user = self.request.user
        # show memberships related to families the user is part of or owns
        return FamilyMembership.objects.filter(family__members=user) | FamilyMembership.objects.filter(user=user)  # union

    def perform_create(self, serializer):
        # Only family owner can create membership via API
        family = serializer.validated_data.get("family")
        user_obj = serializer.validated_data.get("user")
        if family.owner != self.request.user:
            raise exceptions.PermissionDenied("Only family owner can add members directly.")
        serializer.save(added_by=self.request.user)

    def perform_destroy(self, instance: FamilyMembership):
        # prevent removing owner via API
        if instance.role == FamilyMembership.Role.OWNER:
            raise exceptions.PermissionDenied("Cannot remove family owner.")
        # Allow removal if requester is owner or the member themselves
        if instance.family.owner != self.request.user and instance.user != self.request.user:
            raise exceptions.PermissionDenied("Only owner or the member themselves can remove membership.")
        instance.delete()
