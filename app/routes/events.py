from flask import abort, redirect, render_template, request, url_for

from app.auth import admin_required
from app.db import get_db

PAGE_SIZE = 10
TYPES = ["Note", "Call", "Meeting", "Email", "Task"]


def _filter_sort(rows, q, type_, sort, order):
    if q:
        q_l = q.lower()
        rows = [r for r in rows if q_l in str(dict(r).values()).lower()]
    if type_:
        rows = [r for r in rows if r["type"] == type_]
    rows = sorted(rows, key=lambda r: str(r[sort] if r[sort] is not None else ""), reverse=(order == "desc"))
    return rows


def register(app):
    @app.route("/app/events")
    def list_events():
        db = get_db()
        q = request.args.get("q", "")
        type_ = request.args.get("type", "")
        sort = request.args.get("sort", "event_date")
        order = request.args.get("order", "desc")
        page = max(1, int(request.args.get("page", 1)))

        rows = db.execute("SELECT * FROM events").fetchall()
        rows = _filter_sort(rows, q, type_, sort, order)

        total = len(rows)
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, total_pages)
        rows = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]

        return render_template(
            "blocks/events_list.html",
            events=rows,
            q=q,
            type=type_,
            sort=sort,
            order=order,
            page=page,
            total_pages=total_pages,
            total=total,
        )

    @app.route("/app/events/new", methods=["GET", "POST"])
    @admin_required
    def new_event():
        if request.method == "POST":
            db = get_db()
            db.execute(
                "INSERT INTO events (title, contact, type, event_date, notes) VALUES (?, ?, ?, ?, ?)",
                (
                    request.form.get("title", "").strip(),
                    request.form.get("contact", "").strip(),
                    request.form.get("type", "Note"),
                    request.form.get("event_date") or None,
                    request.form.get("notes", "").strip(),
                ),
            )
            db.commit()
            return redirect(url_for("list_events"))
        return render_template("blocks/events_form.html", item=None, types=TYPES)

    @app.route("/app/events/<int:item_id>")
    def detail_event(item_id):
        db = get_db()
        item = db.execute("SELECT * FROM events WHERE id = ?", (item_id,)).fetchone()
        if item is None:
            abort(404)
        return render_template("blocks/events_detail.html", item=item)

    @app.route("/app/events/<int:item_id>/edit", methods=["GET", "POST"])
    @admin_required
    def edit_event(item_id):
        db = get_db()
        item = db.execute("SELECT * FROM events WHERE id = ?", (item_id,)).fetchone()
        if item is None:
            abort(404)
        if request.method == "POST":
            db.execute(
                "UPDATE events SET title=?, contact=?, type=?, event_date=?, notes=?, "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                (
                    request.form.get("title", "").strip(),
                    request.form.get("contact", "").strip(),
                    request.form.get("type", "Note"),
                    request.form.get("event_date") or None,
                    request.form.get("notes", "").strip(),
                    item_id,
                ),
            )
            db.commit()
            return redirect(url_for("list_events"))
        return render_template("blocks/events_form.html", item=item, types=TYPES)

    @app.route("/app/events/<int:item_id>/delete", methods=["POST"])
    @admin_required
    def delete_event(item_id):
        db = get_db()
        db.execute("DELETE FROM events WHERE id = ?", (item_id,))
        db.commit()
        return redirect(url_for("list_events"))
