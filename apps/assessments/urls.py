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
]
