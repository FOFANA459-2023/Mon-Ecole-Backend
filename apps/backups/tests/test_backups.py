"""Database backups: made, failing safely, purged, started by the owner, seen by directors."""

import gzip
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.backups import services
from apps.backups.models import Backup

pytestmark = pytest.mark.django_db
URL = "/api/v1/platform/backups/"


def test_a_backup_of_the_test_database(school):
    """SQLite (local runs): a gzipped SQL script. PostgreSQL (CI): a real pg_dump in custom format."""
    backup = services.run(Backup.objects.create())
    assert backup.status == "done", backup.error
    assert backup.engine == ("sqlite" if connection.vendor == "sqlite" else "postgresql")
    assert backup.size > 0 and backup.file.name.startswith("backups/mon-ecole-")
    with backup.file.open("rb") as handle:
        content = handle.read()
    if backup.engine == "sqlite":
        assert gzip.decompress(content).startswith(b"BEGIN TRANSACTION;")
    else:
        assert content.startswith(b"PGDMP")


def test_pg_dump_gets_the_connection_without_the_password_on_the_command_line(
    monkeypatch, settings, tmp_path
):
    calls = {}

    def fake_run(command, env, **kwargs):
        calls["command"], calls["password"] = command, env["PGPASSWORD"]
        open(next(c for c in command if c.startswith("--file=")).split("=", 1)[1], "wb").write(b"PGDMP")

        class Result:
            returncode = 0
            stderr = ""

        return Result()

    monkeypatch.setattr("apps.backups.services.subprocess.run", fake_run)
    monkeypatch.setattr("apps.backups.services.connection.vendor", "postgresql")
    settings.BACKUP_DATABASE_URL = "postgres://backup:s3cret@db.example.supabase.co:5432/postgres"
    backup = services.run(Backup.objects.create())
    assert backup.status == "done" and backup.engine == "postgresql" and backup.file.name.endswith(".dump")
    assert "--host=db.example.supabase.co" in calls["command"] and "--format=custom" in calls["command"]
    assert calls["password"] == "s3cret" and not any("s3cret" in part for part in calls["command"])


def test_a_failing_dump_is_recorded_without_its_details(monkeypatch, settings):
    def fake_run(command, env, **kwargs):
        class Result:
            returncode = 1
            stderr = "connection refused for password s3cret"

        return Result()

    monkeypatch.setattr("apps.backups.services.subprocess.run", fake_run)
    monkeypatch.setattr("apps.backups.services.connection.vendor", "postgresql")
    settings.BACKUP_DATABASE_URL = "postgres://u:s3cret@h:5432/d"
    backup = services.run(Backup.objects.create())
    assert backup.status == "failed" and backup.error == services.FAILED_MESSAGE and not backup.file


def test_old_files_are_deleted_and_the_history_kept(school):
    backup = services.run(Backup.objects.create())
    assert services.purge() == 0
    assert services.purge(now=timezone.now() + timedelta(days=31)) == 1
    backup.refresh_from_db()
    assert backup.status == "expired" and not backup.file


class TestEndpoints:
    def test_the_owner_backs_up_now_and_downloads(
        self, owner, client_for, django_capture_on_commit_callbacks
    ):
        client = client_for(owner)
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(URL)
        assert response.status_code == 202 and response.data["kind"] == "manual"
        rows = client.get(URL).data["results"]
        assert rows[0]["status"] == "done" and rows[0]["created_by_name"] == "Platform Owner"
        download = client.get(f"{URL}{rows[0]['id']}/download/")
        assert download.status_code == 200
        assert AuditLog.objects.filter(module="backups", action="export").exists()

    def test_one_backup_at_a_time(self, owner, client_for):
        Backup.objects.create(status=Backup.Status.RUNNING)
        assert client_for(owner).post(URL).status_code == 400

    def test_nobody_else_reaches_them(self, school, make_member, client_for):
        director = client_for(make_member(school, "director"), school)
        assert director.get(URL).status_code == 403
        assert director.post(URL).status_code == 403

    def test_directors_see_when_the_last_backup_succeeded(self, school, make_member, client_for):
        services.run(Backup.objects.create())
        director = client_for(make_member(school, "director"), school)
        data = director.get("/api/v1/backup-status/").data
        assert data["last_status"] == "done" and data["last_success_at"] is not None
        assert data["keep_days"] == 30 and data["every_hours"] == 4
        teacher = client_for(make_member(school, "teacher"), school)
        assert teacher.get("/api/v1/backup-status/").status_code == 403
