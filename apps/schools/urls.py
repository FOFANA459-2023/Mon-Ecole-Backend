from django.urls import path
from rest_framework.routers import SimpleRouter

from .api import CurrentSchoolView
from .platform import PlatformSchoolViewSet

router = SimpleRouter()
router.register("platform/schools", PlatformSchoolViewSet, basename="platform-school")

urlpatterns = [
    path("school/", CurrentSchoolView.as_view(), name="current-school"),
    *router.urls,
]
