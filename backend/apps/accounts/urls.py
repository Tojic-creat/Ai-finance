# backend/apps/accounts/urls.py
"""
URL routes for the `accounts` app.

Exposes:
- /api/accounts/auth/token/        -> DRF token obtain (login)
- /api/accounts/register/          -> registration endpoint
- /api/accounts/me/                -> current user profile / details
- /api/accounts/profile/           -> profile CRUD (optional)
- /api/accounts/                   -> router-mounted resources (families, invitations, memberships)

This module is defensive: if `views` are not yet implemented, it provides
lightweight 501 Not Implemented responses so the API surface is discoverable
and doesn't crash imports during early development.
"""

from __future__ import annotations

from django.urls import include, path
from rest_framework import routers
from rest_framework.authtoken import views as drf_authtoken_views
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

# Try to import real views from the app. If they are absent (early dev),
# provide simple 501 stubs so the URL endpoints are available but clearly not implemented.
try:
    from . import views  # noqa: WPS433
except Exception:  # pragma: no cover - defensive fallback for early dev
    views = None


def _not_implemented(request, *args, **kwargs):
    return JsonResponse({"detail": "Not implemented"}, status=501)


# Helper to wrap a view or fallback
def _get_view(attr_name, fallback_func=None):
    if fallback_func is None:
        fallback_func = _not_implemented
    if views is None:
        return fallback_func
    return getattr(views, attr_name, fallback_func)


# Simple function-based stubs (useful fallback)
@require_http_methods(["GET", "PUT", "PATCH"])
def _me_stub(request, *args, **kwargs):
    return JsonResponse({"detail": "User detail endpoint not implemented"}, status=501)


# Router for viewsets (families, invitations, memberships, etc.)
router = routers.DefaultRouter()

# Register viewsets if they exist in views module; otherwise skip registration.
if views is not None:
    if hasattr(views, "FamilyViewSet"):
        router.register(r"families", views.FamilyViewSet, basename="family")
    if hasattr(views, "InvitationViewSet"):
        router.register(r"invitations", views.InvitationViewSet, basename="invitation")
    if hasattr(views, "MembershipViewSet"):
        router.register(r"memberships", views.MembershipViewSet, basename="membership")
    if hasattr(views, "ProfileViewSet"):
        router.register(r"profile", views.ProfileViewSet, basename="profile")
else:
    # nothing to register; router stays empty
    pass


urlpatterns = [
    # Authentication (token-based)
    path("auth/token/", drf_authtoken_views.obtain_auth_token, name="api-token-auth"),

    # Registration endpoint (class-based view expected: RegistrationView)
    path("register/", _get_view("RegistrationView", _not_implemented), name="register"),

    # Current user (read/update)
    path("me/", _get_view("UserDetailView", _me_stub), name="user-me"),

    # Profile (if implemented as view)
    path("profile/", _get_view("ProfileView", _not_implemented), name="profile"),

    # Router-mounted resources (families, invitations, memberships, etc.)
    path("", include((router.urls, "accounts"))),
]

# Export the urlpatterns for inclusion in the project-level urls.py
app_name = "accounts"
