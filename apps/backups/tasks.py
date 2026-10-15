from celery import shared_task


@shared_task
def automatic_backup() -> int:
    """Every few hours: back up the database, then delete the files of old backups."""
    from .models import Backup
    from .services import purge, run

    backup = run(Backup.objects.create(kind=Backup.Kind.AUTOMATIC))
    purge()
    return backup.pk


@shared_task
def run_backup(backup_id: int) -> None:
    from .models import Backup
    from .services import run

    backup = Backup.objects.filter(pk=backup_id, status=Backup.Status.RUNNING).first()
    if backup is not None:
        run(backup)
