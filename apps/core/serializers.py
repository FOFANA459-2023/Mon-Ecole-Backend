from rest_framework import serializers


class TenantPrimaryKeyRelatedField(serializers.PrimaryKeyRelatedField):
    """A foreign-key field that only accepts records of the current school.

    An id from another school is reported as "does not exist", exactly like an unknown id.
    """

    def get_queryset(self):
        queryset = super().get_queryset()
        request = self.context.get("request")
        school = getattr(request, "school", None)
        if school is None:
            return queryset.none()
        return queryset.filter(school=school)
