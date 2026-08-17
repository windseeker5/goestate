import os

from flask import current_app
from werkzeug.utils import secure_filename

from app.ingestion import ingest_document

ALLOWED_EXTENSIONS = {"pdf"}


def is_allowed_document(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def documents_uploads_dir():
    # send_from_directory (used by view_document) validates/resolves its
    # directory argument in a way that silently 404s on a relative path
    # like "instance/uploads" even when the file genuinely exists — Flask's
    # own docs pattern always passes an absolute path, so we do the same here.
    path = os.path.abspath(
        os.path.join(os.path.dirname(current_app.config["DATABASE_PATH"]), "uploads")
    )
    os.makedirs(path, exist_ok=True)
    return path


def save_uploaded_document(file, uploads_dir):
    filename = secure_filename(file.filename)
    dest_path = os.path.join(uploads_dir, filename)

    # Avoid overwriting an existing file with the same name.
    base, ext = os.path.splitext(filename)
    counter = 1
    while os.path.exists(dest_path):
        filename = f"{base}_{counter}{ext}"
        dest_path = os.path.join(uploads_dir, filename)
        counter += 1

    file.save(dest_path)
    return filename


def _record_upload_error(db, filename, entity_type, entity_id, message):
    db.execute(
        "INSERT INTO documents (filename, filepath, linked_entity_type, linked_entity_id, "
        "ingestion_status, ingestion_error) VALUES (?, '', ?, ?, 'error', ?)",
        (filename, entity_type, entity_id, message),
    )
    db.commit()


def attach_documents(db, entity_type, entity_id, files):
    """Save and ingest each uploaded PDF, optionally linking it to an asset/liability.

    Pass entity_type=None, entity_id=None for standalone (unlinked) uploads.
    One bad file (wrong type, disk error) never blocks the others or the
    caller's own transaction — failures are recorded as error-status
    `documents` rows instead of raised, mirroring how unsupported extensions
    were already handled on the generic Documents page.
    """
    uploads_dir = documents_uploads_dir()

    for file in files:
        if not file or not file.filename:
            continue

        if not is_allowed_document(file.filename):
            _record_upload_error(db, file.filename, entity_type, entity_id, "Only PDF files are supported.")
            continue

        try:
            filename = save_uploaded_document(file, uploads_dir)
        except OSError as exc:
            _record_upload_error(db, file.filename, entity_type, entity_id, str(exc)[:2000])
            continue

        cur = db.execute(
            "INSERT INTO documents (filename, filepath, linked_entity_type, linked_entity_id, ingestion_status) "
            "VALUES (?, ?, ?, ?, 'pending')",
            (filename, filename, entity_type, entity_id),
        )
        db.commit()
        document_id = cur.lastrowid
        dest_path = os.path.join(uploads_dir, filename)

        # Run ingestion synchronously, same tradeoff as the generic Documents
        # page (see routes/documents.py) — acceptable for a single-user local
        # app, no background job queue needed.
        ingest_document(db, document_id, dest_path, linked_entity_type=entity_type, linked_entity_id=entity_id)
