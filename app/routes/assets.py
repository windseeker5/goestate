from flask import abort, redirect, render_template, request, url_for

from app.auth import admin_required
from app.db import get_db
from app.photo_storage import PhotoValidationError, delete_photo, save_photo, send_photo

PAGE_SIZE = 10
STATUSES = ["Active", "Pending", "Sold", "Distributed"]


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
        sort = request.args.get("sort", "created_at")
        order = request.args.get("order", "desc")
        page = max(1, int(request.args.get("page", 1)))

        rows = db.execute("SELECT * FROM assets").fetchall()
        rows = _filter_sort(rows, q, sort, order)

        total = len(rows)
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, total_pages)
        rows = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]

        return render_template(
            "blocks/assets_list.html",
            assets=rows,
            q=q,
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
                db.execute(
                    "INSERT INTO assets "
                    "(name, category, estimated_value, sale_price, status, notes, photo_path, photo_mime_type) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        request.form.get("name", "").strip(),
                        request.form.get("category", "").strip(),
                        request.form.get("estimated_value") or 0,
                        request.form.get("sale_price") or None,
                        request.form.get("status", "Active"),
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
                    form_data=request.form,
                    error=str(exc),
                ), 400
            except Exception:
                db.rollback()
                delete_photo(photo_path)
                raise
            return redirect(url_for("list_assets"))
        return render_template(
            "blocks/assets_form.html", item=None, statuses=STATUSES, form_data=None, error=None
        )

    @app.route("/app/assets/<int:item_id>")
    def detail_asset(item_id):
        db = get_db()
        item = db.execute("SELECT * FROM assets WHERE id = ?", (item_id,)).fetchone()
        if item is None:
            abort(404)
        return render_template("blocks/assets_detail.html", item=item)

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
            old_photo_path = item["photo_path"]
            new_photo_path = old_photo_path
            new_photo_mime_type = item["photo_mime_type"]
            saved_photo_path = None
            try:
                photo = request.files.get("photo")
                if photo and photo.filename:
                    saved_photo_path, new_photo_mime_type = save_photo(photo, "assets")
                    new_photo_path = saved_photo_path
                elif request.form.get("remove_photo") == "1":
                    new_photo_path = None
                    new_photo_mime_type = None

                db.execute(
                    "UPDATE assets SET name=?, category=?, estimated_value=?, sale_price=?, status=?, notes=?, "
                    "photo_path=?, photo_mime_type=?, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                    (
                        request.form.get("name", "").strip(),
                        request.form.get("category", "").strip(),
                        request.form.get("estimated_value") or 0,
                        request.form.get("sale_price") or None,
                        request.form.get("status", "Active"),
                        request.form.get("notes", "").strip(),
                        new_photo_path,
                        new_photo_mime_type,
                        item_id,
                    ),
                )
                db.commit()
            except PhotoValidationError as exc:
                return render_template(
                    "blocks/assets_form.html",
                    item=item,
                    statuses=STATUSES,
                    form_data=request.form,
                    error=str(exc),
                ), 400
            except Exception:
                db.rollback()
                delete_photo(saved_photo_path)
                raise

            if old_photo_path and old_photo_path != new_photo_path:
                delete_photo(old_photo_path)
            return redirect(url_for("list_assets"))
        return render_template(
            "blocks/assets_form.html", item=item, statuses=STATUSES, form_data=None, error=None
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
