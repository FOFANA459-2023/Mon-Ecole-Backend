from django.urls import path

from . import api

urlpatterns = [path("reports/finance/<str:key>/", api.FinanceReportView.as_view(), name="finance-report")]
