from django.db import transaction
from django.http import FileResponse
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.academics.scoping import visible_class_ids
from apps.audit import services as audit
from apps.core.files import DOCUMENT_TYPES, MAX_DOCUMENT_BYTES, validate_upload
from apps.core.permissions import HasSchoolPermission
from apps.enrollments.models import Enrollment
from apps.people.models import StaffMember, Student

from .models import Document

# (read permission, write permission) per owner type
OWNER_PERMISSIONS = {
    Document.OwnerType.STUDENT: ("students.view", "students.update"),
    Document.OwnerType.STAFF: ("staff.view", "staff.update"),
    Document.OwnerType.SCHOOL: ("administration.view", "administration.manage"),
}


class DocumentSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = [
            "id",
            "owner_type",
            "owner_id",
            "category",
            "title",
            "content_type",
            "size",
            "uploaded_by_name",
            "created_at",
        ]

    def get_uploaded_by_name(self, obj) -> str:
        return obj.created_by.full_name if obj.created_by else ""


class DocumentUploadSerializer(serializers.Serializer):
    owner_type = serializers.ChoiceField(choices=Document.OwnerType.choices)
    owner_id = serializers.IntegerField(min_value=0)
    category = serializers.ChoiceField(choices=Document.Category.choices, default=Document.Category.OTHER)
    title = serializers.CharField(max_length=200, required=False, allow_blank=True)
    file = serializers.FileField()


class DocumentViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet
):
    """Documents are listed per owner: ?owner_type=student&owner_id=42."""

    serializer_class = DocumentSerializer
    permission_classes = [IsAuthenticated, HasSchoolPermission]
    required_permissions = {"*": []}  # checked per owner below
    parser_classes = [MultiPartParser, FormParser]
    pagination_class = None

    def _check_owner(self, owner_type: str, owner_id: int, *, write: bool) -> None:
        read_perm, write_perm = OWNER_PERMISSIONS[owner_type]
        if (write_perm if write else read_perm) not in self.request.permission_codes:
            raise PermissionDenied()
        school = self.request.school
        if owner_type == Document.OwnerType.STUDENT:
            if not Student.objects.filter(school=school, pk=owner_id).exists():
                raise NotFound()
            class_ids = visible_class_ids(self.request)
            if (
                class_ids is not None
                and not Enrollment.objects.filter(
                    student_id=owner_id, status="active", class_group_id__in=class_ids
                ).exists()
            ):
                raise NotFound()
        elif owner_type == Document.OwnerType.STAFF:
            if not StaffMember.objects.filter(school=school, pk=owner_id).exists():
                raise NotFound()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Document.objects.none()
        return Document.objects.filter(school=self.request.school).select_related("created_by")

    @extend_schema(
        parameters=[
            OpenApiParameter("owner_type", enum=[c for c, _ in Document.OwnerType.choices], required=True),
            OpenApiParameter("owner_id", int, required=True),
        ]
    )
    def list(self, request, *args, **kwargs):
        owner_type = request.query_params.get("owner_type")
        owner_id = request.query_params.get("owner_id", "")
        if owner_type not in OWNER_PERMISSIONS or not owner_id.isdigit():
            raise ValidationError({"owner_type": ["Give owner_type and owner_id."]})
        self._check_owner(owner_type, int(owner_id), write=False)
        documents = self.get_queryset().filter(owner_type=owner_type, owner_id=int(owner_id))
        return Response(self.get_serializer(documents, many=True).data)

    def get_object(self):
        document = super().get_object()
        self._check_owner(document.owner_type, document.owner_id, write=self.action == "destroy")
        return document

    @extend_schema(
        request={"multipart/form-data": DocumentUploadSerializer}, responses={201: DocumentSerializer}
    )
    def create(self, request, *args, **kwargs):
        serializer = DocumentUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        self._check_owner(data["owner_type"], data["owner_id"], write=True)
        upload = data["file"]
        validate_upload(upload, allowed_types=DOCUMENT_TYPES, max_bytes=MAX_DOCUMENT_BYTES)
        with transaction.atomic():
            document = Document.objects.create(
                school=request.school,
                owner_type=data["owner_type"],
                owner_id=data["owner_id"],
                category=data["category"],
                title=(data.get("title") or upload.name)[:200],
                file=upload,
                content_type=upload.content_type,
                size=upload.size,
                created_by=request.user,
            )
            audit.record(
                "create",
                request=request,
                instance=document,
                module="documents",
                summary=f"Document added: {document.title} ({document.owner_type} #{document.owner_id})",
            )
        return Response(self.get_serializer(document).data, status=status.HTTP_201_CREATED)

    def perform_destroy(self, instance):
        with transaction.atomic():
            audit.record(
                "delete",
                request=self.request,
                instance=instance,
                module="documents",
                summary=f"Document deleted: {instance.title} ({instance.owner_type} #{instance.owner_id})",
            )
            instance.file.delete(save=False)
            instance.delete()

    @extend_schema(responses={(200, "application/octet-stream"): bytes})
    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        document = self.get_object()
        return FileResponse(
            document.file.open("rb"),
            content_type=document.content_type or None,
            as_attachment=False,
            filename=document.title,
        )
