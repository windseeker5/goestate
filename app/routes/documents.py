import os

from flask import abort, redirect, render_template, request, send_from_directory, url_for

from app.auth import admin_required
from app.db import get_db
from app.document_storage import attach_documents, documents_uploads_dir
from app.ingestion import ingest_document


def _redirect_for_document(doc):
    """Redirect back to the owning asset/liability if this document is linked,
    else back to the generic Documents page. Always server-derived from the
    document row itself — never a client-supplied "next" param — so this
    can't be abused as an open redirect.
    """
    if doc["linked_entity_type"] == "asset" and doc["linked_entity_id"]:
        return url_for("detail_asset", item_id=doc["linked_entity_id"])
    if doc["linked_entity_type"] == "liability" and doc["linked_entity_id"]:
        return url_for("detail_liability", item_id=doc["linked_entity_id"])
    return url_for("list_documents")


def register(app):
    @app.route("/app/documents")
    def list_documents():
        db = get_db()
        rows = db.execute(
            """
            SELECT d.*, la.name AS linked_asset_name, ll.creditor AS linked_liability_name
            FROM documents d
            LEFT JOIN assets la ON d.linked_entity_type = 'asset' AND la.id = d.linked_entity_id
            LEFT JOIN liabilities ll ON d.linked_entity_type = 'liability' AND ll.id = d.linked_entity_id
            ORDER BY d.created_at DESC
            """
        ).fetchall()
        return render_template("blocks/documents_list.html", documents=rows)

    @app.route("/app/documents/upload", methods=["POST"])
    @admin_required
    def upload_document():
        db = get_db()
        # getlist supports the <input multiple> the dropzone uses — a user
        # can select/drop several PDFs in one go, each ingested in turn.
        files = [f for f in request.files.getlist("file") if f and f.filename]
        if files:
            attach_documents(db, None, None, files)
        return redirect(url_for("list_documents"))

    @app.route("/app/documents/<int:document_id>/view")
    def view_document(document_id):
        db = get_db()
        doc = db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        if doc is None or not doc["filepath"]:
            abort(404)
        uploads_dir = documents_uploads_dir()
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
        redirect_target = _redirect_for_document(doc)

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
            file_path = os.path.join(documents_uploads_dir(), doc["filepath"])
            if os.path.exists(file_path):
                os.remove(file_path)

        return redirect(redirect_target)

    @app.route("/app/documents/<int:document_id>/reindex", methods=["POST"])
    @admin_required
    def reindex_document(document_id):
        db = get_db()
        doc = db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        if doc is None:
            abort(404)
        redirect_target = _redirect_for_document(doc)

        file_path = os.path.join(documents_uploads_dir(), doc["filepath"])
        if not os.path.exists(file_path):
            db.execute(
                "UPDATE documents SET ingestion_status = 'error', ingestion_error = ? WHERE id = ?",
                ("Original file no longer exists on disk.", document_id),
            )
            db.commit()
        else:
            # Pass the existing linkage through — ingest_document() rebuilds
            # every doc_chunk_meta row for this document from scratch, so
            # omitting these would silently drop the asset/liability
            # attribution from a linked document on reindex.
            ingest_document(
                db,
                document_id,
                file_path,
                linked_entity_type=doc["linked_entity_type"],
                linked_entity_id=doc["linked_entity_id"],
            )

        return redirect(redirect_target)
