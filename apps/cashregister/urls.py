from rest_framework.routers import SimpleRouter

from . import api

router = SimpleRouter()
router.register("cash-registers", api.CashRegisterViewSet, basename="cash-register")
router.register("cash-sessions", api.CashSessionViewSet, basename="cash-session")

urlpatterns = router.urls
