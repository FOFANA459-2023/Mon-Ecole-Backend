import os
import uuid

from django.utils.deconstruct import deconstructible
from django.utils.translation import gettext as _
from rest_framework.exceptions import ValidationError

IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
DOCUMENT_TYPES = IMAGE_TYPES | {"application/pdf"}
MAX_PHOTO_BYTES = 2 * 1024 * 1024
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024


def validate_upload(file, *, allowed_types: set[str], max_bytes: int) -> None:
    if file.size > max_bytes:
        raise ValidationError(
            {"file": [_("The file is too large (maximum %d MB).") % (max_bytes // 1024 // 1024)]}
        )
    if getattr(file, "content_type", None) not in allowed_types:
        raise ValidationError({"file": [_("This file type is not allowed.")]})


@deconstructible
class SchoolUploadPath:
    """upload_to: store files under schools/<school_id>/<folder>/ with random names (no guessable URLs)."""

    def __init__(self, folder: str):
        self.folder = folder

    def __call__(self, instance, filename):
        ext = os.path.splitext(filename)[1].lower()[:10]
        return f"schools/{instance.school_id}/{self.folder}/{uuid.uuid4().hex}{ext}"

    def __eq__(self, other):
        return isinstance(other, SchoolUploadPath) and other.folder == self.folder
