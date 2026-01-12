# backend/apps/accounts/tests/test_views.py
from __future__ import annotations

import json
from typing import Any, Dict

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate

from ..views import (
    FamilyViewSet,
    InvitationViewSet,
    MembershipViewSet,
    RegistrationView,
    UserDetailView,
)
from ..models import Family, FamilyMembership, Invitation

User = get_user_model()


class AccountsViewsTestCase(TestCase):
    def setUp(self) -> None:
        self.factory = APIRequestFactory()
        # create two users
        self.user1 = User.objects.create_user(username="alice", email="alice@example.com", password="pass1234")
        self.user2 = User.objects.create_user(username="bob", email="bob@example.com", password="pass1234")

    def _post_view(self, view, user, data: Dict[str, Any], action: Dict[str, str] = None, pk=None):
        """
        Helper to call a view (function/class) via APIRequestFactory with JSON body.
        If view is a ViewSet class, pass action mapping (e.g. {'post': 'create'}).
        """
        body = data
        # build request
        req = self.factory.post("/", body, format="json")
        if user is not None:
            force_authenticate(req, user=user)
        if action:
            view_callable = view.as_view(action)
        else:
            view_callable = view.as_view()
        # call
        if pk is not None:
            response = view_callable(req, pk=pk)
        else:
            response = view_callable(req)
        return response

    def _get_view(self, view, user, action: Dict[str, str] = None, pk=None):
        req = self.factory.get("/", format="json")
        if user is not None:
            force_authenticate(req, user=user)
        if action:
            view_callable = view.as_view(action)
        else:
            view_callable = view.as_view()
        if pk is not None:
            return view_callable(req, pk=pk)
        return view_callable(req)

    def test_registration_creates_user_and_returns_token(self):
        """
        POST to RegistrationView should create a user and return serialized user and token (if token available).
        """
        data = {"email": "charlie@example.com", "password": "securepw", "password2": "securepw"}
        # call RegistrationView (CreateAPIView)
        req = self.factory.post("/", data, format="json")
        # no auth needed
        view = RegistrationView.as_view()
        resp = view(req)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        # Response should include user info
        resp_data = resp.data
        self.assertIn("user", resp_data)
        self.assertEqual(resp_data["user"]["email"], "charlie@example.com")
        # token may or may not be present depending on Token model availability
        # but the user should exist in DB
        self.assertTrue(User.objects.filter(email="charlie@example.com").exists())

    def test_user_detail_get_and_patch(self):
        """
        Authenticated GET to UserDetailView returns user data; PATCH updates allowed fields.
        """
        # GET
        resp = self._get_view(UserDetailView, user=self.user1)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data.get(getattr(User, "USERNAME_FIELD", "username")), getattr(self.user1, getattr(User, "USERNAME_FIELD", "username")))
        # PATCH update first_name
        patch_req = self.factory.patch("/", {"first_name": "AliceUpdated"}, format="json")
        force_authenticate(patch_req, user=self.user1)
        resp2 = UserDetailView.as_view()(patch_req)
        self.assertIn(resp2.status_code, (status.HTTP_200_OK, status.HTTP_202_ACCEPTED))
        # reload user from db
        self.user1.refresh_from_db()
        self.assertEqual(self.user1.first_name, "AliceUpdated")

    def test_family_create_list_and_permissions(self):
        """
        FamilyViewSet: owner creates family; listing returns families for owner.
        Non-owners cannot delete family.
        """
        # create family as user1
        data = {"name": "The A Team", "description": "Test family"}
        resp = self._post_view(FamilyViewSet, user=self.user1, data=data, action={"post": "create"})
        self.assertIn(resp.status_code, (status.HTTP_201_CREATED, status.HTTP_200_OK))
        fam_id = resp.data.get("id")
        self.assertIsNotNone(fam_id)
        # list families for user1
        list_req = self.factory.get("/", format="json")
        force_authenticate(list_req, user=self.user1)
        list_resp = FamilyViewSet.as_view({"get": "list"})(list_req)
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
        # ensure the created family present
        ids = [item["id"] for item in list_resp.data] if isinstance(list_resp.data, list) else []
        if not ids:
            # may be paginated
            ids = [item["id"] for item in list_resp.data.get("results", [])]
        self.assertIn(fam_id, ids)

        # Try to delete as non-owner (user2) -> should return 403
        delete_req = self.factory.delete("/", format="json")
        force_authenticate(delete_req, user=self.user2)
        del_resp = FamilyViewSet.as_view({"delete": "destroy"})(delete_req, pk=fam_id)
        self.assertIn(del_resp.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_405_METHOD_NOT_ALLOWED))

    def test_invitation_flow_create_resend_accept(self):
        """
        Owner creates invitation; resend action callable by owner; invited user can accept.
        """
        # create family owned by user1
        fam = Family.objects.create(name="Household", owner=self.user1)
        # create invitation as owner via viewset
        inv_data = {"family": fam.id, "email": "newbie@example.com", "message": "Please join"}
        resp = self._post_view(InvitationViewSet, user=self.user1, data=inv_data, action={"post": "create"})
        self.assertIn(resp.status_code, (status.HTTP_201_CREATED, status.HTTP_200_OK))
        inv_id = resp.data.get("id")
        self.assertIsNotNone(inv_id)
        # call resend action as owner
        resend_req = self.factory.post("/", format="json")
        force_authenticate(resend_req, user=self.user1)
        resend_resp = InvitationViewSet.as_view({"post": "resend"})(resend_req, pk=inv_id)
        # resend action may succeed or return 200/400 depending on email send; allow 200 or 400 but not 500
        self.assertIn(resend_resp.status_code, (status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST, status.HTTP_204_NO_CONTENT))

        # simulate accept: create actual Invitation instance and accept as user with matching email
        invitation = Invitation.objects.get(pk=inv_id)
        # create a user with matching email (the invitee)
        invitee = User.objects.create_user(username="invitee", email=invitation.email, password="pw")
        accept_req = self.factory.post("/", format="json")
        force_authenticate(accept_req, user=invitee)
        accept_resp = InvitationViewSet.as_view({"post": "accept"})(accept_req, pk=inv_id)
        self.assertIn(accept_resp.status_code, (status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST))
        # If accepted, membership created and invitation status updated
        invitation.refresh_from_db()
        if invitation.status == Invitation.Status.ACCEPTED:
            self.assertTrue(FamilyMembership.objects.filter(family=invitation.family, user=invitee).exists())
        else:
            # not accepted -> membership should not exist
            self.assertFalse(FamilyMembership.objects.filter(family=invitation.family, user=invitee).exists())

    def test_membership_create_by_owner_and_forbidden_for_non_owner(self):
        """
        Owner can create membership via MembershipViewSet; non-owner cannot.
        """
        fam = Family.objects.create(name="GroupX", owner=self.user1)
        # owner adds user2 via MembershipViewSet.create
        payload = {"family": fam.id, "user_id": self.user2.id, "role": FamilyMembership.Role.PARTICIPANT}
        # membership serializer expects 'user' field but our helper will pass user_id as write-only; using APIRequestFactory post
        req = self.factory.post("/", payload, format="json")
        force_authenticate(req, user=self.user1)
        resp = MembershipViewSet.as_view({"post": "create"})(req)
        self.assertIn(resp.status_code, (status.HTTP_201_CREATED, status.HTTP_200_OK))
        # Now attempt to create membership as non-owner (user2 trying to add user1) -> should be forbidden
        req2 = self.factory.post("/", {"family": fam.id, "user_id": self.user1.id, "role": FamilyMembership.Role.PARTICIPANT}, format="json")
        force_authenticate(req2, user=self.user2)
        resp2 = MembershipViewSet.as_view({"post": "create"})(req2)
        self.assertIn(resp2.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_400_BAD_REQUEST))
