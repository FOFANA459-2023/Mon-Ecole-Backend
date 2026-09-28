from django.urls import path

from .api import DashboardSummaryView

urlpatterns = [path("dashboard/summary/", DashboardSummaryView.as_view(), name="dashboard-summary")]
