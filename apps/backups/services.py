"""Making a backup: `pg_dump` (custom format, compressed) of the database, or a copy of the SQLite file in
development, saved to the default storage (the private S3 bucket in production) under backups/.

Supabase's transaction pooler cannot serve pg_dump: set BACKUP_DATABASE_URL to the session pooler or the
direct connection (port 5432). Files older than BACKUP_KEEP_DAYS are deleted; the history rows stay.
"""

import gzip
import logging
import os
import shutil
import subprocess  # noqa: S404 — runs pg_dump with fixed arguments
import tempfile
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.core.files import File
from django.db import connection
from django.utils import timezone

from .models import Backup

logger = logging.getLogger(__name__)
FAILED_MESSAGE = "The backup could not be made; the error is in the server logs."


def _pg_dump(target: str) -> None:
    url = settings.BACKUP_DATABASE_URL
    environment = dict(os.environ)
    if url:
        parts = urlsplit(url)
        command = [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            f"--host={parts.hostname}",
            f"--port={parts.port or 5432}",
            f"--username={parts.username}",
            f"--dbname={parts.path.lstrip('/')}",
            f"--file={target}",
        ]
        environment["PGPASSWORD"] = parts.password or ""
    else:
        db = settings.DATABASES["default"]
        command = [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            f"--host={db.get('HOST') or 'localhost'}",
            f"--port={db.get('PORT') or 5432}",
            f"--username={db.get('USER')}",
            f"--dbname={db.get('NAME')}",
            f"--file={target}",
        ]
        environment["PGPASSWORD"] = db.get("PASSWORD") or ""
    environment.setdefault("PGSSLMODE", "prefer")
    result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=1800)  # noqa: S603
    if result.returncode != 0:
        raise RuntimeError(f"pg_dump failed ({result.returncode}): {result.stderr.strip()[:300]}")


def _sqlite_copy(target: str) -> None:
    """Development only: the SQLite database as a gzipped SQL script (read through Django's connection)."""
    connection.ensure_connection()
    with gzip.open(target, "wt", encoding="utf-8") as handle:
        for line in connection.connection.iterdump():
            handle.write(f"{line}\n")


def run(backup: Backup) -> Backup:
    engine = "sqlite" if connection.vendor == "sqlite" else "postgresql"
    suffix = ".sql.gz" if engine == "sqlite" else ".dump"
    stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    directory = tempfile.mkdtemp(prefix="mon-ecole-backup-")
    target = os.path.join(directory, f"mon-ecole-{stamp}{suffix}")
    try:
        if engine == "sqlite":
            _sqlite_copy(target)
        else:
            _pg_dump(target)
        with open(target, "rb") as handle:
            backup.file.save(os.path.basename(target), File(handle), save=False)
        backup.size = os.path.getsize(target)
        backup.status, backup.engine = Backup.Status.DONE, engine
    except Exception:
        logger.exception("Backup %s failed", backup.pk)
        backup.status, backup.engine, backup.error = Backup.Status.FAILED, engine, FAILED_MESSAGE
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    backup.finished_at = timezone.now()
    backup.save()
    return backup


def purge(now=None) -> int:
    """Delete the files of backups older than BACKUP_KEEP_DAYS (their history rows stay, marked expired)."""
    cutoff = (now or timezone.now()) - timedelta(days=settings.BACKUP_KEEP_DAYS)
    expired = 0
    for backup in Backup.objects.filter(started_at__lt=cutoff, status=Backup.Status.DONE):
        backup.file.delete(save=False)
        backup.status = Backup.Status.EXPIRED
        backup.save(update_fields=["file", "status", "updated_at"])
        expired += 1
    return expired


def last_success():
    return (
        Backup.objects.filter(status__in=[Backup.Status.DONE, Backup.Status.EXPIRED])
        .order_by("-started_at")
        .first()
    )
