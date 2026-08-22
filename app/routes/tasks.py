from flask import abort, g, redirect, render_template, request, url_for

from app.auth import admin_required
from app.db import get_db

PAGE_SIZE = 10
PRIORITIES = ["Low", "Medium", "High"]
STATUSES = ["Open", "In Progress", "Done", "Cancelled"]


def _users(db):
    return db.execute("SELECT id, name, email FROM users ORDER BY name").fetchall()


def _filter_sort(rows, q, sort, order):
    if q:
        q_l = q.lower()
        rows = [r for r in rows if q_l in str(dict(r).values()).lower()]
    rows = sorted(rows, key=lambda r: str(r[sort] if r[sort] is not None else ""), reverse=(order == "desc"))
    if sort == "due_date":
        # Keep undated tasks after dated ones in either date direction.
        rows = sorted(rows, key=lambda r: r["due_date"] is None)
    return rows


def register(app):
    @app.route("/app/tasks")
    def list_tasks():
        db = get_db()
        q = request.args.get("q", "")
        status = request.args.get("status", "")
        priority = request.args.get("priority", "")
        assigned_to = request.args.get("assigned_to", "")
        sort = request.args.get("sort", "due_date")
        order = request.args.get("order", "asc")
        page = max(1, int(request.args.get("page", 1)))

        # Ignore stale or hand-edited filter values rather than returning an
        # unexpectedly empty task list.
        status = status if status in STATUSES else ""
        priority = priority if priority in PRIORITIES else ""

        rows = db.execute(
            """
            SELECT t.*,
                   cu.name AS created_by_name, cu.email AS created_by_email,
                   au.name AS assigned_to_name, au.email AS assigned_to_email
            FROM tasks t
            LEFT JOIN users cu ON cu.id = t.user_id
            LEFT JOIN users au ON au.id = t.assigned_to
            """
        ).fetchall()
        if assigned_to:
            rows = [r for r in rows if str(r["assigned_to"]) == assigned_to]
        if status:
            rows = [row for row in rows if row["status"] == status]
        if priority:
            rows = [row for row in rows if row["priority"] == priority]
        rows = _filter_sort(rows, q, sort, order)

        total = len(rows)
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, total_pages)
        rows = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]

        return render_template(
            "blocks/tasks_list.html",
            tasks=rows,
            q=q,
            status=status,
            priority=priority,
            assigned_to=assigned_to,
            priorities=PRIORITIES[::-1],
            statuses=STATUSES,
            sort=sort,
            order=order,
            page=page,
            total_pages=total_pages,
            total=total,
        )

    @app.route("/app/tasks/new", methods=["GET", "POST"])
    @admin_required
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
                    users=_users(db),
                    form_data=request.form,
                    error="Title is required.",
                ), 400
            db.execute(
                "INSERT INTO tasks (user_id, assigned_to, title, due_date, priority, status, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    g.user["id"],
                    request.form.get("assigned_to") or None,
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
            "blocks/tasks_form.html", item=None, priorities=PRIORITIES, statuses=STATUSES,
            users=_users(get_db()), form_data=None, error=None
        )

    @app.route("/app/tasks/<int:item_id>")
    def detail_task(item_id):
        db = get_db()
        item = db.execute(
            """
            SELECT t.*,
                   cu.name AS created_by_name, cu.email AS created_by_email,
                   au.name AS assigned_to_name, au.email AS assigned_to_email
            FROM tasks t
            LEFT JOIN users cu ON cu.id = t.user_id
            LEFT JOIN users au ON au.id = t.assigned_to
            WHERE t.id = ?
            """,
            (item_id,),
        ).fetchone()
        if item is None:
            abort(404)
        return render_template("blocks/tasks_detail.html", item=item)

    @app.route("/app/tasks/<int:item_id>/edit", methods=["GET", "POST"])
    @admin_required
    def edit_task(item_id):
        db = get_db()
        item = db.execute("SELECT * FROM tasks WHERE id = ?", (item_id,)).fetchone()
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
                    users=_users(db),
                    form_data=request.form,
                    error="Title is required.",
                ), 400
            db.execute(
                "UPDATE tasks SET title=?, assigned_to=?, due_date=?, priority=?, status=?, notes=?, "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                (
                    title,
                    request.form.get("assigned_to") or None,
                    request.form.get("due_date") or None,
                    request.form.get("priority", "Medium"),
                    request.form.get("status", "Open"),
                    request.form.get("notes", "").strip(),
                    item_id,
                ),
            )
            db.commit()
            return redirect(url_for("list_tasks"))
        return render_template(
            "blocks/tasks_form.html", item=item, priorities=PRIORITIES, statuses=STATUSES,
            users=_users(db), form_data=None, error=None
        )

    @app.route("/app/tasks/<int:item_id>/toggle", methods=["POST"])
    @admin_required
    def toggle_task(item_id):
        db = get_db()
        item = db.execute("SELECT id, status FROM tasks WHERE id = ?", (item_id,)).fetchone()
        if item is None:
            abort(404)
        new_status = "Open" if item["status"] in ("Done", "Cancelled") else "Done"
        db.execute(
            "UPDATE tasks SET status=?, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
            "WHERE id=?",
            (new_status, item_id),
        )
        db.commit()
        return redirect(request.referrer or url_for("dashboard"))

    @app.route("/app/tasks/<int:item_id>/delete", methods=["POST"])
    @admin_required
    def delete_task(item_id):
        db = get_db()
        item = db.execute("SELECT id FROM tasks WHERE id = ?", (item_id,)).fetchone()
        if item is None:
            abort(404)
        db.execute("DELETE FROM tasks WHERE id = ?", (item_id,))
        db.commit()
        return redirect(url_for("list_tasks"))
