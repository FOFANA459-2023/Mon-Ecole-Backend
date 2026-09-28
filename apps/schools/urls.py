from django.urls import path

from .api import CurrentSchoolView

urlpatterns = [
    path("school/", CurrentSchoolView.as_view(), name="current-school"),
]
