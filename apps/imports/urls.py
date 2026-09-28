from django.urls import path

from .api import ImportTemplateView, ImportView

urlpatterns = [
    path("imports/<str:kind>/", ImportView.as_view(), name="import"),
    path("imports/<str:kind>/template/", ImportTemplateView.as_view(), name="import-template"),
]
