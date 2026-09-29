# Mon École — Backend (API)

One school. One platform. One source of truth.

Mon École manages enrolment, students, classes, teachers, finance, cash register, attendance, assessments and report cards for schools in Guinea, Liberia and beyond — designed from day one to serve many schools (multi-tenant SaaS).

This repository is the **Django REST API** (plus Celery worker and beat). The web app lives in **[Mon-Ecole](https://github.com/FOFANA459-2023/Mon-Ecole)** and talks to this API.

- **Build plan & architecture:** [docs/build-plan.md](docs/build-plan.md)
- **Role × permission matrix (for client sign-off):** [docs/permission-matrix.md](docs/permission-matrix.md)

## Status

| Phase | Scope | State |
|---|---|---|
| 0 | Discovery & sign-off (grading rules, fees, sample report cards) | Waiting on client answers |
| 1 | Foundation: auth, multi-school tenancy, roles & permissions, audit log, school settings, users | Built |
| 2 | Core school management: students, guardians, enrolment, classes, subjects, staff, documents, import, search | Built |
| 3–7 | Finance, academics, reports & notifications, AI, hardening & go-live | Planned |

Still to do before the first deployment: Terraform for AWS + Cloudflare (the deploy pipeline is ready and waits for it), Sentry/PostHog keys.

## Stack

Python 3.13, Django 5.2 LTS, Django REST Framework, SimpleJWT, Celery, ReportLab (PDFs), openpyxl (Excel) — deployed as one Docker image to **AWS ECS Fargate (ARM)**, with PostgreSQL on **Supabase Pro**, Valkey (Redis-compatible) on ElastiCache, files in a private S3 bucket and email through EmailJS.

## Repository layout

```
config/             settings (base / local / test / production), urls, celery
apps/core/          base models, tenancy, permission checks, errors, numbering, request context, PDFs, exports
apps/schools/       School (the tenant) + settings
apps/accounts/      users, roles, memberships, auth API, permission registry
apps/audit/         append-only audit log
apps/academics/     academic years, terms, levels, classes, subjects, class subjects
apps/people/        students, guardians, staff
apps/enrollments/   enrolment, class changes, withdrawals, promotion, enrolment form PDF
apps/documents/     files attached to students, staff or the school
apps/imports/       Excel/CSV import of students and staff
apps/search/        global search
apps/dashboard/     dashboard figures
openapi.yaml        generated API schema (the web app generates its TypeScript types from it)
docs/               build plan, permission matrix
Dockerfile          production image (API, worker and beat use the same image)
docker-compose.yml  local stack: PostgreSQL, Valkey, Mailpit, API, worker, beat
.github/            CI, CD and deploy workflows, Dependabot
```

## Run it locally

### Option A — without Docker (fastest)

SQLite, tasks run inline, emails printed in the console:

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # macOS/Linux: .venv/bin/python
.venv/Scripts/python manage.py migrate
.venv/Scripts/python manage.py seed_demo
.venv/Scripts/python manage.py runserver
```

### Option B — Docker Compose (PostgreSQL + Valkey + Celery, like production)

```bash
docker compose up --build
docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py seed_demo
```

API http://localhost:8000/api/v1/ · API docs http://localhost:8000/api/docs/ · Emails http://localhost:8025

Then start the web app from the [Mon-Ecole](https://github.com/FOFANA459-2023/Mon-Ecole) repository; it expects this API on port 8000.

Demo accounts (all `@monecole.test`): `admin` (Super Administrator in both demo schools), `directeur`, `secretariat`, `comptable`, `enseignant`, `teacher`. The demo password is set in `apps/schools/management/commands/seed_demo.py` (override with `DEMO_PASSWORD`).

## Branches and pull requests

`main` is protected: nothing reaches it without a pull request whose checks all pass. Work on `develop` (or a
feature branch from it), push, and open a pull request into `main`. Merging to `main` deploys to staging; a
`v*` tag deploys to production.

```bash
git switch develop && git pull
# …commit…
git push
gh pr create --base main --fill
```

Install the pre-commit hooks once so most problems are caught before you push:

```bash
.venv/Scripts/pre-commit install
```

## Tests and quality gates

| Suite | What it covers | Run locally |
|---|---|---|
| Unit + integration | services, API views, permissions, school isolation, imports, PDFs, commands (pytest, PostgreSQL in CI, random order) | `.venv/Scripts/python -m pytest` |
| Security regression | every API route: requires sign-in, refuses members without permission, hides other schools (404); JWT forgery/expiry; cookie flags, CORS, security headers; production settings | `.venv/Scripts/python -m pytest apps/core/tests/test_security.py` |
| Coverage | fails under 85 % (branch coverage) | `.venv/Scripts/python -m pytest --cov` |
| Lint, format, types | ruff, mypy with the Django and DRF plugins | `.venv/Scripts/ruff check . && .venv/Scripts/ruff format --check . && .venv/Scripts/mypy .` |
| Schema and migrations | migrations committed and applied on PostgreSQL; `openapi.yaml` matches the code | `.venv/Scripts/python manage.py makemigrations --check --dry-run` |
| SAST | semgrep (Python, Django, secrets, Dockerfile rules) | CI |
| Dependencies | pip-audit against known vulnerabilities; Dependabot weekly | `.venv/Scripts/pip-audit -r requirements.txt` |
| Secrets | gitleaks over the whole git history (reviewed false positives in `.gitleaksignore`) | pre-commit |
| Docker | hadolint, Django deployment checks inside the image, non-root user, trivy image + configuration scans | CI |
| DAST | OWASP ZAP API scan of the production image, signed in, driven by `openapi.yaml` (injection, XSS, SSRF… fail the build; report uploaded) | CI |
| End to end | the web app against this API: sign-in, permissions, enrolment, search, accessibility (in the Mon-Ecole repository) | Mon-Ecole CI |

After changing an API serializer, regenerate the schema (CI fails if it drifts), commit it, then regenerate the web app's types (`npm run gen:api` in Mon-Ecole):

```bash
DATABASE_URL= .venv/Scripts/python manage.py spectacular --lang en --file openapi.yaml --validate
```

## How the code is organised (conventions)

- **Every school-owned record has a `school`**; the API picks the school from the `X-School-ID` header and checks the user's membership. Records of another school answer **404**, never 403. See `apps/core/tenancy.py`.
- **Permissions are codes** (`students.view`, `finance.payment.record`, …) grouped into roles per school. Views declare `required_permissions = {"list": [...], "create": [...]}`; anything undeclared is denied. See `apps/accounts/permissions_registry.py` and `apps/core/permissions.py`.
- **Writes go through `services.py`** (transaction + audit entry); `api.py` views stay thin.
- **Audit log** is append-only: `apps.audit.services.record(...)` with before/after values.
- **Errors** always come back as `{code, message, fields}`.
- **Auth:** 15-minute access token kept in memory by the SPA; 7-day rotating refresh token in an `HttpOnly` cookie; automatic sign-out after the school's idle timeout.

## CI/CD

| Workflow | When | What |
|---|---|---|
| `ci.yml` | every pull request | the quality and security gates above (lint, types, tests, SAST, dependency and secret scans, Docker image checks, DAST) — all required before merging to `main` |
| `cd.yml` | push to `main`, tags `v*` | runs CI, then builds the image for `linux/arm64` + `linux/amd64`, publishes it to `ghcr.io/fofana459-2023/mon-ecole-backend` (tags: `sha-…`, `main`, `latest`, version) with an SBOM and build provenance, and signs it (Sigstore/cosign); then deploys `main` to **staging** and `v*` tags to **production** |
| `deploy.yml` | called by CD, or by hand from the Actions tab | copies the image to ECR, runs `migrate` as a one-off ECS task, rolls the api/worker/beat services and waits until they are stable. Run it by hand with an older image tag to roll back |

### Turning on deployment (once the AWS infrastructure exists)

1. **Settings → Environments**: create `staging` and `production` (add required reviewers to `production` for a manual approval gate).
2. In each environment, add the variables listed at the top of [.github/workflows/deploy.yml](.github/workflows/deploy.yml) (`AWS_REGION`, `AWS_DEPLOY_ROLE_ARN`, `ECR_REPOSITORY`, `ECS_CLUSTER`, `ECS_SERVICES`, `ECS_SUBNETS`, `ECS_SECURITY_GROUPS`).
3. **Settings → Secrets and variables → Actions → Variables** (repository level): set `DEPLOY_STAGING=true` and, when ready, `DEPLOY_PRODUCTION=true`. Until then CD stops after publishing the image.
4. Release to production by pushing a tag: `git tag v1.0.0 && git push origin v1.0.0`.

The ECS task definitions must set `DJANGO_SETTINGS_MODULE=config.settings.production` (the image default), `DJANGO_SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`, `AWS_STORAGE_BUCKET_NAME` and the other production variables in [.env.example](.env.example). Give the `beat` service a deployment of minimum 0 % / maximum 100 % so two schedulers never run at once.

## Useful commands

```bash
python manage.py create_school --name "École X" --code ecolex --admin-email director@ecolex.org   # real school + invitation
python manage.py permission_matrix --output docs/permission-matrix.md                           # refresh the matrix doc
```
