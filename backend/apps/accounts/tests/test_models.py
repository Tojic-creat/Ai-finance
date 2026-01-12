# backend/apps/accounts/tests/test_models.py
from __future__ import annotations

from datetime import timedelta
from typing import List

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from ..models import Family, FamilyMembership, Invitation, Profile

User = get_user_model()


class AccountsModelsTestCase(TestCase):
    def setUp(self) -> None:
        # Create two users for tests
        self.user1 = User.objects.create_user(
            username="alice", email="alice@example.com", password="password123"
        )
        self.user2 = User.objects.create_user(
            username="bob", email="bob@example.com", password="password123"
        )

    def test_profile_create_and_str(self):
        # Create Profile and verify fields and __str__
        prof = Profile.objects.create(user=self.user1, display_name="Alice X")
        self.assertEqual(prof.display_name, "Alice X")
        # __str__ prefers display_name
        self.assertEqual(str(prof), "Alice X")

        # If display_name empty, fallback to username/email
        prof.display_name = ""
        prof.save(update_fields=["display_name"])
        s = str(prof)
        # Should contain username or email
        self.assertTrue("alice" in s or "alice@example.com" in s)

    def test_family_add_member_and_membership_unique(self):
        # Create family and add a member; adding twice should not duplicate
        fam = Family.objects.create(name="Smiths", owner=self.user1)
        # Initially no memberships
        self.assertEqual(fam.members.count(), 0)

        # add member user2
        m1 = fam.add_member(self.user2, role=FamilyMembership.Role.PARTICIPANT, added_by=self.user1)
        self.assertIsInstance(m1, FamilyMembership)
        self.assertEqual(m1.role, FamilyMembership.Role.PARTICIPANT)
        self.assertEqual(fam.members.count(), 1)

        # add same member again (should return same membership and not create duplicate)
        m2 = fam.add_member(self.user2, role=FamilyMembership.Role.PARTICIPANT)
        self.assertEqual(m1.pk, m2.pk)
        self.assertEqual(fam.members.count(), 1)

        # add owner as member explicitly and check membership creation
        owner_membership = fam.add_member(self.user1, role=FamilyMembership.Role.OWNER, added_by=self.user1)
        self.assertEqual(owner_membership.role, FamilyMembership.Role.OWNER)
        self.assertTrue(fam.members.filter(pk=self.user1.pk).exists())

    def test_remove_member_and_prevent_remove_owner(self):
        fam = Family.objects.create(name="Team", owner=self.user1)
        # create explicit memberships
        member = FamilyMembership.objects.create(family=fam, user=self.user2, role=FamilyMembership.Role.PARTICIPANT, added_by=self.user1)
        owner_mem = FamilyMembership.objects.create(family=fam, user=self.user1, role=FamilyMembership.Role.OWNER, added_by=self.user1)

        # removing a normal member should work
        fam.remove_member(self.user2)
        self.assertFalse(FamilyMembership.objects.filter(pk=member.pk).exists())

        # attempting to remove owner should raise PermissionError
        with self.assertRaises(PermissionError):
            fam.remove_member(self.user1)

    def test_invitation_resend_and_accept_creates_membership(self):
        fam = Family.objects.create(name="Household", owner=self.user1)
        inv = Invitation.objects.create(family=fam, email=self.user2.email, invited_by=self.user1, message="Join us")

        # resend with a fake send function (simulate success)
        sent: List[bool] = []

        def fake_send_email(**kwargs):
            # simulate sending email successfully
            sent.append(True)

        ok = inv.resend(send_email_func=fake_send_email)
        self.assertTrue(ok)
        # reload from db to check resend_count updated (it uses F expression)
        inv.refresh_from_db()
        # resend_count may be an F expression increment; after save it should be int >= 1
        self.assertGreaterEqual(int(inv.resend_count), 1)

        # accept invitation as the invited user (email matches)
        membership = inv.accept(self.user2)
        self.assertIsNotNone(membership)
        # membership should exist and link to family/user
        self.assertTrue(FamilyMembership.objects.filter(family=fam, user=self.user2).exists())
        inv.refresh_from_db()
        self.assertEqual(inv.status, Invitation.Status.ACCEPTED)
        self.assertIsNotNone(inv.accepted_at)

    def test_invitation_expired_behaviour(self):
        fam = Family.objects.create(name="ExpiredFam", owner=self.user1)
        past = timezone.now() - timedelta(days=2)
        inv = Invitation.objects.create(family=fam, email=self.user2.email, invited_by=self.user1, expires_at=past)

        # is_expired should be True
        self.assertTrue(inv.is_expired())
        inv.mark_expired_if_needed()
        inv.refresh_from_db()
        self.assertEqual(inv.status, Invitation.Status.EXPIRED)

        # acceptance should return None and not create membership
        result = inv.accept(self.user2)
        self.assertIsNone(result)
        self.assertFalse(FamilyMembership.objects.filter(family=fam, user=self.user2).exists())

    def test_family_membership_str_and_unique_constraint(self):
        fam = Family.objects.create(name="UniqueTest", owner=self.user1)
        mem = FamilyMembership.objects.create(family=fam, user=self.user2, role=FamilyMembership.Role.PARTICIPANT, added_by=self.user1)
        self.assertIn(str(self.user2), str(mem))
        # Unique together constraint: attempting to create duplicate should raise IntegrityError
        from django.db import IntegrityError

        with self.assertRaises(IntegrityError):
            FamilyMembership.objects.create(family=fam, user=self.user2, role=FamilyMembership.Role.PARTICIPANT, added_by=self.user1)
