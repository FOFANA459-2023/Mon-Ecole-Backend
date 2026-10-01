# Mon École — Full Build Plan (System Design, Tech Stack, Development Phases)

## Context

The client's Terms of Reference (`Mon_Ecole_School_Management_System_Terms_of_Reference.docx`) describe a School Management Information System (SMIS): enrolment, students, classes/subjects, teachers, finance, cash register, attendance, assessments, results & report cards, dashboards, RBAC, notifications, backups — for schools in Guinea / Liberia, with a future multi-school SaaS goal. The project folder is greenfield (only the ToR and the cost-estimate PDFs exist).

Decisions already made in this engagement (reflected in `Mon_Ecole_Lean_Launch_Cost_Estimate.pdf`):
- **Scale target:** 5 schools (~10,000 students) growing to 10 schools (~20,000 students); design ceiling ~15 schools / 30,000 students without re-architecture.
- **Stack:** React + TypeScript + Tailwind frontend hosted on **Cloudflare**; **Django + DRF** backend hosted on **AWS** (ECS Fargate ARM, ALB, ElastiCache Valkey, S3); **Supabase Pro** as the PostgreSQL database; EmailJS Business for email; Cloudflare Pro, PostHog, Sentry (free), UptimeRobot; Gemini + DeepSeek for AI features.
- **Team:** 1 developer using Claude Code (Max 20x), Cursor Pro, GitHub Copilot Pro+ (PR review/testing). No Figma — UI is designed directly in code with shadcn/ui.

Goal of this plan: a complete, buildable blueprint — architecture, data model, key business-logic designs, infrastructure, security, testing and a phased delivery schedule — so implementation can start module by module with client sign-off at each step.

**Change from the earlier draft:** the frontend is a **Vite + React SPA on Cloudflare Pages** instead of Next.js. Everything sits behind a login (no SEO/SSR need), a static SPA is free/cheap to host on Cloudflare's CDN, and it avoids running a Node server.

---

## 1. Scope

**In scope (ToR §31 deliverables):** auth & users, roles & permissions, school settings, academic structure, students, guardians, enrolment, classes, subjects, teachers/staff, finance (fees, invoices, payments, receipts, expenses), cash register, attendance, assessments & grades, results/ranking, report cards (PDF), dashboard, global search, reports (PDF/Excel/CSV), in-app + email notifications, administration (announcements, calendar, documents), audit logs, backups, AI assistant, documentation & training.

**Out of scope for launch (ToR §33 future):** parent/student mobile apps, parent portal (planned post-launch), online registration, online/mobile-money payments, SMS/WhatsApp, biometric attendance, QR cards, e-learning, payroll, library/canteen/bus, SaaS self-service onboarding & billing. The data model keeps these additive.

---

## 2. Tech stack (final)

| Layer | Choice |
|---|---|
| Frontend | React 19 + TypeScript, **Vite**, React Router, **TanStack Query** (server state), TanStack Table, **React Hook Form + Zod**, **Tailwind CSS + shadcn/ui** (Radix), Recharts (charts), react-i18next (French + English), Sentry + PostHog JS SDKs |
| Frontend hosting | **Cloudflare Pages** (`app.<domain>`), free preview deployments per branch/PR |
| Edge | **Cloudflare Pro**: DNS, CDN, SSL, WAF managed rules, rate limiting, DDoS |
| Backend | Python 3.12, **Django 5.x + Django REST Framework**, SimpleJWT, django-filter, drf-spectacular (OpenAPI), django-storages (S3), Argon2 password hashing |
| Background jobs | **Celery** worker + Celery beat, broker/cache on **ElastiCache for Valkey** |
| Backend hosting | **AWS** (eu-west-3 Paris or eu-west-1 Ireland — same region as Supabase): ECR, **ECS Fargate (ARM/Graviton)**, Application Load Balancer, S3, CloudWatch, SSM Parameter Store, public subnets + security groups (no NAT) |
| Database | **Supabase Pro** (PostgreSQL 15+), used as a plain Postgres: Django ORM + migrations own the schema; connection through Supavisor pooler; extensions `pg_trgm`, `unaccent` |
| PDF / exports | **WeasyPrint** (HTML/CSS → PDF: report cards, receipts, forms), openpyxl (Excel), csv |
| Email | **EmailJS Business** via its REST API (server-side private key) for essential emails |
| AI | Gemini API (assistant, comment drafts), DeepSeek API (bulk/translation) behind a backend AI gateway |
| Monitoring | Sentry (errors), PostHog (product analytics), CloudWatch (logs/metrics), UptimeRobot (uptime) |
| IaC | **Terraform** (AWS + Cloudflare providers); Supabase configured via dashboard + documented |
| CI/CD | **GitHub Actions**; GitHub Copilot PR review; Dependabot |
| Testing | pytest + pytest-django + factory_boy (backend), Vitest + Testing Library (frontend), **Playwright** (E2E), Locust (load) |
| Dev tooling | Claude Code, Cursor, GitHub Copilot; Docker Compose for local dev; ruff (lint/format), mypy (gradual), ESLint + Prettier, tsc |

Principles: **PostgreSQL is the source of truth; the backend owns all business rules and authorization; the frontend is the interface.** Modular monolith — no microservices.

---

## 3. System architecture

```
 Users (desktop / tablet / phone)
        │ HTTPS
        ▼
 ┌──────────────────── Cloudflare (Pro) ────────────────────┐
 │ DNS · SSL · WAF · rate limiting · DDoS · CDN             │
 │                                                          │
 │  app.<domain>  → Cloudflare Pages (React SPA, static)    │
 │  api.<domain>  → proxied to AWS ALB                      │
 └──────────────────────────────────────────────────────────┘
                              │
 ┌──────────────────────── AWS (eu-west-x) ─────────────────┐
 │  ALB ──► ECS Fargate service "api"   (Django + Gunicorn) │
 │          ECS Fargate service "worker" (Celery worker)    │
 │          ECS Fargate service "beat"   (Celery beat, 1)   │
 │          ElastiCache Valkey (Celery broker + cache)      │
 │          S3 (files, PDFs, DB dump backups — private)     │
 │          CloudWatch · SSM Parameter Store · ECR          │
 └──────────────────────────────────────────────────────────┘
                              │ TLS, same region
                     Supabase Pro (PostgreSQL)
                              │
 External: EmailJS · Gemini · DeepSeek · Sentry · PostHog · UptimeRobot
```

**Request flow:** SPA → `api.<domain>/api/v1/...` → Cloudflare WAF → ALB → Django: authentication (JWT) → tenant resolution (active school) → permission check → service layer (business validation, transaction, audit) → Postgres → JSON response. Heavy work (PDFs, exports, bulk emails, AI batches) is queued to Celery and polled via a `jobs` endpoint; files are downloaded via short-lived S3 presigned URLs.

**Environments:**
- **Local:** Docker Compose — postgres, valkey, django (runserver), celery worker/beat, vite dev server, mailpit (email capture).
- **Staging (on demand):** Cloudflare Pages preview + ECS staging services scaled 0↔1 + a free Supabase project + separate S3 bucket. Brought up for release testing and client demos.
- **Production:** as diagrammed. Deploys only from `main` via GitHub Actions with a manual approval gate.

---

## 4. Backend design

### 4.1 Repository layout (two repositories)

The API and the web app live in separate repositories, each with its own Docker image and CI/CD pipeline:
**Mon-Ecole-Backend** (this layout) and **Mon-Ecole** (the React SPA, §7). The frontend's TypeScript API types
are generated from this repository's `openapi.yaml`.

```
Mon-Ecole-Backend/
    config/            settings/{base,local,staging,production}.py, urls.py, celery.py, asgi/wsgi
    apps/
      core/            base models (TimeStamped, TenantScoped), tenancy middleware, permissions, pagination, exceptions, numbering sequences, jobs
      accounts/        User, Membership, Role, Permission, auth endpoints, password reset
      schools/         School, SchoolSettings, branding
      academics/       AcademicYear, Term, Level, ClassGroup, Subject, ClassSubject
      people/          Student, Guardian, StudentGuardian, StaffMember
      enrollments/     Enrollment, transfers, promotion
      finance/         FeeCategory, FeeStructure, Invoice, InvoiceLine, Payment, PaymentAllocation, Receipt, Refund, Expense
      cashregister/    CashRegister, CashSession, CashTransaction
      attendance/      AttendanceSession, AttendanceRecord, StaffAttendance
      assessments/     GradingScale, Gradebook, GradeCategory, Assessment, Grade, engine.py (calculation), results, ReportCard
      administration/  Department, Announcement, CalendarEvent
      documents/       Document (generic attachments), upload/download
      notifications/   Notification, preferences, email dispatch (EmailJS)
      reports/         report catalogue, PDF/Excel/CSV generators
      dashboard/       KPI selectors + cache
      search/          global search endpoint
      audit/           AuditLog, audit.record() helper, request context
      ai/              providers (gemini, deepseek), gateway, PII redaction, budgets, tools
    templates/         pdf/ (report_card, receipt, invoice, enrollment_form, reports), email/
    locale/            fr, en
    Dockerfile, docker-compose.yml, pyproject.toml, requirements*.txt, openapi.yaml
    infra/terraform/   modules: network, ecr, ecs, alb, valkey, s3, iam, cloudwatch, ssm, cloudflare; envs/staging, envs/prod
    .github/workflows/ ci.yml (lint, tests, schema, image check), cd.yml (image → staging → production), deploy.yml
    docs/              build plan, permission-matrix.md, data-dictionary.md, runbooks/ (deploy, restore, incident), user-manual/

Mon-Ecole/ (frontend, see §7)
    src/, public/, Dockerfile (nginx), docker-compose.yml
    .github/workflows/ ci.yml (lint, types, tests, build, image check), cd.yml (image + Cloudflare Pages), e2e.yml
```

### 4.2 Layering convention (every app)
- `models.py` — data + DB constraints only.
- `selectors.py` — read queries (always tenant-scoped, `select_related`/`prefetch_related`).
- `services.py` — all writes and business rules, wrapped in `transaction.atomic()`, call `audit.record()`.
- `serializers.py` / `api.py` (DRF ViewSets) — thin: validate input, call services/selectors, declare required permissions.
- `tests/` — unit (services/engine), API (permissions + tenant isolation).

### 4.3 Multi-tenancy (built in from day one)
- `School` is the tenant root. Every tenant-owned table inherits abstract `TenantScopedModel` (`school` FK, indexed) with a `TenantQuerySet.for_school(school)` manager.
- A `User` can belong to several schools through `Membership(user, school, roles, is_active)`; the SPA sends `X-School-ID`; `TenancyMiddleware`/DRF authentication validates the membership and sets `request.school`. Unknown/foreign school → 404 (never 403, to avoid leaking existence).
- Base ViewSet `TenantViewSet.get_queryset()` always filters by `request.school`; objects created via services get `school=request.school` server-side (never from client input).
- Composite indexes lead with `school_id` (e.g. `(school_id, student_number)` unique, `(school_id, academic_year_id, class_id)`).
- A dedicated **tenant-isolation test suite** hits every endpoint as a user of School B against School A's objects and asserts 404.
- Optional hardening (Phase 7): Postgres Row-Level Security keyed on `SET app.current_school`.

### 4.4 Authentication & sessions
- Login with email/username + password (Argon2 hashing, Django password validators, min 10 chars).
- **Access token** (JWT, 15 min) held in memory by the SPA; **refresh token** (7 days, rotating, blacklisted on logout) in an `HttpOnly; Secure; SameSite=Lax` cookie scoped to `api.<domain>`; CSRF protection on the refresh endpoint.
- **Automatic logout:** SPA idle timer (default 30 min, configurable per school) + refresh expiry. Session list / "log out everywhere" for admins.
- Login throttling (DRF throttle + lockout after N failures), password reset via email (EmailJS), forced password change on first login, `last_login` tracked.
- Optional TOTP 2FA for Super Admin / Director / Accountant (django-otp) in Phase 7.

### 4.5 RBAC
- `Permission` = code strings grouped by module: `students.view|create|update|archive|transfer|export`, `enrollments.*`, `classes.*`, `subjects.*`, `staff.*`, `finance.view|invoice.create|payment.record|payment.reverse|refund|expense.create|export`, `cash.open|close|record|view`, `attendance.view|record|edit`, `grades.view|enter|submit|review|publish|reopen`, `reportcards.generate`, `reports.view|export`, `settings.manage`, `users.manage`, `audit.view`, `ai.use`.
- `Role` (per school; system templates seeded): Super Admin, Director/Principal, Administrative Staff, Accountant, Teacher, Parent (reserved). Schools can clone/customise roles.
- DRF permission class `HasSchoolPermission` reads `required_permissions = {"list": [...], "create": [...]}` on each ViewSet.
- **Object-level scope for teachers:** teachers only see classes/subjects they are assigned to (`ClassSubject.teacher`), enforced in selectors.
- `/api/v1/me` returns user, memberships, active school and effective permission codes; the SPA hides menu items/buttons accordingly (UX only — the backend always re-checks).
- Deliverable in Phase 0: `docs/permission-matrix.md` (role × permission) signed off by the client.

### 4.6 Audit logging
`AuditLog(school, user, action, module, entity_type, entity_id, summary, old_values JSONB, new_values JSONB, ip, user_agent, created_at)`. Written by services for every create/update/delete/archive and every sensitive action (payment recorded/reversed, grade changed/published, login/logout/failed login, role change, export). Request context (user, IP, UA) captured by middleware into `contextvars`. Append-only (no update/delete API); viewer screen with filters for users with `audit.view`.

### 4.7 API conventions
- Base path `/api/v1/`; resources: `auth/`, `me/`, `schools/`, `settings/`, `academic-years/`, `terms/`, `levels/`, `classes/`, `subjects/`, `class-subjects/`, `staff/`, `students/`, `guardians/`, `enrollments/`, `fee-categories/`, `fee-structures/`, `invoices/`, `payments/`, `receipts/`, `expenses/`, `cash-sessions/`, `attendance-sessions/`, `assessments/`, `grades/`, `results/`, `report-cards/`, `reports/`, `notifications/`, `announcements/`, `calendar/`, `documents/`, `search/`, `dashboard/`, `audit-logs/`, `jobs/`, `ai/`.
- Page-number pagination (default 25, max 100), `django-filter` filters, `?search=`, `?ordering=`.
- Consistent error shape `{code, message, fields}`; money as decimal strings; dates ISO-8601; times in UTC stored, displayed in school timezone.
- OpenAPI schema generated by drf-spectacular → TypeScript types generated in the frontend (`openapi-typescript`) so frontend and backend never drift.
- Long jobs: `POST` returns `{job_id}` → `GET /jobs/{id}` → `{status, progress, result_url}`.

### 4.8 Files, PDFs, exports
- Uploads (photos ≤ 2 MB, documents ≤ 10 MB, whitelisted types) go through the API to a **private S3 bucket** under `schools/<school_id>/...`; downloads via presigned URLs (5 min).
- PDFs rendered by WeasyPrint from Django templates (school logo, name, colours from `SchoolSettings`; French/English). ARM Docker image includes Pango/Cairo fonts.
- Single receipt/enrolment form: generated synchronously (<2 s). Class/term report cards and large exports: Celery job → ZIP/merged PDF in S3.

### 4.9 Search
- Global search endpoint across students (name, number, phone), guardians (name, phone), staff, classes, receipts, invoices. Postgres `pg_trgm` GIN indexes + `unaccent` for accent-insensitive French names; results grouped by type, permission-filtered.

### 4.10 Internationalisation
- UI and PDFs in **French and English** (Guinea is francophone, Liberia anglophone); default language per school, override per user. Currency per school (GNF, LRD, USD) with locale formatting; grading scale per school.

---

## 5. Data model (key entities)

Common fields on every table: `id` (BigAutoField), `created_at`, `updated_at`, `created_by`; tenant tables add `school_id`. Soft delete via `status`/`archived_at` for people and academic records; financial records are **never deleted**.

**accounts / schools**
- `School(name, code, registration_number, address, phone, email, website, logo, country, timezone, currency, default_language, status)`
- `SchoolSettings(school, idle_timeout, receipt_prefix, invoice_prefix, student_number_format, report_card_template, sms/ai flags)`
- `User(email, username, first/last name, phone, language, is_active, last_login, must_change_password)`; `Membership(user, school, roles M2M, is_active)`; `Role(school|null, name, is_system, permissions M2M)`; `Permission(code, module, description)`

**academics**
- `AcademicYear(name "2026-2027", start, end, is_current, status open/closed)`; `Term(year, name, order, start, end, status)`
- `Level(name, order, cycle)` e.g. 7ème, 10ème, Terminale; `ClassGroup(year, level, name, room, class_teacher→Staff, capacity, status)`
- `Subject(code, name, level?, status)`; `ClassSubject(class, subject, teacher→Staff, coefficient, weekly_hours)` — coefficient lives here so it can differ by level/year

**people / enrollments**
- `Student(student_number unique per school, first/last name, gender, dob, place_of_birth, nationality, address, phone, photo, status active/archived, notes)`
- `Guardian(first/last name, relationship default, phone, email, address, occupation, user→User null for future parent portal)`; `StudentGuardian(student, guardian, relationship father/mother/guardian, is_primary, is_financial_contact)`
- `StaffMember(employee_id, names, gender, dob, phone, email, address, qualification, specialization, employment_date, department, status, user→User)`
- `Enrollment(student, academic_year, class, enrollment_date, previous_school, type new/re-enrolment/transfer-in, status active/transferred/withdrawn/completed, left_on, reason)` — unique active enrolment per student per year

**finance**
- `FeeCategory(name: registration, tuition, exam, transport, canteen, uniform, other; is_recurring)`
- `FeeStructure(year, level|class, category, amount, installments JSON [{due_date, amount}])`
- `Invoice(number, student, enrollment, year, issue_date, due_date, status draft/issued/cancelled, total)`; `InvoiceLine(invoice, category, description, amount, discount)`
- `Payment(number, student, date, amount, method cash/bank/mobile_money/card/other, reference, received_by, status posted/reversed, reversal_of→Payment)`
- `PaymentAllocation(payment, invoice_line, amount)`; `Receipt(number, payment, pdf, issued_at)`; `Refund(payment, amount, reason, approved_by)`
- `Expense(date, category, amount, method, payee, reference, description, attachment, recorded_by)`
- Derived (never stored as editable): invoice paid/balance/status (Paid/Partially paid/Unpaid/Overdue), student balance.

**cash register**
- `CashRegister(name)`; `CashSession(register, date, opened_by, opening_balance, closed_by, counted_closing, expected_closing, difference, status open/closed)`
- `CashTransaction(session, type IN/OUT, category, amount, method, source payment|expense|manual, reference, description, created_by, reversal_of)` — append-only

**attendance**
- `AttendanceSession(class, date, period/subject optional, taken_by, submitted_at, locked)`; `AttendanceRecord(session, enrollment, status present/absent/late/excused/unexcused, minutes_late, note)`; `StaffAttendance(staff, date, status, note)`

**assessments / results** — grading rules belong to the teacher (decided 2026-10-01: schools in Guinea and Liberia have no common rules; each teacher chooses how many quizzes, tests, homework and exams to give and what to call them)
- `GradingScale(school, level null=school default, max_mark e.g. 20/10/100, pass_mark, decimals, rank_method competition|dense)` — how marks are *reported*; the only school-level rule
- `Gradebook(class_subject, term, missing_policy exclude|zero, status open/submitted/published, submitted/published by+at, status_note)` — one per subject, class and term, created when a term's list is first read
- `GradeCategory(gradebook, name — free text, weight, method average|total, order)` — the teacher's own groups ("Interrogations" ×1, "Composition" ×2, "Quizzes" 20 %…)
- `Assessment(gradebook, category, name — free text, date, max_score — any number, weight within its category)` — as many as the teacher wants
- `Grade(assessment, enrollment, score null=not marked, excused flag, comment, updated_by)`
- Subject marks, class results and ranks are computed on read from the rules (never stored); published gradebooks are locked, so results stay stable. `SubjectResult`/`TermResult` snapshots arrive with report cards.
- `ReportCard(enrollment, term, version, pdf, generated_at, published)`

**administration / documents / notifications / audit / ai**
- `Department`, `Announcement(title, body, audience roles/classes, publish_at)`, `CalendarEvent(title, type exam/holiday/meeting, start, end)`
- `Document(owner_type student|staff|school, owner_id, category, file, uploaded_by)`
- `Notification(user, type, title, body, link, read_at)`; `NotificationPreference(user, type, in_app, email)`
- `AuditLog` (see §4.6); `Job(type, status, progress, result_file, error, created_by)`
- `AIUsage(school, user, provider, feature, tokens_in, tokens_out, cost)`; `AIBudget(school, monthly_limit)`

Numbering sequences (`core.Sequence(school, key, year, last_value)`, incremented with `select_for_update`): student `MEC-2026-00001`, invoice `INV-2026-000001`, receipt `REC-2026-000001`, payment, employee IDs — formats configurable per school.

---

## 6. Key business-logic designs

**Enrolment flow:** create/find student → attach guardians → choose year/level/class (capacity check) → upload documents → `enrollments.services.enroll()` creates `Enrollment`, auto-generates the invoice from the class/level `FeeStructure`, optional initial payment → receipt PDF → print enrolment form. Re-enrolment/promotion: end-of-year wizard proposes next level per student (based on `TermResult.decision`), admin confirms in bulk. Transfer: close current enrolment (`transferred`, date, reason) and open a new one; historical grades stay attached to the old enrolment.

**Finance (ledger-style):** payments are recorded against a student and **allocated** to open invoice lines (oldest due first by default, manually adjustable). Balances and statuses are always computed from invoices − allocations. Posted payments cannot be edited or deleted; corrections are **reversals** (the payment is marked reversed with a reason, requires `finance.payment.reverse`) → audit logged. Overdue = due date passed and balance > 0 (nightly Celery job updates alerts). Cash payments automatically create a `CashTransaction` IN in the open cash session; recording cash without an open session is blocked.

**Cash register:** open session with opening balance (defaults to previous closing) → IN/OUT transactions (payments, expenses, manual) → close with counted cash; system computes `expected = opening + Σin − Σout` and records the difference. Daily/weekly/monthly cash reports from transactions.

**Grades workflow:** teacher sets up their categories (or starts from a template / copies another gradebook) → adds assessments → enters scores (bulk grid, keyboard-friendly, "E" = excused, explicit save) → **submit** → head/director **sends back** with a reason or **publishes** (counts in class results). Published grades are locked; edits require `grades.reopen` with a reason. Every batch of marks and every step is in the audit log (old → new values).

**Calculation engine (`assessments/engine.py`, pure functions, fully unit-tested):**
1. Each assessment counts as score / max. Excused → left out. Missing → left out, or 0 when the teacher chose "count as zero" (only once the assessment has marks for someone).
2. Category = weighted average of its assessments' fractions ("average") or points earned / points possible ("total").
3. Subject = Σ(category × weight) / Σ(weight) over categories with something counted, × the scale's `max_mark`, rounded half-up.
4. Overall average = Σ(subject mark × `ClassSubject.coefficient`) / Σ coefficients over subjects with a mark (ToR §15).
5. Class average, min, max per subject and overall; rank by mark (competition or dense per `GradingScale`, ties share rank).
6. Decision pass/fail vs `pass_mark`. Still to come with report cards: mention bands, annual average from term averages.
All arithmetic in `Decimal`.

**Attendance:** teacher opens "Today" for an assigned class on a phone → everyone defaults to Present → tap to mark Absent/Late (minutes)/Excused → submit. Editable same day; later edits need `attendance.edit` and are audited. Aggregates (daily, monthly, per student, per class, absence and late reports) from records; high-absenteeism alert rule (e.g. > N absences in 30 days) evaluated nightly.

**Dashboard:** KPIs (students, active classes, teachers, expected/collected revenue, outstanding, students with arrears, present/absent/late today), charts (enrolment by class, revenue by month, outstanding, attendance, performance, gender, students per level) and alerts (overdue payments, upcoming deadlines, high absenteeism, upcoming exams, missing grades). Computed by selectors with filters (year, term, level, class, payment status) and cached in Valkey for 5 minutes per school+filter; invalidated on relevant writes.

**Notifications:** events (payment recorded, overdue, absence, grades published, announcement) create in-app notifications; only essential messages also go by email through EmailJS (password reset, receipts, invitations), queued in Celery with a monthly quota counter and alert at 80 % of the EmailJS limit.

**Backups:** Supabase daily backups (7 days) + Celery beat `pg_dump` every 4 hours to a versioned, encrypted S3 bucket (30-day lifecycle) + monthly restore drill into staging (runbook in `docs/runbooks/restore.md`). **Supabase PITR ($100/month) is switched on before the finance module goes live.** In-app "Backups" screen lists backup history and status for admins.

---

## 7. Frontend design

```
Mon-Ecole/            (frontend repository)
  src/
    app/          router.tsx (lazy routes), providers (QueryClient, Auth, School, i18n, Theme), layout/ (Sidebar, Topbar, MobileNav)
    components/ui shadcn components; components/ shared (DataTable, PageHeader, FilterBar, StatCard, ConfirmDialog, FileUpload, MoneyInput, EmptyState)
    features/
      auth/ dashboard/ enrollments/ students/ administration/ classes/ subjects/ staff/
      finance/ cash-register/ attendance/ assessments/ results/ reports/ settings/ users/ audit/ ai/
        each: api.ts (TanStack Query hooks), schemas.ts (Zod), pages/, components/
    lib/          api-client (fetch wrapper, auth refresh, X-School-ID, error mapping), api-types.ts (generated from OpenAPI), permissions.ts (<Can permission="...">), format.ts (money/date per locale), analytics.ts (PostHog), sentry.ts
    i18n/         fr.json, en.json
  public/_headers  (CSP, HSTS, X-Frame-Options for Cloudflare Pages)
```
- **Navigation** mirrors ToR §4: Dashboard, Enrolments, Students, Administration, Classes, Subjects, Teachers, Finance, Cash Register, Attendance, Assessments, Results & Report Cards, Settings (+ Users, Audit). Persistent left sidebar on desktop; bottom/hamburger navigation on mobile.
- **Server state** only via TanStack Query (no global store needed beyond auth/school context).
- **Forms:** React Hook Form + Zod schemas mirroring API validation; multi-step enrolment wizard.
- **Tables:** TanStack Table with server-side pagination/sort/filter; card layout on small screens.
- **Mobile-first screens:** attendance taking and grade entry are designed for phones/tablets first (large tap targets, sticky save).
- **Permission gating:** menu items, routes and buttons wrapped in `<Can>`; forbidden routes redirect.
- **Accessibility:** Radix primitives, keyboard navigation, contrast-checked Tailwind theme; light/dark themes.
- **Performance:** route-level code splitting, cached queries, optimistic updates for grade/attendance entry.
- Design reference: the client's "Mon École dashboard design" (ToR §24) translated into a Tailwind theme (colours, typography) in Phase 1.

---

## 8. Infrastructure & DevOps

**AWS (Terraform `infra/terraform`):**
- VPC with 2 public subnets (2 AZs); security groups: ALB accepts 443 only from Cloudflare IP ranges; tasks accept traffic only from the ALB; Valkey only from tasks.
- ECR repo; ECS cluster with services `api` (min 2 tasks, autoscale on CPU 60 % up to 6), `worker` (1–3, scale on queue depth), `beat` (exactly 1); ARM64 task definitions.
- ALB with HTTPS (ACM certificate) → `api` target group, health check `/healthz`.
- ElastiCache for Valkey (smallest node, encryption in transit).
- S3 buckets: `media` (private, versioned), `backups` (private, versioned, lifecycle 30 days), `exports` (lifecycle 7 days); SSE encryption, block public access.
- SSM Parameter Store (SecureString) for `DATABASE_URL`, `DJANGO_SECRET_KEY`, EmailJS keys, AI keys, Sentry DSN; injected into task definitions.
- CloudWatch log groups (14-day retention), alarms: 5xx rate, CPU, unhealthy targets, queue backlog → email.
- IAM: task role limited to its S3 buckets and SSM paths; CI deploy role via GitHub OIDC (no long-lived keys).

**Cloudflare (Terraform):** DNS for `app` (Pages) and `api` (proxied CNAME to ALB), SSL Full (strict), WAF managed rules, rate-limit rules on `/api/v1/auth/*`, bot fight mode, cache rules (never cache `/api/*`), Pages project connected to GitHub.

**Supabase:** Pro project in the same region; strong DB password; SSL enforced; network restrictions to AWS egress where possible; connection via Supavisor pooler (transaction mode, `CONN_MAX_AGE=0`, server-side cursors off); Supabase Auth/Storage/Realtime not used; `pg_trgm`, `unaccent` enabled; compute Medium at 5 schools → Large at 10; PITR enabled before finance go-live.

**CI/CD (GitHub Actions):**
- On PR: backend — ruff, mypy (gradual), pytest with Postgres + Valkey services, `makemigrations --check`, OpenAPI schema diff; frontend — ESLint, `tsc --noEmit`, Vitest, build; Copilot review; Cloudflare Pages preview URL posted to the PR.
- On merge to `main`: build ARM image (buildx) → push to ECR → deploy **staging** (run migrations as one-off ECS task, then update services) → Playwright smoke tests.
- Production: manual approval → migrations task → rolling ECS deploy (min healthy 100 %) → Pages production deploy → smoke test; automatic rollback to previous task definition on failed health checks.
- Migrations must be backward-compatible (expand/contract) so rolling deploys never break.

---

## 9. Security & data protection

- HTTPS everywhere (Cloudflare + ACM), HSTS, CSP and security headers on Pages and Django.
- Tenant isolation + RBAC enforced server-side (§4.3, §4.5) and covered by automated tests.
- Argon2 passwords, lockout/throttling, rotating refresh tokens, idle auto-logout, optional TOTP for high-privilege roles.
- Input validation on every endpoint; Django ORM (no raw SQL with user input); file-type/size checks on upload; private S3 + presigned URLs.
- Secrets only in SSM; no secrets in repo (pre-commit secret scan + GitHub secret scanning); Dependabot for dependency updates.
- Audit log on all sensitive actions; financial records immutable (reversals only).
- Student data (minors): minimal data to AI providers, PII redaction layer, never send names/IDs/contacts to DeepSeek, paid Gemini tier only; per-school AI feature flag; data export/deletion procedures documented.
- Backups encrypted; restore tested monthly; disaster recovery runbook (RPO ≤ 4 h before PITR, minutes after; RTO ≤ 4 h).
- Phase 7 security review: OWASP ASVS checklist, dependency audit, authenticated ZAP scan against staging, permission-matrix test pass.

---

## 10. Performance, scaling & observability

**Targets (NFRs):** API p95 < 400 ms for list/detail; dashboard < 1 s (cached); global search < 300 ms; single receipt PDF < 2 s; 1,000 report cards < 15 min; 300 concurrent users per 10 schools without errors; uptime ≥ 99.5 %.

**How:** `school_id`-leading composite indexes; `select_related`/`prefetch_related` everywhere (django-debug-toolbar + nplusone in dev, query-count assertions in tests); pagination on every list; Valkey caching of KPIs; Celery for heavy jobs; ECS autoscaling; Supabase compute upgrade path (Medium → Large → XL) and read replica for reporting when needed (> 10 schools); CDN for all static assets.

**Observability:** Sentry (backend + frontend, release tracking), PostHog (page views, feature usage, funnels — no student PII in events), CloudWatch logs/alarms, structured JSON logging with request IDs, UptimeRobot on `app` and `api/healthz`.

---

## 11. AI features design (`apps/ai`)

- **Gateway** with provider adapters (`GeminiProvider`, `DeepSeekProvider`), retries/timeouts, per-school monthly budget (`AIBudget`) + usage metering (`AIUsage`), feature flag per school, `ai.use` permission.
- **PII redaction** before any call: replace names/IDs with placeholders, re-insert locally after the response.
- **Admin assistant (Gemini):** natural-language questions answered through **function/tool calling over a fixed set of safe, tenant-scoped query functions** (e.g. `attendance_summary`, `revenue_by_month`, `class_performance`, `outstanding_by_class`) — the model never writes SQL or sees other schools' data.
- **Report-card comment drafts (Gemini; DeepSeek for bulk):** suggestions based on anonymised averages/trends; teacher must review/edit before saving; never auto-published.
- **Translation helper (DeepSeek):** French ↔ English for announcements/comments.
- Prompts versioned in code; outputs logged (without PII) for quality review.

---

## 12. Testing strategy

- **Unit:** calculation engine (averages, coefficients, ranking ties, missing grades, rounding), balance/status derivation, allocation logic, cash session totals, numbering sequences, PII redaction.
- **API/integration:** every endpoint — permissions per role, tenant isolation (cross-school 404), validation errors, audit entries created; flows Student→Enrollment→Invoice→Payment→Receipt, Assessment→Grade→Publish→Results→ReportCard.
- **Frontend:** Vitest + Testing Library for forms, permission gating, formatting.
- **E2E (Playwright, staging):** Admin sets up year/levels/classes/subjects → creates teacher → enrols student (invoice generated) → accountant records payment (receipt) and closes cash session → teacher takes attendance on mobile viewport → teacher enters grades → director publishes → report card PDF generated.
- **Load (Locust):** simulated 10 schools, 300 concurrent users mixing dashboard, lists, grade entry and payment recording; report-card bulk job timing.
- **Operational:** backup restore drill, deploy rollback drill.
- Coverage goal: ≥ 90 % on engine/finance services, ≥ 75 % backend overall.

---

## 13. Development phases (1 developer + AI tools)

Each phase ends with a staging demo to the client, feedback fixes, and sign-off before the next phase.

| Phase | Duration | Scope & deliverables | Exit criteria |
|---|---|---|---|
| **0. Discovery & sign-off** | 1–2 wks | Answers to open questions (§14); sample report cards, receipts, fee schedules, existing Excel lists; permission matrix; ERD & data dictionary; screen list + low-fidelity layouts built as shadcn page shells; confirm languages/currency | Client signs functional spec, permission matrix, grading & finance rules |
| **1. Foundation** | 2–3 wks | Monorepo, Docker Compose, Django project (core, accounts, schools, audit), tenancy, RBAC, JWT auth, password reset, `/me`; React shell (layout, sidebar, login, i18n fr/en, theme, `<Can>`, API client + generated types); Terraform for AWS + Cloudflare; Supabase project; CI/CD to staging; Sentry, PostHog, UptimeRobot wired | Login → empty dashboard on staging; tenant-isolation and permission tests green; deploy pipeline works end to end |
| **2. Core school management** | 3–4 wks | School settings; academic years/terms/levels/classes; subjects & class-subjects (coefficients, teachers); staff/teachers; students, guardians, documents; enrolment wizard, re-enrolment, transfer, archive; student profile (tabs); student card & enrolment form PDF; **Excel/CSV import** for students, guardians, staff; global search; basic dashboard counts; user management UI | A school can be fully set up and all students imported/enrolled; E2E journey #1 green |
| **3. Finance & cash register** | 3–4 wks | Fee categories & structures with installments; invoice generation on enrolment; payments + allocation + reversals; receipts PDF; outstanding balances & statuses; expenses; cash register sessions; finance reports (payments, outstanding, cash daily/weekly/monthly, financial summary); enable **Supabase PITR** | Accountant can run a full day (payments, expenses, closing) with correct totals; finance unit tests ≥ 90 %; client's sample receipt matched |
| **4. Academics** | 4 wks | Attendance (mobile-first) + staff attendance + reports; assessment types/assessments; grade entry grid; submit/review/publish/reopen workflow; calculation engine; results & ranking; report card PDF (client template), bulk generation job; class/grade/ranking reports | Engine reproduces client's sample report card exactly; bulk generation meets NFR; E2E journey #2 green |
| **5. Reports, notifications, dashboard** | 2–3 wks | Report catalogue (ToR §20) with PDF/Excel/CSV export; full dashboard KPIs, charts, filters, alerts; in-app notifications + essential emails (EmailJS); announcements; school calendar; administration module (departments, official documents); backup history screen | All ToR §20 reports available; dashboard matches agreed KPI definitions |
| **6. AI features** | 2 wks | AI gateway, budgets, PII redaction; admin assistant (tool calling); report-card comment drafts; translation helper; per-school toggle | AI answers verified against real figures; no PII leaves the system (tests) |
| **7. Hardening, UAT & go-live** | 2–3 wks | Security review, optional 2FA/RLS, load test & tuning, restore & rollback drills; UAT with pilot school; data migration of real school data; user manual (fr/en), admin training, technical docs & runbooks; production cut-over | UAT sign-off; ToR §32 success criteria met; production live for pilot school |
| **8. Rollout & next versions** | ongoing | Onboard schools 2→5→10; monitor costs/performance; then parent portal, SMS/WhatsApp, online payments, SaaS onboarding per client priority | — |

**Total to first go-live:** ~19–25 weeks (≈ 5–6 months) for one developer, assuming Phase 0 answers arrive on time. Finance (3) and Academics (4) can swap order if the client needs grades before fees.

---

## 14. Open questions gating phases (send to client in Phase 0)

- **Academics (gates Phase 4):** grade scale (0–20?), assessment weights vs coefficients, how missing grades/absences count, ranking tie rule, who reviews/publishes, can published grades be reopened, term structure (trimesters/semesters), annual average and promotion rules, sample report card.
- **Finance (gates Phase 3):** real fee schedules by level, installment plans and due dates, discounts/scholarships/sibling rates, payment methods used, who may reverse/refund, currency, sample receipt/invoice, existing accounting software.
- **General:** languages (French/English default per school), roles actually used and any extra roles, internet reliability at schools (decides whether offline attendance is added later), existing data to import (formats), document types/sizes/retention, branding assets (logo, colours, dashboard design file).

---

## Verification

- **Per phase:** `docker compose up` locally; `pytest` (incl. tenant-isolation + permission suites) and `vitest` green in CI; Playwright E2E journeys green against staging; client demo on staging and written sign-off.
- **Calculation & finance correctness:** unit tests reproduce the client's sample report card and receipts to the cent/decimal.
- **Non-functional:** Locust load test meets §10 targets at a 10-school simulation; report-card bulk timing measured; restore drill from S3 dump and from Supabase PITR into staging succeeds; rollback drill succeeds.
- **Security:** OWASP checklist, ZAP scan on staging with no high findings, secrets scan clean, permission matrix verified by automated tests.
- **Go-live check:** ToR §32 success criteria walked through with the client on production for the pilot school.
