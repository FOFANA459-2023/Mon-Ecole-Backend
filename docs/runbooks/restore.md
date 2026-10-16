# Backups and restoring the database

Mon École backs up the whole database (every school) on its own, and the platform owner can back up on
demand. This page says where the backups are, how to check them, and how to restore one.

## What is backed up, when, where

- **What:** the whole PostgreSQL database, with `pg_dump --format=custom` (compressed, restorable table by
  table). Uploaded files (photos, documents, report cards) are not in it: they live in the S3 bucket, which
  is not touched by a restore.
- **When:** every 4 hours (Celery beat task `database-backup`, at :15), and whenever the platform owner
  presses **Back up now** (Platform → Backups).
- **Where:** the private media bucket (`mon-ecole-production-media-…`), under `backups/`, encrypted at rest
  (SSE-S3). Never served by a public URL; only the platform owner can download one from the app, and every
  download is in the audit log.
- **How long:** 30 days (`BACKUP_KEEP_DAYS`). Older files are deleted after each automatic backup; their
  history stays in the app, marked *expired*.
- Supabase also keeps its own daily backups (on the paid plan; and point-in-time recovery once PITR is on).

### Settings (SSM parameter `/mon-ecole/<environment>/app-env`)

| Variable | Default | Notes |
|---|---|---|
| `BACKUP_DATABASE_URL` | `DATABASE_URL` | **Must be a connection pg_dump can use.** Supabase's transaction pooler (port 6543) cannot: use the session pooler (same host, port **5432**) or the direct connection, with `?sslmode=require`. |
| `BACKUP_EVERY_HOURS` | `4` | |
| `BACKUP_KEEP_DAYS` | `30` | |

## Checking that backups work

- Platform → Backups lists every backup with its status and size. A *failed* backup's reason is in the
  worker's logs (`docker compose logs worker` through Session Manager); the app only says it failed.
- School settings show directors when the last backup succeeded.
- **Every month:** restore the latest backup into a scratch database (below) and open a few screens. A
  backup that was never restored is not a backup.

## Restoring

Restoring replaces data for **every school**. Decide first whether you need the whole database back or only
some rows (then restore to a scratch database and copy what is needed).

1. **Get the file.** Platform → Backups → download, or with the AWS CLI:
   `aws s3 cp s3://<media bucket>/backups/<file>.dump .`
2. **Check it:** `pg_restore --list <file>.dump | head` lists the tables.
3. **Restore into a scratch database first** (a local PostgreSQL 16+ or a new Supabase project):

   ```bash
   createdb monecole_restore
   pg_restore --no-owner --no-privileges --dbname=monecole_restore <file>.dump
   ```

   Point a local backend at it (`DATABASE_URL=postgres://…/monecole_restore`) and check the figures.
4. **To replace production** (outage: stop the app first so nothing writes during the restore):

   ```bash
   # On the server (Session Manager), stop the API, worker and beat:
   cd /opt/mon-ecole && docker compose stop api worker beat
   # From a machine with pg_restore 16+, into Supabase (session pooler or direct, port 5432):
   pg_restore --clean --if-exists --no-owner --no-privileges \
     --dbname="postgres://postgres.<ref>:<password>@<host>:5432/postgres?sslmode=require" <file>.dump
   # If the backup is older than the running code, bring its tables up to date, then start the app:
   docker compose run --rm --no-deps api python manage.py migrate --noinput
   docker compose up -d
   ```

5. **Afterwards:** sign in as the platform owner and a director, check the latest payments, registers and
   marks, and note in the audit trail (or the incident log) what was restored and why.

## Disaster recovery

| Lost | Recover with | Data lost at most |
|---|---|---|
| The EC2 server | `terraform apply` in `infra/aws`, then a deploy (the server holds no data) | nothing |
| The database | the latest backup here (or Supabase's own backup / PITR) | up to 4 hours (minutes with PITR) |
| The media bucket | nothing today: S3 versioning is not enabled — turn it on, or copy the bucket off-site | the uploads and the backups |
| The AWS account | the last downloaded backup + the uploads, if copied elsewhere | keep an off-site copy monthly |

Target: the service back within 4 hours (RTO), at most 4 hours of data lost (RPO) before PITR.
