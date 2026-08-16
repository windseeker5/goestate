import os
import uuid

from flask import current_app, send_from_directory
from werkzeug.security import safe_join

PHOTO_MAX_BYTES = 15 * 1024 * 1024


class PhotoValidationError(ValueError):
    pass


def _photo_root():
    database_dir = os.path.dirname(os.path.abspath(current_app.config["DATABASE_PATH"]))
    path = os.path.join(database_dir, "uploads", "ledger-photos")
    os.makedirs(path, exist_ok=True)
    return path


def _detect_image(data):
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp", "image/webp"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in {b"heic", b"heix", b"hevc", b"hevx"}:
            return ".heic", "image/heic"
        if brand in {b"mif1", b"msf1"}:
            return ".heif", "image/heif"
    raise PhotoValidationError("Choose a JPEG, PNG, WebP, HEIC, or HEIF image.")


def save_photo(file, entity_type):
    data = file.read(PHOTO_MAX_BYTES + 1)
    if not data:
        raise PhotoValidationError("The selected photo is empty.")
    if len(data) > PHOTO_MAX_BYTES:
        raise PhotoValidationError("The selected photo must be 15 MB or smaller.")

    extension, mime_type = _detect_image(data)
    filename = f"{uuid.uuid4().hex}{extension}"
    entity_dir = os.path.join(_photo_root(), entity_type)
    os.makedirs(entity_dir, exist_ok=True)
    destination = os.path.join(entity_dir, filename)

    try:
        with open(destination, "xb") as photo_file:
            photo_file.write(data)
    except Exception:
        if os.path.exists(destination):
            os.remove(destination)
        raise

    return f"{entity_type}/{filename}", mime_type


def delete_photo(relative_path):
    if not relative_path:
        return
    file_path = safe_join(_photo_root(), relative_path)
    if file_path and os.path.isfile(file_path):
        try:
            os.remove(file_path)
        except OSError:
            current_app.logger.warning("Could not remove ledger photo %s", file_path, exc_info=True)


def send_photo(relative_path, mime_type):
    response = send_from_directory(
        _photo_root(),
        relative_path,
        mimetype=mime_type,
        conditional=True,
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
