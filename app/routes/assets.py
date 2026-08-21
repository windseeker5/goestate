from flask import abort, redirect, render_template, request, url_for

from app.auth import admin_required
from app.db import get_db
from app.document_storage import attach_documents
from app.photo_storage import PhotoValidationError, delete_photo, save_photo, send_photo

PAGE_SIZE = 10
STATUSES = ["Active", "Pending", "Sold", "Transferred"]


def _users(db):
    return db.execute("SELECT id, name, email FROM users ORDER BY name").fetchall()


def _filter_sort(rows, q, sort, order):
    if q:
        q_l = q.lower()
        rows = [r for r in rows if q_l in str(dict(r).values()).lower()]
    rows = sorted(rows, key=lambda r: str(r[sort] if r[sort] is not None else ""), reverse=(order == "desc"))
    return rows


def register(app):
    @app.route("/app/assets")
    def list_assets():
        db = get_db()
        q = request.args.get("q", "")
        sold_by = request.args.get("sold_by", "")
        sort = request.args.get("sort", "created_at")
        order = request.args.get("order", "desc")
        page = max(1, int(request.args.get("page", 1)))

        rows = db.execute(
            """
            SELECT a.*,
                   u.name AS sold_by_name,
                   u.email AS sold_by_email,
                   (
                       SELECT d.id
                       FROM documents d
                       WHERE d.linked_entity_type = 'asset'
                         AND d.linked_entity_id = a.id
                         AND d.filepath <> ''
                       ORDER BY d.created_at DESC, d.id DESC
                       LIMIT 1
                   ) AS document_id
            FROM assets a
            LEFT JOIN users u ON u.id = a.sold_by
            """
        ).fetchall()
        if sold_by:
            rows = [r for r in rows if str(r["sold_by"]) == sold_by]
        rows = _filter_sort(rows, q, sort, order)

        total = len(rows)
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, total_pages)
        rows = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]

        return render_template(
            "blocks/assets_list.html",
            assets=rows,
            q=q,
            sold_by=sold_by,
            users=_users(db),
            sort=sort,
            order=order,
            page=page,
            total_pages=total_pages,
            total=total,
        )

    @app.route("/app/assets/new", methods=["GET", "POST"])
    @admin_required
    def new_asset():
        if request.method == "POST":
            db = get_db()
            photo_path = None
            photo_mime_type = None
            try:
                photo = request.files.get("photo")
                if photo and photo.filename:
                    photo_path, photo_mime_type = save_photo(photo, "assets")
                sold_by = request.form.get("sold_by") or None
                cur = db.execute(
                    "INSERT INTO assets "
                    "(name, category, estimated_value, sale_price, status, sold_by, notes, photo_path, photo_mime_type) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        request.form.get("name", "").strip(),
                        request.form.get("category", "").strip(),
                        request.form.get("estimated_value") or 0,
                        request.form.get("sale_price") or None,
                        request.form.get("status", "Active"),
                        sold_by,
                        request.form.get("notes", "").strip(),
                        photo_path,
                        photo_mime_type,
                    ),
                )
                db.commit()
            except PhotoValidationError as exc:
                return render_template(
                    "blocks/assets_form.html",
                    item=None,
                    statuses=STATUSES,
                    users=_users(db),
                    form_data=request.form,
                    error=str(exc),
                ), 400
            except Exception:
                db.rollback()
                delete_photo(photo_path)
                raise
            return redirect(url_for("detail_asset", item_id=cur.lastrowid))
        return render_template(
            "blocks/assets_form.html", item=None, statuses=STATUSES, users=_users(get_db()), form_data=None
        )

    @app.route("/app/assets/<int:item_id>")
    def detail_asset(item_id):
        db = get_db()
        item = db.execute(
            "SELECT a.*, u.name AS sold_by_name, u.email AS sold_by_email "
            "FROM assets a LEFT JOIN users u ON u.id = a.sold_by WHERE a.id = ?",
            (item_id,),
        ).fetchone()
        if item is None:
            abort(404)
        documents = db.execute(
            "SELECT * FROM documents WHERE linked_entity_type = 'asset' AND linked_entity_id = ? "
            "ORDER BY created_at DESC",
            (item_id,),
        ).fetchall()
        return render_template("blocks/assets_detail.html", item=item, documents=documents)

    @app.route("/app/assets/<int:item_id>/documents/upload", methods=["POST"])
    @admin_required
    def upload_asset_documents(item_id):
        db = get_db()
        item = db.execute("SELECT id FROM assets WHERE id = ?", (item_id,)).fetchone()
        if item is None:
            abort(404)
        attach_documents(db, "asset", item_id, request.files.getlist("documents"))
        return redirect(url_for("detail_asset", item_id=item_id))

    @app.route("/app/assets/<int:item_id>/photo/upload", methods=["POST"])
    @admin_required
    def upload_asset_photo(item_id):
        db = get_db()
        item = db.execute("SELECT * FROM assets WHERE id = ?", (item_id,)).fetchone()
        if item is None:
            abort(404)
        photo = request.files.get("photo")
        if not photo or not photo.filename:
            return redirect(url_for("detail_asset", item_id=item_id))
        try:
            new_photo_path, new_photo_mime_type = save_photo(photo, "assets")
        except PhotoValidationError as exc:
            documents = db.execute(
                "SELECT * FROM documents WHERE linked_entity_type = 'asset' AND linked_entity_id = ? "
                "ORDER BY created_at DESC",
                (item_id,),
            ).fetchall()
            return render_template(
                "blocks/assets_detail.html", item=item, documents=documents, error=str(exc)
            ), 400
        db.execute(
            "UPDATE assets SET photo_path=?, photo_mime_type=?, "
            "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
            (new_photo_path, new_photo_mime_type, item_id),
        )
        db.commit()
        if item["photo_path"]:
            delete_photo(item["photo_path"])
        return redirect(url_for("detail_asset", item_id=item_id))

    @app.route("/app/assets/<int:item_id>/photo/delete", methods=["POST"])
    @admin_required
    def delete_asset_photo(item_id):
        db = get_db()
        item = db.execute("SELECT photo_path FROM assets WHERE id = ?", (item_id,)).fetchone()
        if item is None:
            abort(404)
        db.execute(
            "UPDATE assets SET photo_path=NULL, photo_mime_type=NULL, "
            "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
            (item_id,),
        )
        db.commit()
        delete_photo(item["photo_path"])
        return redirect(url_for("detail_asset", item_id=item_id))

    @app.route("/app/assets/<int:item_id>/photo")
    def view_asset_photo(item_id):
        db = get_db()
        item = db.execute(
            "SELECT photo_path, photo_mime_type FROM assets WHERE id = ?", (item_id,)
        ).fetchone()
        if item is None or not item["photo_path"]:
            abort(404)
        return send_photo(item["photo_path"], item["photo_mime_type"])

    @app.route("/app/assets/<int:item_id>/edit", methods=["GET", "POST"])
    @admin_required
    def edit_asset(item_id):
        db = get_db()
        item = db.execute("SELECT * FROM assets WHERE id = ?", (item_id,)).fetchone()
        if item is None:
            abort(404)
        if request.method == "POST":
            db.execute(
                "UPDATE assets SET name=?, category=?, estimated_value=?, sale_price=?, status=?, sold_by=?, notes=?, "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                (
                    request.form.get("name", "").strip(),
                    request.form.get("category", "").strip(),
                    request.form.get("estimated_value") or 0,
                    request.form.get("sale_price") or None,
                    request.form.get("status", "Active"),
                    request.form.get("sold_by") or None,
                    request.form.get("notes", "").strip(),
                    item_id,
                ),
            )
            db.commit()
            return redirect(url_for("detail_asset", item_id=item_id))
        return render_template(
            "blocks/assets_form.html", item=item, statuses=STATUSES, users=_users(db), form_data=None
        )

    @app.route("/app/assets/<int:item_id>/delete", methods=["POST"])
    @admin_required
    def delete_asset(item_id):
        db = get_db()
        item = db.execute("SELECT photo_path FROM assets WHERE id = ?", (item_id,)).fetchone()
        if item is None:
            abort(404)
        db.execute("DELETE FROM assets WHERE id = ?", (item_id,))
        db.commit()
        delete_photo(item["photo_path"])
        return redirect(url_for("list_assets"))

    @app.route("/app/assets/bulk-status", methods=["POST"])
    @admin_required
    def bulk_update_asset_status():
        target_status = request.form.get("status", "")
        if target_status not in STATUSES:
            abort(400)
        ids = [int(i) for i in request.form.getlist("asset_ids") if i.isdigit()]
        if ids:
            db = get_db()
            # sold_by is intentionally left untouched here: the only bulk
            # action exposed in the UI is Sold -> Transferred, where
            # attribution should persist from the original sale. A future
            # bulk transition INTO "Sold" would need the same becoming-sold
            # logic as edit_asset().
            placeholders = ",".join("?" * len(ids))
            db.execute(
                f"UPDATE assets SET status=?, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
                f"WHERE id IN ({placeholders})",
                (target_status, *ids),
            )
            db.commit()
        return redirect(request.referrer or url_for("list_assets"))
