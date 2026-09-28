from django.urls import include, path

urlpatterns = [
    path("", include("apps.accounts.urls")),
    path("", include("apps.schools.urls")),
    path("", include("apps.audit.urls")),
    path("", include("apps.academics.urls")),
    path("", include("apps.people.urls")),
    path("", include("apps.enrollments.urls")),
    path("", include("apps.documents.urls")),
    path("", include("apps.search.urls")),
    path("", include("apps.dashboard.urls")),
    path("", include("apps.imports.urls")),
]
