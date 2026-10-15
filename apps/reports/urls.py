from django.urls import path

from . import api

urlpatterns = [
    path("reports/finance/<str:key>/", api.FinanceReportView.as_view(), name="finance-report"),
    path("reports/academic/<str:key>/", api.AcademicReportView.as_view(), name="academic-report"),
    path("reports/school/<str:key>/", api.SchoolReportView.as_view(), name="school-report"),
]
