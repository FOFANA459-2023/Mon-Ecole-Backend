from django.db import transaction
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from apps.audit import services as audit

from .permissions import HasSchoolPermission


class TenantModelViewSet(viewsets.ModelViewSet):
    """CRUD for school-owned records: scoped to the current school, audited on every write.

    Subclasses set `queryset`, `serializer_class`, `required_permissions` and `audit_module`.
    """

    permission_classes = [IsAuthenticated, HasSchoolPermission]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    audit_module = ""

    def get_queryset(self):
        queryset = super().get_queryset()
        if getattr(self, "swagger_fake_view", False):
            return queryset.none()
        return queryset.filter(school=self.request.school)

    def describe(self, instance) -> str:
        return str(instance)

    def perform_create(self, serializer):
        instance = serializer.save(school=self.request.school, created_by=self.request.user)
        audit.record(
            "create",
            request=self.request,
            instance=instance,
            module=self.audit_module,
            summary=f"Created {instance._meta.verbose_name}: {self.describe(instance)}",
            new=audit.snapshot(instance),
        )

    def perform_update(self, serializer):
        old = audit.snapshot(serializer.instance)
        instance = serializer.save()
        old_changed, new_changed = audit.diff(old, audit.snapshot(instance))
        if new_changed:
            audit.record(
                "update",
                request=self.request,
                instance=instance,
                module=self.audit_module,
                summary=f"Updated {instance._meta.verbose_name}: {self.describe(instance)}",
                old=old_changed,
                new=new_changed,
            )

    def perform_destroy(self, instance):
        # One transaction: if the delete is refused (record in use), the audit entry is rolled back too.
        with transaction.atomic():
            audit.record(
                "delete",
                request=self.request,
                instance=instance,
                module=self.audit_module,
                summary=f"Deleted {instance._meta.verbose_name}: {self.describe(instance)}",
                old=audit.snapshot(instance),
            )
            instance.delete()
