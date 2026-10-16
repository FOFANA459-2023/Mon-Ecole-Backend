from celery import shared_task


@shared_task
def check_absenteeism() -> int:
    """Nightly: raise the high-absenteeism alerts of every school. Returns how many were raised."""
    from .alerts import check_all_schools

    return check_all_schools()
