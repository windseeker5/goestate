import os

from flask import abort, current_app, redirect, render_template, request, send_from_directory, url_for
from werkzeug.utils import secure_filename

from app.auth import admin_required
from app.db import get_db
from app.ingestion import ingest_document

ALLOWED_EXTENSIONS = {"pdf"}


def _allowed(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _uploads_dir():
    # send_from_directory (used by view_document below) validates/resolves
    # its directory argument in a way that silently 404s on a relative path
    # like "instance/uploads" even when the file genuinely exists — Flask's
    # own docs pattern always passes an absolute path (e.g. app.root_path
    # joined with a folder name), so we do the same here via abspath.
    path = os.path.abspath(
        os.path.join(os.path.dirname(current_app.config["DATABASE_PATH"]), "uploads")
    )
    os.makedirs(path, exist_ok=True)
    return path


def register(app):
    @app.route("/app/documents")
    def list_documents():
        db = get_db()
        rows = db.execute(
            "SELECT * FROM documents ORDER BY created_at DESC"
        ).fetchall()
        return render_template("blocks/documents_list.html", documents=rows)

    @app.route("/app/documents/upload", methods=["POST"])
    @admin_required
    def upload_document():
        db = get_db()
        # getlist supports the <input multiple> the dropzone uses — a user
        # can select/drop several PDFs in one go, each ingested in turn.
        files = [f for f in request.files.getlist("file") if f and f.filename]

        if not files:
            return redirect(url_for("list_documents"))

        uploads_dir = _uploads_dir()

        for file in files:
            if not _allowed(file.filename):
                db.execute(
                    "INSERT INTO documents (filename, filepath, ingestion_status, ingestion_error) "
                    "VALUES (?, '', 'error', ?)",
                    (file.filename, "Only PDF files are supported."),
                )
                db.commit()
                continue

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

            cur = db.execute(
                "INSERT INTO documents (filename, filepath, ingestion_status) VALUES (?, ?, 'pending')",
                (filename, filename),
            )
            db.commit()
            document_id = cur.lastrowid

            # Run ingestion synchronously, one document at a time. For a
            # single-user local app, this is acceptable (a few seconds per
            # PDF on CPU) and keeps the pipeline simple — no background job
            # queue needed. The upload form shows a full-page loading
            # overlay while this request is in flight so multi-document
            # uploads don't look frozen (see documents_list.html).
            ingest_document(db, document_id, dest_path)

        return redirect(url_for("list_documents"))

    @app.route("/app/documents/<int:document_id>/view")
    def view_document(document_id):
        db = get_db()
        doc = db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        if doc is None or not doc["filepath"]:
            abort(404)
        uploads_dir = _uploads_dir()
        file_path = os.path.join(uploads_dir, doc["filepath"])
        if not os.path.exists(file_path):
            abort(404)
        # as_attachment=False (the default) so the browser renders the PDF
        # inline in a new tab instead of forcing a download.
        return send_from_directory(uploads_dir, doc["filepath"], mimetype="application/pdf")

    @app.route("/app/documents/<int:document_id>/delete", methods=["POST"])
    @admin_required
    def delete_document(document_id):
        db = get_db()
        doc = db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        if doc is None:
            abort(404)

        chunk_ids = [
            row[0]
            for row in db.execute(
                "SELECT chunk_id FROM doc_chunk_meta WHERE document_id = ?", (document_id,)
            ).fetchall()
        ]
        for cid in chunk_ids:
            db.execute("DELETE FROM doc_chunks WHERE rowid = ?", (cid,))
        db.execute("DELETE FROM doc_chunk_meta WHERE document_id = ?", (document_id,))
        db.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        db.commit()

        if doc["filepath"]:
            file_path = os.path.join(_uploads_dir(), doc["filepath"])
            if os.path.exists(file_path):
                os.remove(file_path)

        return redirect(url_for("list_documents"))

    @app.route("/app/documents/<int:document_id>/reindex", methods=["POST"])
    @admin_required
    def reindex_document(document_id):
        db = get_db()
        doc = db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        if doc is None:
            abort(404)

        file_path = os.path.join(_uploads_dir(), doc["filepath"])
        if not os.path.exists(file_path):
            db.execute(
                "UPDATE documents SET ingestion_status = 'error', ingestion_error = ? WHERE id = ?",
                ("Original file no longer exists on disk.", document_id),
            )
            db.commit()
        else:
            ingest_document(db, document_id, file_path)

        return redirect(url_for("list_documents"))
