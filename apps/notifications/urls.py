from rest_framework.routers import SimpleRouter

from . import api

router = SimpleRouter()
router.register("notifications", api.NotificationViewSet, basename="notification")

urlpatterns = router.urls
