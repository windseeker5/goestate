"""Admin-only database backup and CSV data export.

Full backups include users.password_hash (hashed, not plaintext) and
settings.value for llm_api_key (stored in plaintext). Acceptable here since
this route is @admin_required and the app is single-tenant/locally-hosted —
same trust boundary as /app/users. No redaction: an admin restoring a backup
needs the real values intact.
"""

import csv
import io
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime

import sqlite_vec
from flask import (
    Response, after_this_request, current_app, g, redirect, request,
    send_file, url_for,
)

from app import money as estate_money
from app.auth import admin_required
from app.db import get_db

REQUIRED_TABLES = {
    "users", "assets", "liabilities", "events", "tasks", "documents",
    "settings", "doc_chunks", "doc_chunk_meta",
}
SQLITE_MAGIC = b"SQLite format 3\x00"
BACKUP_FORMAT = 1
RESTORE_CONFIRM_WORD = "RESTORE"


def _csv_response(rows, fieldnames, filename):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row[k] for k in fieldnames})
    response = Response(buffer.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _timestamp():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _instance_dir():
    return os.path.dirname(os.path.abspath(current_app.config["DATABASE_PATH"]))


def _uploads_dir():
    return os.path.join(_instance_dir(), "uploads")


def _snapshot_db(src, dest_path):
    dest = sqlite3.connect(dest_path)
    try:
        src.backup(dest)
    finally:
        dest.close()


def _validate_backup_db(path):
    """Raise ValueError unless `path` is a sound Estate Copilot database."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except sqlite3.Error as exc:
        raise ValueError(f"Could not open the database in this backup ({exc}).")
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("The database in this backup failed its integrity check.")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = REQUIRED_TABLES - tables
        if missing:
            raise ValueError(
                "This does not look like an Estate Copilot backup "
                f"(missing tables: {', '.join(sorted(missing))})."
            )
        if conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0] == 0:
            raise ValueError(
                "This backup has no admin user, so restoring it would lock everyone out."
            )
        # Prove the vector index is readable too, not just the plain tables.
        conn.execute("SELECT COUNT(*) FROM doc_chunks").fetchone()
    except sqlite3.Error as exc:
        raise ValueError(f"The database in this backup is damaged ({exc}).")
    finally:
        conn.close()


def _extract_backup(upload, workdir):
    """Save the upload into `workdir`; return (db_path, uploads_path_or_None)."""
    raw_path = os.path.join(workdir, "upload.bin")
    upload.save(raw_path)
    with open(raw_path, "rb") as fh:
        head = fh.read(16)

    if head.startswith(SQLITE_MAGIC):
        # Legacy / bare database backup: no files inside, uploads stay as-is.
        return raw_path, None

    if not zipfile.is_zipfile(raw_path):
        raise ValueError("That file is neither a backup .zip nor a SQLite .db file.")

    extract_root = os.path.join(workdir, "extracted")
    os.makedirs(extract_root)
    root_real = os.path.realpath(extract_root)
    with zipfile.ZipFile(raw_path) as zf:
        names = zf.namelist()
        if "estate.db" not in names:
            raise ValueError("This zip has no estate.db inside, so it is not an Estate Copilot backup.")
        for member in zf.infolist():
            if member.filename != "estate.db" and not member.filename.startswith("uploads/") \
                    and member.filename != "manifest.json":
                raise ValueError(f"Unexpected entry in backup: {member.filename}")
            target = os.path.realpath(os.path.join(extract_root, member.filename))
            if target != root_real and not target.startswith(root_real + os.sep):
                raise ValueError("Backup contains an unsafe file path.")
        zf.extractall(extract_root)

    uploads_path = os.path.join(extract_root, "uploads")
    return (
        os.path.join(extract_root, "estate.db"),
        uploads_path if os.path.isdir(uploads_path) else None,
    )


def _restore_from(db_path, uploads_path):
    """Swap the live database (and uploads) for the validated backup.

    The current state is kept in instance/pre-restore-<timestamp>/ first, so a
    wrong restore can itself be undone.
    """
    live_db_path = current_app.config["DATABASE_PATH"]

    # Bring an older backup up to the current schema (adds missing columns,
    # rebuilds FTS indexes) while it is still a temp copy, so a failure here
    # leaves the live data untouched.
    from app.commands import upgrade_schema
    prep = sqlite3.connect(db_path)
    prep.row_factory = sqlite3.Row
    try:
        prep.enable_load_extension(True)
        sqlite_vec.load(prep)
        prep.enable_load_extension(False)
        upgrade_schema(prep)
    finally:
        prep.close()

    safety_dir = os.path.join(_instance_dir(), f"pre-restore-{_timestamp()}")
    suffix = 1
    while os.path.exists(safety_dir):  # two restores within the same second
        safety_dir = os.path.join(_instance_dir(), f"pre-restore-{_timestamp()}-{suffix}")
        suffix += 1
    os.makedirs(safety_dir)

    # Release this request's connection before touching the file.
    old = g.pop("db", None)
    if old is not None:
        _snapshot_db(old, os.path.join(safety_dir, "estate.db"))
        old.close()
    elif os.path.exists(live_db_path):
        live = sqlite3.connect(live_db_path)
        try:
            _snapshot_db(live, os.path.join(safety_dir, "estate.db"))
        finally:
            live.close()

    # Copy the backup INTO the live database with SQLite's backup API, rather
    # than replacing the file: correct under WAL and with other open handles.
    src = sqlite3.connect(db_path)
    dest = sqlite3.connect(live_db_path)
    try:
        src.backup(dest)
    finally:
        src.close()
        dest.close()

    if uploads_path is not None:
        live_uploads = _uploads_dir()
        if os.path.isdir(live_uploads):
            shutil.move(live_uploads, os.path.join(safety_dir, "uploads"))
        shutil.copytree(uploads_path, live_uploads)

    return os.path.basename(safety_dir)


def register(app):
    @app.route("/app/backup/db")
    @admin_required
    def backup_database():
        """Download one .zip with a consistent DB snapshot plus every upload."""
        workdir = tempfile.mkdtemp()
        db_copy = os.path.join(workdir, "estate.db")
        _snapshot_db(get_db(), db_copy)

        zip_path = os.path.join(workdir, "backup.zip")
        uploads = _uploads_dir()
        file_count = 0
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(db_copy, "estate.db")
            if os.path.isdir(uploads):
                for folder, _dirs, files in os.walk(uploads):
                    for name in files:
                        full = os.path.join(folder, name)
                        rel = os.path.relpath(full, uploads)
                        zf.write(full, os.path.join("uploads", rel))
                        file_count += 1
            zf.writestr("manifest.json", json.dumps({
                "format": BACKUP_FORMAT,
                "app": "estate-copilot",
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "upload_files": file_count,
            }, indent=2))

        @after_this_request
        def cleanup(response):
            shutil.rmtree(workdir, ignore_errors=True)
            return response

        return send_file(
            zip_path, as_attachment=True,
            download_name=f"estate-backup-{_timestamp()}.zip", mimetype="application/zip",
        )

    @app.route("/app/backup/restore", methods=["POST"])
    @admin_required
    def restore_backup():
        upload = request.files.get("backup_file")
        if upload is None or not upload.filename:
            return redirect(url_for("settings", restore_error="Choose a backup file to restore."))
        if request.form.get("confirm_word", "").strip() != RESTORE_CONFIRM_WORD:
            return redirect(url_for(
                "settings", restore_error=f"Type {RESTORE_CONFIRM_WORD} to confirm the restore.",
            ))

        workdir = tempfile.mkdtemp()
        try:
            db_path, uploads_path = _extract_backup(upload, workdir)
            _validate_backup_db(db_path)
            safety = _restore_from(db_path, uploads_path)
        except ValueError as exc:
            return redirect(url_for(
                "settings", restore_error=f"Restore refused, nothing was changed: {exc}",
            ))
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        return redirect(url_for("settings", restored=safety))

    @app.route("/app/backup/export/assets.csv")
    @admin_required
    def export_assets_csv():
        db = get_db()
        fields = [
            "id", "name", "category", "estimated_value", "sale_price",
            "status", "notes", "created_at", "updated_at",
        ]
        rows = db.execute(f"SELECT {', '.join(fields)} FROM assets ORDER BY id ASC").fetchall()
        return _csv_response(rows, fields, f"assets-{_timestamp()}.csv")

    @app.route("/app/backup/export/liabilities.csv")
    @admin_required
    def export_liabilities_csv():
        db = get_db()
        fields = [
            "id", "creditor", "description", "amount", "due_date",
            "status", "notes", "created_at", "updated_at",
        ]
        rows = db.execute(f"SELECT {', '.join(fields)} FROM liabilities ORDER BY id ASC").fetchall()
        return _csv_response(rows, fields, f"liabilities-{_timestamp()}.csv")

    @app.route("/app/backup/export/events.csv")
    @admin_required
    def export_events_csv():
        db = get_db()
        fields = [
            "id", "title", "contact", "type", "email_direction",
            "event_date", "notes", "created_at", "updated_at",
        ]
        rows = db.execute(f"SELECT {', '.join(fields)} FROM events ORDER BY id ASC").fetchall()
        return _csv_response(rows, fields, f"events-{_timestamp()}.csv")

    @app.route("/app/backup/export/money.csv")
    @admin_required
    def export_money_csv():
        db = get_db()
        fields = [
            "id", "entry_date", "account", "kind", "category", "amount", "description",
            "asset_id", "liability_id", "checked_with_bank_on", "created_at",
        ]
        rows = db.execute(
            """
            SELECT e.id AS id, e.entry_date AS entry_date,
                   CASE WHEN a.kind = 'bank' THEN 'Estate bank account'
                        ELSE 'Cash held by ' || COALESCE(u.name, u.email, '?') END AS account,
                   e.kind AS kind, e.category AS category,
                   printf('%.2f', e.amount_cents / 100.0) AS amount,
                   e.description AS description, e.asset_id AS asset_id,
                   e.liability_id AS liability_id, r.statement_date AS checked_with_bank_on,
                   e.created_at AS created_at
            FROM money_entries e
            JOIN money_accounts a ON a.id = e.account_id
            LEFT JOIN users u ON u.id = a.user_id
            LEFT JOIN reconciliations r ON r.id = e.reconciliation_id
            ORDER BY e.entry_date ASC, e.id ASC
            """
        ).fetchall()
        # Readable category names in the file the accountant / heirs receive.
        rows = [dict(r, category=estate_money.CATEGORY_LABELS.get(r["category"], r["category"] or "")) for r in rows]
        return _csv_response(rows, fields, f"money-{_timestamp()}.csv")

    @app.route("/app/backup/export/tasks.csv")
    @admin_required
    def export_tasks_csv():
        db = get_db()
        fields = [
            "id", "title", "owner", "due_date", "priority",
            "status", "notes", "created_at", "updated_at",
        ]
        rows = db.execute(
            """
            SELECT tasks.id AS id, tasks.title AS title, users.email AS owner,
                   tasks.due_date AS due_date, tasks.priority AS priority,
                   tasks.status AS status, tasks.notes AS notes,
                   tasks.created_at AS created_at, tasks.updated_at AS updated_at
            FROM tasks
            LEFT JOIN users ON users.id = tasks.user_id
            ORDER BY tasks.id ASC
            """
        ).fetchall()
        return _csv_response(rows, fields, f"tasks-{_timestamp()}.csv")
