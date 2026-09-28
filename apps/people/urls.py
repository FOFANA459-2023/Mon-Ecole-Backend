from rest_framework.routers import SimpleRouter

from . import api

router = SimpleRouter()
router.register("students", api.StudentViewSet, basename="student")
router.register("guardians", api.GuardianViewSet, basename="guardian")
router.register("staff", api.StaffViewSet, basename="staff")

urlpatterns = router.urls
