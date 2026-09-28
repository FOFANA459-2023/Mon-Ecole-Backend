from rest_framework.routers import SimpleRouter

from .api import DocumentViewSet

router = SimpleRouter()
router.register("documents", DocumentViewSet, basename="document")

urlpatterns = router.urls
