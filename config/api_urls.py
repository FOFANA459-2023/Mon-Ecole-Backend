from django.urls import include, path

urlpatterns = [
    path("", include("apps.accounts.urls")),
    path("", include("apps.schools.urls")),
    path("", include("apps.audit.urls")),
]
