# backend/apps/accounts/admin.py
"""
Admin registration for the `accounts` app.

Provides:
 - a safe, flexible admin for the project's User model (custom or default),
 - helpful admin actions: activate/deactivate/block/unblock users,
 - optional registration for family/group and invitation models if present.

This file is written defensively: if optional models (Family, Invitation, Profile)
aren't implemented yet, admin registration will be skipped without raising errors.
"""

from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.translation import gettext_lazy as _
from django.apps import apps
from django.db import transaction

User = get_user_model()


@admin.register(User)
class AccountUserAdmin(DjangoUserAdmin):
    """
    Admin for the User model.
    Uses Django's built-in UserAdmin as base, but adds a few convenient admin actions
    and sensible list/search displays that work whether your project uses username or email.
    """

    # Try to adapt to common custom user models:
    try:
        USERNAME_FIELD = getattr(User, "USERNAME_FIELD", "username")
    except Exception:
        USERNAME_FIELD = "username"

    # Common useful columns — adapt if fields absent
    list_display = (
        "id",
        USERNAME_FIELD,
        "email" if hasattr(User, "email") else None,
        "is_active",
        "is_staff",
        "is_superuser",
        "date_joined" if hasattr(User, "date_joined") else None,
    )
    # Remove None values if some attributes aren't present
    list_display = tuple(x for x in list_display if x is not None)

    list_filter = ("is_active", "is_staff", "is_superuser", "groups")
    search_fields = (USERNAME_FIELD, "email") if hasattr(User, "email") else (USERNAME_FIELD,)
    ordering = (USERNAME_FIELD,)

    actions = [
        "action_activate_users",
        "action_deactivate_users",
        "action_block_users",
        "action_unblock_users",
        "action_export_user_data",
    ]

    @admin.action(description=_("Activate selected users"))
    def action_activate_users(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, _("%d user(s) activated.") % updated, messages.SUCCESS)

    @admin.action(description=_("Deactivate selected users"))
    def action_deactivate_users(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, _("%d user(s) deactivated.") % updated, messages.WARNING)

    @admin.action(description=_("Block selected users (deactivate + mark blocked)"))
    def action_block_users(self, request, queryset):
        """
        Block user accounts. If the User model has a 'is_blocked' field it will be set,
        otherwise we just set is_active=False. Uses a transaction to update all selected users.
        """
        updated_count = 0
        with transaction.atomic():
            for user in queryset.select_for_update():
                if hasattr(user, "is_blocked"):
                    user.is_blocked = True
                user.is_active = False
                user.save()
                updated_count += 1
        self.message_user(request, _("%d user(s) blocked.") % updated_count, messages.WARNING)

    @admin.action(description=_("Unblock selected users"))
    def action_unblock_users(self, request, queryset):
        updated_count = 0
        with transaction.atomic():
            for user in queryset.select_for_update():
                if hasattr(user, "is_blocked"):
                    user.is_blocked = False
                user.is_active = True
                user.save()
                updated_count += 1
        self.message_user(request, _("%d user(s) unblocked.") % updated_count, messages.SUCCESS)

    @admin.action(description=_("Export selected users' basic data (CSV-ish)"))
    def action_export_user_data(self, request, queryset):
        """
        A simple admin action that prepares a small export of selected users.
        Rather than streaming a file from the action (which is possible), here we
        add a message with a short CSV preview that admin can copy — simple and safe.
        """
        # Build CSV header based on available fields
        fields = [USERNAME_FIELD]
        if hasattr(User, "email"):
            fields.append("email")
        if hasattr(User, "date_joined"):
            fields.append("date_joined")
        lines = [",".join(fields)]
        for u in queryset:
            vals = []
            for f in fields:
                vals.append(str(getattr(u, f, "")))
            lines.append(",".join(vals))
        preview = "\n".join(lines[:20])  # limit preview length
        self.message_user(
            request,
            _("Export preview (first %d rows):\n%s") % (min(len(lines) - 1, 19), preview),
            messages.INFO,
        )


# -------------------------
# Optional models: Family, Invitation, Profile
# -------------------------
# Register admin for optional models only if they exist in the app registry.
def _get_model(app_label: str, model_name: str):
    try:
        return apps.get_model(app_label, model_name)
    except LookupError:
        return None


Family = _get_model("accounts", "Family")
Invitation = _get_model("accounts", "Invitation")
Profile = _get_model("accounts", "Profile")  # optional profile model

if Family is not None:
    @admin.register(Family)
    class FamilyAdmin(admin.ModelAdmin):
        list_display = ("id", "name", "owner", "created_at") if hasattr(Family, "created_at") else ("id", "name", "owner")
        search_fields = ("name", "owner__email", "owner__username")
        list_filter = ("created_at",) if hasattr(Family, "created_at") else ()
        readonly_fields = ("created_at",) if hasattr(Family, "created_at") else ()

if Invitation is not None:
    @admin.register(Invitation)
    class InvitationAdmin(admin.ModelAdmin):
        list_display = ("id", "email", "invited_by", "status", "created_at") if hasattr(Invitation, "created_at") else ("id", "email", "invited_by", "status")
        search_fields = ("email", "invited_by__email")
        list_filter = ("status",)
        actions = ("action_resend_invite",)

        @admin.action(description=_("Resend invitation emails"))
        def action_resend_invite(self, request, queryset):
            # Attempt to call a resend method on invitation instances if implemented.
            resent = 0
            for inv in queryset:
                try:
                    if hasattr(inv, "resend"):
                        inv.resend()
                        resent += 1
                except Exception:
                    # ignore per-invitation errors but continue
                    continue
            self.message_user(request, _("%d invitation(s) resent.") % resent, messages.SUCCESS)

if Profile is not None:
    @admin.register(Profile)
    class ProfileAdmin(admin.ModelAdmin):
        list_display = ("id", "user", "display_name") if hasattr(Profile, "display_name") else ("id", "user")
        search_fields = ("user__email", "display_name") if hasattr(Profile, "display_name") else ("user__email",)


# If other account-related models exist, they can be registered below similarly.
