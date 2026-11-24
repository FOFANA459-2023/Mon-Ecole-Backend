# Production on a VPS (Hetzner Cloud) + Supabase

One small server runs the API with Docker Compose (Caddy for HTTPS, gunicorn, Celery worker and beat, Valkey).
The database and the uploaded files live on Supabase: Postgres for the data, Supabase Storage (S3-compatible)
for photos, documents, report cards and backups. Replaces the AWS setup in `infra/aws` (kept for reference).

**Cost:** Hetzner CAX11 (2 Arm vCPU, 4 GB) about €4/month; Supabase Free.

## First setup

1. **Server** — Hetzner Cloud → *Add server*: Nuremberg or Falkenstein, Ubuntu 24.04, *Arm64 → CAX11*
   (or *x86 → CX22*), public IPv4, the SSH key `~/.ssh/mon-ecole-deploy.pub`, a firewall allowing TCP 22/80/443.
   Then prepare it:
   ```
   ssh -i ~/.ssh/mon-ecole-deploy root@<ip> 'bash -s' < deploy/vps/setup.sh
   ```

2. **Supabase Storage** — Supabase → *Storage* → *New bucket* `mon-ecole-media`, **private**. Then
   *Project settings → Storage → S3 access keys* → *New access key*; note the endpoint, region, key id and secret.

3. **App settings** — write `app.env` (outside the repository; never commit it):
   ```
   DJANGO_SECRET_KEY=<python -c "import secrets; print(secrets.token_urlsafe(50))">
   DATABASE_URL=postgres://postgres.<ref>:<password>@aws-1-eu-west-3.pooler.supabase.com:6543/postgres?sslmode=require
   BACKUP_DATABASE_URL=postgres://postgres.<ref>:<password>@aws-1-eu-west-3.pooler.supabase.com:5432/postgres?sslmode=require
   AWS_STORAGE_BUCKET_NAME=mon-ecole-media
   AWS_S3_REGION_NAME=eu-west-3
   AWS_S3_ENDPOINT_URL=https://<ref>.storage.supabase.co/storage/v1/s3
   AWS_S3_ACCESS_KEY_ID=<key id>
   AWS_S3_SECRET_ACCESS_KEY=<secret>
   FRONTEND_URL=https://mon-ecole.<account>.workers.dev
   CORS_ALLOWED_ORIGINS=https://mon-ecole.<account>.workers.dev
   EMAILJS_MONTHLY_LIMIT=200
   SENTRY_ENVIRONMENT=production
   # Optional: EMAILJS_SERVICE_ID, EMAILJS_TEMPLATE_ID, EMAILJS_PUBLIC_KEY, EMAILJS_PRIVATE_KEY, SENTRY_DSN
   ```
   `BACKUP_DATABASE_URL` must use the session pooler (5432): pg_dump cannot work through the 6543 pooler.

4. **GitHub** (Mon-Ecole-Backend → *Settings → Environments → production*):
   - variables: `VPS_HOST` = the server IP, `API_HOST` = `api.<ip with dashes>.sslip.io` (e.g.
     `api.203-0-113-7.sslip.io`); delete `EC2_INSTANCE_ID`;
   - secrets: `VPS_SSH_KEY` = the content of `~/.ssh/mon-ecole-deploy`, `VPS_KNOWN_HOSTS` = the output of
     `ssh-keyscan <ip>`, `APP_ENV` = the content of `app.env`.

5. **Deploy** — *Actions → Deploy → Run workflow*, environment `production`, image
   `ghcr.io/fofana459-2023/mon-ecole-backend:main`. From then on every push to `main` deploys.

6. **Frontend** — Cloudflare Worker variable `VITE_API_URL` = `https://<API_HOST>/api/v1`, then redeploy it.

## Day to day

| Task | How |
|---|---|
| Connect | `ssh -i ~/.ssh/mon-ecole-deploy root@<ip>`, then `cd /opt/mon-ecole` |
| Logs | `docker compose logs -f api` (also `worker`, `beat`, `caddy`) |
| Django shell | `docker compose exec api python manage.py shell` |
| Change a setting | edit `app.env`, paste it into the `APP_ENV` secret, redeploy |
| Roll back | *Actions → Deploy → Run workflow* with an older image tag (`sha-1a2b3c4`) |
| Own domain | DNS `A` record `api.example.org` → the server IP; set `API_HOST`; redeploy; update `VITE_API_URL`, `FRONTEND_URL`, `CORS_ALLOWED_ORIGINS` |
