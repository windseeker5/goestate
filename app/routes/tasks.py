from flask import abort, g, redirect, render_template, request, url_for

from app.db import get_db

PAGE_SIZE = 10
PRIORITIES = ["Low", "Medium", "High"]
STATUSES = ["Open", "In Progress", "Done", "Cancelled"]


def _filter_sort(rows, q, sort, order):
    if q:
        q_l = q.lower()
        rows = [r for r in rows if q_l in str(dict(r).values()).lower()]
    rows = sorted(rows, key=lambda r: str(r[sort] if r[sort] is not None else ""), reverse=(order == "desc"))
    return rows


def register(app):
    @app.route("/app/tasks")
    def list_tasks():
        db = get_db()
        q = request.args.get("q", "")
        sort = request.args.get("sort", "due_date")
        order = request.args.get("order", "asc")
        page = max(1, int(request.args.get("page", 1)))

        rows = db.execute("SELECT * FROM tasks WHERE user_id = ?", (g.user["id"],)).fetchall()
        rows = _filter_sort(rows, q, sort, order)

        total = len(rows)
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, total_pages)
        rows = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]

        return render_template(
            "blocks/tasks_list.html",
            tasks=rows,
            q=q,
            sort=sort,
            order=order,
            page=page,
            total_pages=total_pages,
            total=total,
        )

    @app.route("/app/tasks/new", methods=["GET", "POST"])
    def new_task():
        if request.method == "POST":
            db = get_db()
            title = request.form.get("title", "").strip()
            if not title:
                return render_template(
                    "blocks/tasks_form.html",
                    item=None,
                    priorities=PRIORITIES,
                    statuses=STATUSES,
                    form_data=request.form,
                    error="Title is required.",
                ), 400
            db.execute(
                "INSERT INTO tasks (user_id, title, due_date, priority, status, notes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    g.user["id"],
                    title,
                    request.form.get("due_date") or None,
                    request.form.get("priority", "Medium"),
                    request.form.get("status", "Open"),
                    request.form.get("notes", "").strip(),
                ),
            )
            db.commit()
            return redirect(url_for("list_tasks"))
        return render_template(
            "blocks/tasks_form.html", item=None, priorities=PRIORITIES, statuses=STATUSES, form_data=None, error=None
        )

    @app.route("/app/tasks/<int:item_id>")
    def detail_task(item_id):
        db = get_db()
        item = db.execute(
            "SELECT * FROM tasks WHERE id = ? AND user_id = ?", (item_id, g.user["id"])
        ).fetchone()
        if item is None:
            abort(404)
        return render_template("blocks/tasks_detail.html", item=item)

    @app.route("/app/tasks/<int:item_id>/edit", methods=["GET", "POST"])
    def edit_task(item_id):
        db = get_db()
        item = db.execute(
            "SELECT * FROM tasks WHERE id = ? AND user_id = ?", (item_id, g.user["id"])
        ).fetchone()
        if item is None:
            abort(404)
        if request.method == "POST":
            title = request.form.get("title", "").strip()
            if not title:
                return render_template(
                    "blocks/tasks_form.html",
                    item=item,
                    priorities=PRIORITIES,
                    statuses=STATUSES,
                    form_data=request.form,
                    error="Title is required.",
                ), 400
            db.execute(
                "UPDATE tasks SET title=?, due_date=?, priority=?, status=?, notes=?, "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=? AND user_id=?",
                (
                    title,
                    request.form.get("due_date") or None,
                    request.form.get("priority", "Medium"),
                    request.form.get("status", "Open"),
                    request.form.get("notes", "").strip(),
                    item_id,
                    g.user["id"],
                ),
            )
            db.commit()
            return redirect(url_for("list_tasks"))
        return render_template(
            "blocks/tasks_form.html", item=item, priorities=PRIORITIES, statuses=STATUSES, form_data=None, error=None
        )

    @app.route("/app/tasks/<int:item_id>/toggle", methods=["POST"])
    def toggle_task(item_id):
        db = get_db()
        item = db.execute(
            "SELECT id, status FROM tasks WHERE id = ? AND user_id = ?", (item_id, g.user["id"])
        ).fetchone()
        if item is None:
            abort(404)
        new_status = "Open" if item["status"] in ("Done", "Cancelled") else "Done"
        db.execute(
            "UPDATE tasks SET status=?, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
            "WHERE id=? AND user_id=?",
            (new_status, item_id, g.user["id"]),
        )
        db.commit()
        return redirect(request.referrer or url_for("dashboard"))

    @app.route("/app/tasks/<int:item_id>/delete", methods=["POST"])
    def delete_task(item_id):
        db = get_db()
        item = db.execute(
            "SELECT id FROM tasks WHERE id = ? AND user_id = ?", (item_id, g.user["id"])
        ).fetchone()
        if item is None:
            abort(404)
        db.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", (item_id, g.user["id"]))
        db.commit()
        return redirect(url_for("list_tasks"))
