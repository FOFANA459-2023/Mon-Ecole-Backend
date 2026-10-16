from django.urls import path
from rest_framework.routers import SimpleRouter

from . import api

router = SimpleRouter()
router.register("attendance/alerts", api.AbsenceAlertViewSet, basename="absence-alert")

urlpatterns = [
    *router.urls,
    path("attendance/day/", api.AttendanceDayView.as_view(), name="attendance-day"),
    path("attendance/register/", api.RegisterView.as_view(), name="attendance-register"),
    path("attendance/class-month/", api.ClassMonthView.as_view(), name="attendance-class-month"),
    path("attendance/absences/", api.AbsencesView.as_view(), name="attendance-absences"),
    path("attendance/students/<int:pk>/", api.StudentAttendanceView.as_view(), name="attendance-student"),
    path("attendance/staff/", api.StaffAttendanceView.as_view(), name="attendance-staff"),
]
