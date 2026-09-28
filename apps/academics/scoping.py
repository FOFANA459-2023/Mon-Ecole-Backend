from django.db.models import Q

VIEW_ALL = "classes.view_all"


def staff_profile(request):
    """The staff record of the signed-in user in the current school, if any."""
    from apps.people.models import StaffMember

    return (
        StaffMember.objects.filter(school=request.school, user=request.user, status=StaffMember.Status.ACTIVE)
        .only("id")
        .first()
    )


def sees_all_classes(request) -> bool:
    return VIEW_ALL in request.permission_codes


def visible_classes(request, queryset):
    """Classes the user may see: all of them, or only those they teach or lead."""
    if sees_all_classes(request):
        return queryset
    staff = staff_profile(request)
    if staff is None:
        return queryset.none()
    return queryset.filter(Q(class_teacher=staff) | Q(class_subjects__teacher=staff)).distinct()


def visible_class_ids(request) -> list[int] | None:
    """None = no restriction; otherwise the ids of the classes the user teaches or leads."""
    if sees_all_classes(request):
        return None
    from .models import ClassGroup

    return list(
        visible_classes(request, ClassGroup.objects.filter(school=request.school)).values_list(
            "id", flat=True
        )
    )
