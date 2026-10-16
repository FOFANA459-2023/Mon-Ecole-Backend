from rest_framework.routers import SimpleRouter

from . import api

router = SimpleRouter()
router.register("jobs", api.JobViewSet, basename="job")

urlpatterns = router.urls
