from flask import abort, redirect, render_template, request, url_for

from app.auth import admin_required
from app.db import get_db
from app.document_storage import attach_documents
from app.photo_storage import PhotoValidationError, delete_photo, save_photo, send_photo

PAGE_SIZE = 10
STATUSES = ["Unpaid", "Paid", "Disputed", "Cancelled"]


def _filter_sort(rows, q, sort, order):
    if q:
        q_l = q.lower()
        rows = [r for r in rows if q_l in str(dict(r).values()).lower()]
    rows = sorted(rows, key=lambda r: str(r[sort] if r[sort] is not None else ""), reverse=(order == "desc"))
    return rows


def register(app):
    @app.route("/app/liabilities")
    def list_liabilities():
        db = get_db()
        q = request.args.get("q", "")
        sort = request.args.get("sort", "due_date")
        order = request.args.get("order", "desc")
        page = max(1, int(request.args.get("page", 1)))

        rows = db.execute(
            """
            SELECT l.*,
                   (
                       SELECT d.id
                       FROM documents d
                       WHERE d.linked_entity_type = 'liability'
                         AND d.linked_entity_id = l.id
                         AND d.filepath <> ''
                       ORDER BY d.created_at DESC, d.id DESC
                       LIMIT 1
                   ) AS document_id
            FROM liabilities l
            """
        ).fetchall()
        rows = _filter_sort(rows, q, sort, order)

        total = len(rows)
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, total_pages)
        rows = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]

        return render_template(
            "blocks/liabilities_list.html",
            liabilities=rows,
            q=q,
            sort=sort,
            order=order,
            page=page,
            total_pages=total_pages,
            total=total,
        )

    @app.route("/app/liabilities/new", methods=["GET", "POST"])
    @admin_required
    def new_liability():
        if request.method == "POST":
            db = get_db()
            photo_path = None
            photo_mime_type = None
            try:
                photo = request.files.get("photo")
                if photo and photo.filename:
                    photo_path, photo_mime_type = save_photo(photo, "liabilities")
                cur = db.execute(
                    "INSERT INTO liabilities "
                    "(creditor, description, amount, due_date, status, notes, photo_path, photo_mime_type) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        request.form.get("creditor", "").strip(),
                        request.form.get("description", "").strip(),
                        request.form.get("amount") or 0,
                        request.form.get("due_date") or None,
                        request.form.get("status", "Unpaid"),
                        request.form.get("notes", "").strip(),
                        photo_path,
                        photo_mime_type,
                    ),
                )
                db.commit()
            except PhotoValidationError as exc:
                return render_template(
                    "blocks/liabilities_form.html",
                    item=None,
                    statuses=STATUSES,
                    form_data=request.form,
                    error=str(exc),
                ), 400
            except Exception:
                db.rollback()
                delete_photo(photo_path)
                raise
            return redirect(url_for("detail_liability", item_id=cur.lastrowid))
        return render_template(
            "blocks/liabilities_form.html", item=None, statuses=STATUSES, form_data=None
        )

    @app.route("/app/liabilities/<int:item_id>")
    def detail_liability(item_id):
        db = get_db()
        item = db.execute("SELECT * FROM liabilities WHERE id = ?", (item_id,)).fetchone()
        if item is None:
            abort(404)
        documents = db.execute(
            "SELECT * FROM documents WHERE linked_entity_type = 'liability' AND linked_entity_id = ? "
            "ORDER BY created_at DESC",
            (item_id,),
        ).fetchall()
        return render_template("blocks/liabilities_detail.html", item=item, documents=documents)

    @app.route("/app/liabilities/<int:item_id>/documents/upload", methods=["POST"])
    @admin_required
    def upload_liability_documents(item_id):
        db = get_db()
        item = db.execute("SELECT id FROM liabilities WHERE id = ?", (item_id,)).fetchone()
        if item is None:
            abort(404)
        attach_documents(db, "liability", item_id, request.files.getlist("documents"))
        return redirect(url_for("detail_liability", item_id=item_id))

    @app.route("/app/liabilities/<int:item_id>/photo/upload", methods=["POST"])
    @admin_required
    def upload_liability_photo(item_id):
        db = get_db()
        item = db.execute("SELECT * FROM liabilities WHERE id = ?", (item_id,)).fetchone()
        if item is None:
            abort(404)
        photo = request.files.get("photo")
        if not photo or not photo.filename:
            return redirect(url_for("detail_liability", item_id=item_id))
        try:
            new_photo_path, new_photo_mime_type = save_photo(photo, "liabilities")
        except PhotoValidationError as exc:
            documents = db.execute(
                "SELECT * FROM documents WHERE linked_entity_type = 'liability' AND linked_entity_id = ? "
                "ORDER BY created_at DESC",
                (item_id,),
            ).fetchall()
            return render_template(
                "blocks/liabilities_detail.html", item=item, documents=documents, error=str(exc)
            ), 400
        db.execute(
            "UPDATE liabilities SET photo_path=?, photo_mime_type=?, "
            "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
            (new_photo_path, new_photo_mime_type, item_id),
        )
        db.commit()
        if item["photo_path"]:
            delete_photo(item["photo_path"])
        return redirect(url_for("detail_liability", item_id=item_id))

    @app.route("/app/liabilities/<int:item_id>/photo/delete", methods=["POST"])
    @admin_required
    def delete_liability_photo(item_id):
        db = get_db()
        item = db.execute("SELECT photo_path FROM liabilities WHERE id = ?", (item_id,)).fetchone()
        if item is None:
            abort(404)
        db.execute(
            "UPDATE liabilities SET photo_path=NULL, photo_mime_type=NULL, "
            "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
            (item_id,),
        )
        db.commit()
        delete_photo(item["photo_path"])
        return redirect(url_for("detail_liability", item_id=item_id))

    @app.route("/app/liabilities/<int:item_id>/photo")
    def view_liability_photo(item_id):
        db = get_db()
        item = db.execute(
            "SELECT photo_path, photo_mime_type FROM liabilities WHERE id = ?", (item_id,)
        ).fetchone()
        if item is None or not item["photo_path"]:
            abort(404)
        return send_photo(item["photo_path"], item["photo_mime_type"])

    @app.route("/app/liabilities/<int:item_id>/edit", methods=["GET", "POST"])
    @admin_required
    def edit_liability(item_id):
        db = get_db()
        item = db.execute("SELECT * FROM liabilities WHERE id = ?", (item_id,)).fetchone()
        if item is None:
            abort(404)
        if request.method == "POST":
            db.execute(
                "UPDATE liabilities SET creditor=?, description=?, amount=?, due_date=?, status=?, notes=?, "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                (
                    request.form.get("creditor", "").strip(),
                    request.form.get("description", "").strip(),
                    request.form.get("amount") or 0,
                    request.form.get("due_date") or None,
                    request.form.get("status", "Unpaid"),
                    request.form.get("notes", "").strip(),
                    item_id,
                ),
            )
            db.commit()
            return redirect(url_for("detail_liability", item_id=item_id))
        return render_template(
            "blocks/liabilities_form.html", item=item, statuses=STATUSES, form_data=None
        )

    @app.route("/app/liabilities/<int:item_id>/delete", methods=["POST"])
    @admin_required
    def delete_liability(item_id):
        db = get_db()
        item = db.execute("SELECT photo_path FROM liabilities WHERE id = ?", (item_id,)).fetchone()
        if item is None:
            abort(404)
        db.execute("DELETE FROM liabilities WHERE id = ?", (item_id,))
        db.commit()
        delete_photo(item["photo_path"])
        return redirect(url_for("list_liabilities"))
