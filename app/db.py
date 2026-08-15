"""Database connection helper for Estate Copilot.

Usage inside a Flask request context:
    from app.db import get_db
    db = get_db()
    rows = db.execute("SELECT * FROM assets").fetchall()

The connection is opened once per request and closed automatically.

Outside a request context (e.g. CLI commands), use open_db() directly:
    from app.db import open_db
    with open_db() as db:
        db.execute("SELECT 1")
"""

import os
import sqlite3

import sqlite_vec
from flask import current_app, g


def _db_path():
    return current_app.config["DATABASE_PATH"]


def open_db(path=None):
    """Open a new SQLite connection with sqlite-vec loaded.

    If path is None, uses the app config DATABASE_PATH.
    Always returns a connection with row_factory set to sqlite3.Row.
    """
    if path is None:
        path = _db_path()

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    # Enable WAL mode for better concurrent read performance.
    conn.execute("PRAGMA journal_mode=WAL")

    # Load sqlite-vec extension.
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    return conn


def get_db():
    """Return the per-request database connection, opening one if needed."""
    if "db" not in g:
        g.db = open_db()
    return g.db


def close_db(e=None):
    """Close the per-request database connection (registered as teardown)."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_app(app):
    """Register db teardown with the Flask app."""
    app.teardown_appcontext(close_db)
