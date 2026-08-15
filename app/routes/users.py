from flask import abort, g, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from app.auth import admin_required
from app.db import get_db

ROLES = ["admin", "viewer"]


def register(app):
    @app.route("/app/users")
    @admin_required
    def list_users():
        db = get_db()
        rows = db.execute("SELECT * FROM users ORDER BY created_at ASC").fetchall()
        return render_template("blocks/users_list.html", users=rows)

    @app.route("/app/users/new", methods=["GET", "POST"])
    @admin_required
    def new_user():
        error = None
        if request.method == "POST":
            db = get_db()
            email = request.form.get("email", "").strip().lower()
            name = request.form.get("name", "").strip()
            password = request.form.get("password", "")
            role = request.form.get("role", "viewer")

            if not email or not password:
                error = "Email and password are required."
            elif role not in ROLES:
                error = "Invalid role."
            else:
                existing = db.execute(
                    "SELECT id FROM users WHERE email = ?", (email,)
                ).fetchone()
                if existing is not None:
                    error = f"A user with email {email} already exists."
                else:
                    db.execute(
                        "INSERT INTO users (email, name, password_hash, role) VALUES (?, ?, ?, ?)",
                        (email, name or None, generate_password_hash(password), role),
                    )
                    db.commit()
                    return redirect(url_for("list_users"))

        return render_template("blocks/users_form.html", item=None, roles=ROLES, error=error)

    @app.route("/app/users/<int:user_id>/edit", methods=["GET", "POST"])
    @admin_required
    def edit_user(user_id):
        db = get_db()
        item = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if item is None:
            abort(404)

        error = None
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            role = request.form.get("role", "viewer")
            password = request.form.get("password", "")

            if role not in ROLES:
                error = "Invalid role."
            elif role != "admin" and item["id"] == g.user["id"]:
                # Prevent an admin from demoting themselves and getting
                # locked out — keep this dead simple, no "last admin" query.
                error = "You cannot remove your own admin access."
            else:
                if password:
                    db.execute(
                        "UPDATE users SET name = ?, role = ?, password_hash = ? WHERE id = ?",
                        (name or None, role, generate_password_hash(password), user_id),
                    )
                else:
                    db.execute(
                        "UPDATE users SET name = ?, role = ? WHERE id = ?", (name or None, role, user_id)
                    )
                db.commit()
                return redirect(url_for("list_users"))

        return render_template("blocks/users_form.html", item=item, roles=ROLES, error=error)

    @app.route("/app/users/<int:user_id>/delete", methods=["POST"])
    @admin_required
    def delete_user(user_id):
        if user_id == g.user["id"]:
            abort(400)
        db = get_db()
        db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        db.commit()
        return redirect(url_for("list_users"))
