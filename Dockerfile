# Production image for the Django API, Celery worker and Celery beat (same image, different command).
# Built for linux/arm64 (AWS Graviton) in CI; also runs on amd64 for local development.
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# The API, worker, beat and one-off migration tasks all run production settings unless told otherwise
# (docker-compose.yml switches to config.settings.local for development).
ENV DJANGO_SETTINGS_MODULE=config.settings.production

# Pango/HarfBuzz/fonts are needed by WeasyPrint for report-card and receipt PDFs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 \
        fonts-dejavu-core fonts-liberation \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=app:app . .
RUN DJANGO_SETTINGS_MODULE=config.settings.base DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput

USER app
EXPOSE 8000

# Migrations run as a separate one-off task during deployment, never on container start.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60", "--access-logfile", "-"]
