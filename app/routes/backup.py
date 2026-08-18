"""Admin-only database backup and CSV data export.

Full backups include users.password_hash (hashed, not plaintext) and
settings.value for llm_api_key (stored in plaintext). Acceptable here since
this route is @admin_required and the app is single-tenant/locally-hosted —
same trust boundary as /app/users. No redaction: an admin restoring a backup
needs the real values intact.
"""

import csv
import io
import os
import sqlite3
import tempfile
from datetime import datetime

from flask import Response, after_this_request, send_file

from app.auth import admin_required
from app.db import get_db


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


def register(app):
    @app.route("/app/backup/db")
    @admin_required
    def backup_database():
        src = get_db()
        fd, tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        dest = sqlite3.connect(tmp_path)
        src.backup(dest)
        dest.close()

        @after_this_request
        def cleanup(response):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            return response

        filename = f"estate-backup-{_timestamp()}.db"
        return send_file(
            tmp_path, as_attachment=True, download_name=filename, mimetype="application/vnd.sqlite3"
        )

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
