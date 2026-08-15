from flask import abort, redirect, render_template, request, url_for

from app.auth import admin_required
from app.db import get_db

PAGE_SIZE = 10
STATUSES = ["Unpaid", "Paid", "Disputed", "Cancelled"]


def _filter_sort(rows, q, status, sort, order):
    if q:
        q_l = q.lower()
        rows = [r for r in rows if q_l in str(dict(r).values()).lower()]
    if status:
        rows = [r for r in rows if r["status"] == status]
    rows = sorted(rows, key=lambda r: str(r[sort] if r[sort] is not None else ""), reverse=(order == "desc"))
    return rows


def register(app):
    @app.route("/app/liabilities")
    def list_liabilities():
        db = get_db()
        q = request.args.get("q", "")
        status = request.args.get("status", "")
        sort = request.args.get("sort", "due_date")
        order = request.args.get("order", "desc")
        page = max(1, int(request.args.get("page", 1)))

        rows = db.execute("SELECT * FROM liabilities").fetchall()
        rows = _filter_sort(rows, q, status, sort, order)

        total = len(rows)
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, total_pages)
        rows = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]

        return render_template(
            "blocks/liabilities_list.html",
            liabilities=rows,
            q=q,
            status=status,
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
            db.execute(
                "INSERT INTO liabilities (creditor, description, amount, due_date, status, notes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    request.form.get("creditor", "").strip(),
                    request.form.get("description", "").strip(),
                    request.form.get("amount") or 0,
                    request.form.get("due_date") or None,
                    request.form.get("status", "Unpaid"),
                    request.form.get("notes", "").strip(),
                ),
            )
            db.commit()
            return redirect(url_for("list_liabilities"))
        return render_template("blocks/liabilities_form.html", item=None, statuses=STATUSES)

    @app.route("/app/liabilities/<int:item_id>")
    def detail_liability(item_id):
        db = get_db()
        item = db.execute("SELECT * FROM liabilities WHERE id = ?", (item_id,)).fetchone()
        if item is None:
            abort(404)
        return render_template("blocks/liabilities_detail.html", item=item)

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
            return redirect(url_for("list_liabilities"))
        return render_template("blocks/liabilities_form.html", item=item, statuses=STATUSES)

    @app.route("/app/liabilities/<int:item_id>/delete", methods=["POST"])
    @admin_required
    def delete_liability(item_id):
        db = get_db()
        db.execute("DELETE FROM liabilities WHERE id = ?", (item_id,))
        db.commit()
        return redirect(url_for("list_liabilities"))
