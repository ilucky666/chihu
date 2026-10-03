from django.urls import path

from core import api

urlpatterns = [
    path("openapi.json", api.openapi),
    path("me/", api.me),
    path("projects/", api.project_list),
    path("project-directory/", api.project_directory),
    path("projects/<uuid:pk>/join/", api.project_join),
    path("projects/<uuid:pk>/", api.project_detail),
    path("projects/<uuid:pk>/visits/", api.visit_list),
    path("projects/<uuid:pk>/exports/", api.export_list),
    path("visits/<uuid:pk>/", api.visit_detail),
    path("visits/<uuid:pk>/assignments/", api.assignment_list),
    path("assignments/<uuid:pk>/action/", api.assignment_action),
    path("documents/<uuid:pk>/", api.document_detail),
]
