from rest_framework.routers import SimpleRouter

from .api import EnrollmentViewSet

router = SimpleRouter()
router.register("enrollments", EnrollmentViewSet, basename="enrollment")

urlpatterns = router.urls
