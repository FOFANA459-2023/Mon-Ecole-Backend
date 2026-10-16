"""Background jobs: started from a request, run by a Celery worker, polled by the screen.

A job kind maps to the function that does the work (`RUNNERS`). The function receives the `Job`, reports its
progress with `progress()` and ends with `finish()`; anything it raises marks the job as failed.
"""

import logging
from datetime import timedelta

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from django.utils.module_loading import import_string

from .models import Job

logger = logging.getLogger(__name__)

RUNNERS = {
    "report_cards": "apps.assessments.archive.run_report_cards_job",
}
# Result files are kept this long; the job row itself stays as a record.
KEEP_RESULTS = timedelta(days=7)
FAILED_MESSAGE = "The job could not be completed. Try again; if it fails again, contact support."


def start(kind: str, *, request, params: dict, total: int = 0) -> Job:
    """Create the job and queue it once the current transaction is committed."""
    if kind not in RUNNERS:
        raise ValueError(f"Unknown job kind: {kind}")
    job = Job.objects.create(
        school=request.school, created_by=request.user, kind=kind, params=params, total=total
    )
    from .tasks import run_job

    transaction.on_commit(lambda: run_job.delay(job.pk))
    return job


def run(job_id: int) -> None:
    job = Job.objects.select_related("school", "created_by").filter(pk=job_id).first()
    if job is None or job.status != Job.Status.QUEUED:
        return
    job.status, job.started_at = Job.Status.RUNNING, timezone.now()
    job.save(update_fields=["status", "started_at", "updated_at"])
    try:
        import_string(RUNNERS[job.kind])(job)
    except Exception:
        logger.exception("Job %s (%s) failed", job.pk, job.kind)
        job.status, job.error, job.finished_at = Job.Status.FAILED, FAILED_MESSAGE, timezone.now()
        job.save(update_fields=["status", "error", "finished_at", "updated_at"])


def progress(job: Job, done: int, total: int | None = None) -> None:
    job.done = done
    fields = ["done", "updated_at"]
    if total is not None:
        job.total = total
        fields.append("total")
    job.save(update_fields=fields)


def finish(job: Job, *, content: bytes | None, filename: str = "", outcome: dict | None = None) -> None:
    if content is not None:
        job.result.save(filename, ContentFile(content), save=False)
        job.result_name = filename
    job.status, job.outcome, job.finished_at = Job.Status.DONE, outcome or {}, timezone.now()
    job.done = max(job.done, job.total)
    job.save()


def purge_old_results(now=None) -> int:
    """Delete the result files of jobs finished more than KEEP_RESULTS ago."""
    cutoff = (now or timezone.now()) - KEEP_RESULTS
    count = 0
    for job in Job.objects.filter(finished_at__lt=cutoff).exclude(result=""):
        job.result.delete(save=False)
        job.save(update_fields=["result", "updated_at"])
        count += 1
    return count
