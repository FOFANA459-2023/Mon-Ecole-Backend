from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import HasSchoolPermission

from .importers import IMPORTERS
from .parsing import ImportFileError

REQUIRED = {"students": ["students.create"], "staff": ["staff.create"]}


class ImportUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    commit = serializers.BooleanField(default=False, help_text="false = check only; true = save if all valid")


def _importer(request, kind):
    if kind not in IMPORTERS:
        raise NotFound()
    if not all(code in request.permission_codes for code in REQUIRED[kind]):
        raise PermissionDenied()
    return IMPORTERS[kind](request.school, request)


class ImportView(APIView):
    """Check (commit=false) or import (commit=true) an Excel/CSV list of students or staff."""

    permission_classes = [IsAuthenticated, HasSchoolPermission]
    required_permissions = {"post": []}
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(request={"multipart/form-data": ImportUploadSerializer}, responses={200: dict, 400: dict})
    def post(self, request, kind):
        importer = _importer(request, kind)
        serializer = ImportUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        commit = serializer.validated_data["commit"]
        try:
            result = importer.run(serializer.validated_data["file"], commit=commit)
        except ImportFileError as exc:
            raise ValidationError({"file": [str(exc)]}) from exc
        failed = commit and bool(result.errors or result.columns_missing)
        return Response(
            result.as_dict(), status=status.HTTP_400_BAD_REQUEST if failed else status.HTTP_200_OK
        )


class ImportTemplateView(APIView):
    """An Excel template with the expected columns, an example row and instructions."""

    permission_classes = [IsAuthenticated, HasSchoolPermission]
    required_permissions = {"get": []}

    @extend_schema(responses={(200, "application/octet-stream"): bytes})
    def get(self, request, kind):
        importer = _importer(request, kind)
        response = HttpResponse(
            importer.template(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="modele-{kind}.xlsx"'
        return response
