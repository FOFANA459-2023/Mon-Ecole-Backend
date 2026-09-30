from rest_framework.routers import SimpleRouter

from . import api

router = SimpleRouter()
router.register("fee-categories", api.FeeCategoryViewSet, basename="fee-category")
router.register("fee-schedules", api.FeeScheduleViewSet, basename="fee-schedule")
router.register("student-discounts", api.StudentDiscountViewSet, basename="student-discount")
router.register("invoices", api.InvoiceViewSet, basename="invoice")

urlpatterns = router.urls
