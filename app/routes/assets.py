from flask import abort, redirect, render_template, request, url_for

from app.auth import admin_required
from app.db import get_db

PAGE_SIZE = 10
STATUSES = ["Active", "Pending", "Sold", "Distributed"]


def _filter_sort(rows, q, status, sort, order):
    if q:
        q_l = q.lower()
        rows = [r for r in rows if q_l in str(dict(r).values()).lower()]
    if status:
        rows = [r for r in rows if r["status"] == status]
    rows = sorted(rows, key=lambda r: str(r[sort] if r[sort] is not None else ""), reverse=(order == "desc"))
    return rows


def register(app):
    @app.route("/app/assets")
    def list_assets():
        db = get_db()
        q = request.args.get("q", "")
        status = request.args.get("status", "")
        sort = request.args.get("sort", "created_at")
        order = request.args.get("order", "desc")
        page = max(1, int(request.args.get("page", 1)))

        rows = db.execute("SELECT * FROM assets").fetchall()
        rows = _filter_sort(rows, q, status, sort, order)

        total = len(rows)
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, total_pages)
        rows = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]

        return render_template(
            "blocks/assets_list.html",
            assets=rows,
            q=q,
            status=status,
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
            db.execute(
                "INSERT INTO assets (name, category, estimated_value, sale_price, status, notes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    request.form.get("name", "").strip(),
                    request.form.get("category", "").strip(),
                    request.form.get("estimated_value") or 0,
                    request.form.get("sale_price") or None,
                    request.form.get("status", "Active"),
                    request.form.get("notes", "").strip(),
                ),
            )
            db.commit()
            return redirect(url_for("list_assets"))
        return render_template("blocks/assets_form.html", item=None, statuses=STATUSES)

    @app.route("/app/assets/<int:item_id>")
    def detail_asset(item_id):
        db = get_db()
        item = db.execute("SELECT * FROM assets WHERE id = ?", (item_id,)).fetchone()
        if item is None:
            abort(404)
        return render_template("blocks/assets_detail.html", item=item)

    @app.route("/app/assets/<int:item_id>/edit", methods=["GET", "POST"])
    @admin_required
    def edit_asset(item_id):
        db = get_db()
        item = db.execute("SELECT * FROM assets WHERE id = ?", (item_id,)).fetchone()
        if item is None:
            abort(404)
        if request.method == "POST":
            db.execute(
                "UPDATE assets SET name=?, category=?, estimated_value=?, sale_price=?, status=?, notes=?, "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                (
                    request.form.get("name", "").strip(),
                    request.form.get("category", "").strip(),
                    request.form.get("estimated_value") or 0,
                    request.form.get("sale_price") or None,
                    request.form.get("status", "Active"),
                    request.form.get("notes", "").strip(),
                    item_id,
                ),
            )
            db.commit()
            return redirect(url_for("list_assets"))
        return render_template("blocks/assets_form.html", item=item, statuses=STATUSES)

    @app.route("/app/assets/<int:item_id>/delete", methods=["POST"])
    @admin_required
    def delete_asset(item_id):
        db = get_db()
        db.execute("DELETE FROM assets WHERE id = ?", (item_id,))
        db.commit()
        return redirect(url_for("list_assets"))
