from .base import *  # noqa: F403
from .base import env

DEBUG = False

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
REFRESH_COOKIE_SECURE = True
X_FRAME_OPTIONS = "DENY"

# Files live in a private S3-compatible bucket; URLs are short-lived presigned links.
#   AWS S3:            bucket and region only; the credentials come from the instance role.
#   Supabase Storage:  also AWS_S3_ENDPOINT_URL (https://<project>.storage.supabase.co/storage/v1/s3) and the
#                      bucket's S3 access keys (Project settings → Storage → S3 access keys).
STORAGES["default"] = {  # noqa: F405
    "BACKEND": "storages.backends.s3.S3Storage",
    "OPTIONS": {
        "bucket_name": env("AWS_STORAGE_BUCKET_NAME"),
        "region_name": env("AWS_S3_REGION_NAME", default="eu-west-3"),
        "endpoint_url": env("AWS_S3_ENDPOINT_URL", default=None),
        "access_key": env("AWS_S3_ACCESS_KEY_ID", default=None),
        "secret_key": env("AWS_S3_SECRET_ACCESS_KEY", default=None),
        "addressing_style": "path" if env("AWS_S3_ENDPOINT_URL", default="") else None,
        "signature_version": "s3v4",
        "default_acl": None,
        "querystring_auth": True,
        "querystring_expire": 300,
        "file_overwrite": False,
    },
}
