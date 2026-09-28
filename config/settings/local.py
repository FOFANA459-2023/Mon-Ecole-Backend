from .base import *  # noqa: F403

DEBUG = True
REFRESH_COOKIE_SECURE = False
API_DOCS_ENABLED = True
STORAGES["staticfiles"] = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}  # noqa: F405
