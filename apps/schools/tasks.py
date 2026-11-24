import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def erase_school_task(school_id: int) -> None:
    """Delete a school the platform owner chose to delete (status "deleting"). On failure it goes back to
    "suspended", so the owner sees it and can try again."""
    from apps.audit import services as audit

    from .models import School
    from .services import erase_school

    school = School.objects.filter(pk=school_id, status=School.Status.DELETING).first()
    if school is None:
        return
    name, code = school.name, school.code
    try:
        removed = erase_school(school)
    except Exception:
        logger.exception("Deleting school %s failed", code)
        School.objects.filter(pk=school_id).update(status=School.Status.SUSPENDED)
        return
    audit.record(
        "delete",
        module="platform",
        entity_type="school",
        entity_id=school_id,
        summary=f"School deleted with all its data: {name} ({code})",
        old={"rows": sum(removed.values())},
    )
