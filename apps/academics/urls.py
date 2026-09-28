from rest_framework.routers import SimpleRouter

from . import api

router = SimpleRouter()
router.register("academic-years", api.AcademicYearViewSet, basename="academic-year")
router.register("terms", api.TermViewSet, basename="term")
router.register("levels", api.LevelViewSet, basename="level")
router.register("classes", api.ClassGroupViewSet, basename="class")
router.register("subjects", api.SubjectViewSet, basename="subject")
router.register("class-subjects", api.ClassSubjectViewSet, basename="class-subject")

urlpatterns = router.urls
