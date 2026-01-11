# backend/finassist/urls.py
"""
Root URL configuration for FinAssist.

- Admin UI at /admin/
- API mounted under /api/
- Health check at /health/
- (Optional) Swagger / Redoc UI when drf-yasg is available and DEBUG=True
- Serves static files in DEBUG via django.conf.urls.static.static
"""

from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path, re_path
from django.views.generic import TemplateView

# Health check view (simple, safe)


def health_check(request):
    """
    Basic health check used by uptime monitors / load balancers.
    Returns 200 OK when Django is up; can be extended to probe DB/Redis.
    """
    return JsonResponse({"status": "ok", "django_settings": settings.ENVIRONMENT if getattr(settings, "ENVIRONMENT", None) else "unknown"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health_check, name="health"),
    # Frontend entry (could be dashboards / home page rendered by Django templates)
    path("", TemplateView.as_view(template_name="index.html"), name="home"),
    # API: include app routes (each app should expose its own urls.py)
    path("api/", include(("apps.finances.urls", "finances"), namespace="api-finances")),
    # Auth endpoints from DRF (optional)
    path("api-auth/", include("rest_framework.urls", namespace="rest_framework")),
]

# API schema / docs (drf-yasg) — enabled in DEBUG or if explicitly allowed via settings
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
