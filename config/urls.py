from django.contrib import admin
from django.urls import include, path

from core import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz/", views.healthz),
    path("api/v1/", include("core.api_urls")),
    path("", include("django.contrib.auth.urls")),
    path("", include("core.urls")),
]
