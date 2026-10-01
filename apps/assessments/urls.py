from django.urls import path
from rest_framework.routers import SimpleRouter

from . import api

router = SimpleRouter()
router.register("grading-scales", api.GradingScaleViewSet, basename="grading-scale")
router.register("gradebooks", api.GradebookViewSet, basename="gradebook")
router.register("grade-categories", api.GradeCategoryViewSet, basename="grade-category")
router.register("assessments", api.AssessmentViewSet, basename="assessment")

urlpatterns = [
    *router.urls,
    path("class-results/", api.ClassResultsView.as_view(), name="class-results"),
    path("report-cards/", api.ReportCardView.as_view(), name="report-cards"),
    path("report-comments/", api.ReportCommentsView.as_view(), name="report-comments"),
    path("student-results/<int:pk>/", api.StudentResultsView.as_view(), name="student-results"),
]
