"""Simple email + password session auth with two roles.

Roles:
    admin  — full access (create/edit/delete everywhere, manage users)
    viewer — read-only: can browse assets/liabilities/timeline/documents
             and use chat, but cannot create/edit/delete anything

The first admin user is bootstrapped by `flask init-db` from ADMIN_EMAIL /
LIQUIDATOR_PASSWORD in .env (see app/commands.py). After that, admins manage
users from the app at /app/users — no CLI needed to add more people.

Passwords are hashed with werkzeug's generate_password_hash/check_password_hash
(bundled with Flask, no extra dependency).
"""

from functools import wraps

from flask import abort, g, redirect, session, url_for
from werkzeug.security import check_password_hash

from app.db import get_db

SESSION_KEY = "user_id"


def load_logged_in_user():
    """Load the current user (if any) into g.user. Call once per request."""
    user_id = session.get(SESSION_KEY)
    if user_id is None:
        g.user = None
    else:
        db = get_db()
        g.user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def is_logged_in():
    return g.get("user") is not None


def is_admin():
    user = g.get("user")
    return user is not None and user["role"] == "admin"


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_logged_in():
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    """Like login_required, but also requires the admin role (403 for viewers)."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_logged_in():
            return redirect(url_for("login"))
        if not is_admin():
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def check_credentials(email, password):
    """Return the user row if email/password match, else None."""
    db = get_db()
    user = db.execute(
        "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)
    ).fetchone()
    if user is None:
        return None
    if not check_password_hash(user["password_hash"], password):
        return None
    return user
