from django.http import FileResponse
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated

from .models import Job
from .permissions import HasSchoolPermission


class JobSerializer(serializers.ModelSerializer):
    has_result = serializers.SerializerMethodField()

    class Meta:
        model = Job
        fields = [
            "id",
            "kind",
            "status",
            "total",
            "done",
            "outcome",
            "error",
            "result_name",
            "has_result",
            "created_at",
            "started_at",
            "finished_at",
        ]

    def get_has_result(self, obj) -> bool:
        return bool(obj.result)


class JobViewSet(mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """A background job started by the signed-in user: its progress, then its result file."""

    serializer_class = JobSerializer
    permission_classes = [IsAuthenticated, HasSchoolPermission]
    required_permissions: dict[str, list[str]] = {"*": []}

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Job.objects.none()
        return Job.objects.filter(school=self.request.school, created_by=self.request.user)

    @extend_schema(responses={(200, "application/octet-stream"): bytes})
    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        job = self.get_object()
        if not job.result:
            raise NotFound()
        return FileResponse(job.result.open("rb"), as_attachment=True, filename=job.result_name)
