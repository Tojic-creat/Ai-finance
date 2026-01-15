# backend/finassist/urls.py
"""
Root URL configuration for FinAssist.

- Admin UI at /admin/
- Health check at /health/
- Public landing page at /
- Finances JSON placeholder at /finances/
- API mounted under /api/
- (Optional) Swagger / Redoc UI when drf-yasg is available and DEBUG=True
- Serves static files in DEBUG via django.conf.urls.static.static
"""

from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path, re_path
from django.views.generic import TemplateView

# Import the UI view we added (defensive import)
try:
    from apps.finances import views as finances_views  # noqa: WPS433
except Exception:
    finances_views = None


def health_check(request):
    """
    Basic health check used by uptime monitors / load balancers.
    Returns 200 OK when Django is up; can be extended to probe DB/Redis.
    """
    return JsonResponse({
        "status": "ok",
        "django_settings": settings.ENVIRONMENT if getattr(settings, "ENVIRONMENT", None) else "unknown"
    })


def finances_home(request):
    """
    Public "home" for the finances area — lightweight JSON page.
    Keeps /finances/ available as a public placeholder and avoids redirecting
    anonymous users into auth-protected DRF viewsets (which results in 403).
    """
    return JsonResponse({
        "app": "FinAssist",
        "message": "This endpoint is a placeholder for the Finances web UI.",
        "links": {
            "admin": "/admin/",
            "api_root": "/api/",
            "api_finances": "/api/accounts/ (and other /api/ endpoints)",
        },
        "note": "API endpoints require authentication (403 if unauthenticated)."
    })


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health_check, name="health"),

    # Dashboard (UI) - if the view is available
    # Access: /dashboard/ (named 'dashboard' — used by base.html)
    path("dashboard/", (finances_views.dashboard if finances_views is not None else TemplateView.as_view(
        template_name="index.html")), name="dashboard"),

    # Root: render landing page (index.html)
    path("", TemplateView.as_view(template_name="index.html"), name="home"),

    # Keep a simple public entry point for the web UI (JSON placeholder)
    path("finances/", finances_home, name="finances-home"),

    # API: include the finances app router (keeps API under /api/...)
    path("api/", include(("apps.finances.urls", "finances"), namespace="api-finances")),

    # DRF browsable auth (login/logout)
    path("api-auth/", include("rest_framework.urls", namespace="rest_framework")),
]

# drf-yasg docs (swagger/redoc) — enabled in DEBUG or via ENABLE_API_DOCS
if settings.DEBUG or getattr(settings, "ENABLE_API_DOCS", False):
    try:
        from drf_yasg import openapi
        from drf_yasg.views import get_schema_view
        from rest_framework import permissions

        schema_view = get_schema_view(
            openapi.Info(
                title="FinAssist API",
                default_version="v1",
                description="API documentation for FinAssist (backend + ML integrations)",
                contact=openapi.Contact(email="devops@example.com"),
            ),
            public=True,
            permission_classes=(permissions.AllowAny,),
        )

        urlpatterns += [
            re_path(r"^swagger(?P<format>\.json|\.yaml)$", schema_view.without_ui(
                cache_timeout=0), name="schema-json"),
            path("swagger/", schema_view.with_ui("swagger",
                 cache_timeout=0), name="schema-swagger-ui"),
            path("redoc/", schema_view.with_ui("redoc",
                 cache_timeout=0), name="schema-redoc"),
        ]
    except Exception:
        # drf-yasg not installed — skip docs silently
        pass

# Serve static/media in DEBUG using django views (convenience for local dev)
if settings.DEBUG:
    from django.conf.urls.static import static

    urlpatterns += static(settings.STATIC_URL,
                          document_root=getattr(settings, "STATIC_ROOT", None))
    urlpatterns += static(getattr(settings, "MEDIA_URL", "/media/"),
                          document_root=getattr(settings, "MEDIA_ROOT", None))
