from rest_framework.routers import SimpleRouter

from . import api

router = SimpleRouter()
router.register("departments", api.DepartmentViewSet, basename="department")
router.register("announcements", api.AnnouncementViewSet, basename="announcement")
router.register("calendar-events", api.CalendarEventViewSet, basename="calendar-event")

urlpatterns = router.urls
