from rest_framework.routers import SimpleRouter

from . import api

router = SimpleRouter()
router.register("fee-categories", api.FeeCategoryViewSet, basename="fee-category")
router.register("fee-schedules", api.FeeScheduleViewSet, basename="fee-schedule")
router.register("student-discounts", api.StudentDiscountViewSet, basename="student-discount")
router.register("invoices", api.InvoiceViewSet, basename="invoice")
router.register("payments", api.PaymentViewSet, basename="payment")
router.register("student-accounts", api.StudentAccountViewSet, basename="student-account")
router.register("expenses", api.ExpenseViewSet, basename="expense")
router.register("refunds", api.RefundViewSet, basename="refund")

urlpatterns = router.urls
