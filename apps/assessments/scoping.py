"""Who sees and who changes a gradebook.

Users who see every class (directors) see every gradebook. A teacher sees the gradebooks of the subjects
they teach, and — as class teacher — those of every subject of the class they lead. Only the teacher of the
subject (or someone who sees every class) may change one, and only while it is in progress.
"""

from django.db.models import Q

from apps.academics.scoping import sees_all_classes, staff_profile


def visible_gradebooks(request, queryset):
    if sees_all_classes(request):
        return queryset
    staff = staff_profile(request)
    if staff is None:
        return queryset.none()
    return queryset.filter(
        Q(class_subject__teacher=staff) | Q(class_subject__class_group__class_teacher=staff)
    )


def is_subject_teacher(request, gradebook) -> bool:
    staff = staff_profile(request)
    return staff is not None and gradebook.class_subject.teacher_id == staff.pk


def can_edit(request, gradebook) -> bool:
    """May enter marks and change the rules (status aside)."""
    if "grades.enter" not in request.permission_codes:
        return False
    return sees_all_classes(request) or is_subject_teacher(request, gradebook)


def can_view_class(request, class_group) -> bool:
    """May see every subject's results of a class: all-class users and the class teacher."""
    if sees_all_classes(request):
        return True
    staff = staff_profile(request)
    return staff is not None and class_group.class_teacher_id == staff.pk
