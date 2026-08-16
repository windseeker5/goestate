"""Flask CLI commands for database management.

Usage:
    flask init-db        Create all tables (safe to re-run — uses IF NOT EXISTS).
    flask verify-vec     Insert a test vector and run a KNN query to confirm
                         sqlite-vec is working end-to-end.
"""

import os

import click
from flask.cli import with_appcontext
from werkzeug.security import generate_password_hash

from app.db import open_db


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
-- ──────────────────────────────────────────────────────────
-- Users (email + password login, simple role-based access)
-- ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT    NOT NULL UNIQUE,
    password_hash   TEXT    NOT NULL,
    role            TEXT    NOT NULL DEFAULT 'viewer',
                                        -- admin | viewer
    name            TEXT,               -- display name, e.g. "Ken Dresdell". Optional.
    created_at      TEXT    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ──────────────────────────────────────────────────────────
-- Core ledger tables
-- ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS assets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    category        TEXT,               -- e.g. "Real Estate", "Vehicle", "Bank Account"
    estimated_value REAL    DEFAULT 0,
    sale_price      REAL,               -- NULL until sold
    status          TEXT    DEFAULT 'Active',
                                        -- Active | Sold | Distributed | Pending
    notes           TEXT,
    photo_path      TEXT,               -- relative to instance/uploads/ledger-photos/
    photo_mime_type TEXT,
    created_at      TEXT    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS liabilities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    creditor        TEXT    NOT NULL,   -- who is owed
    description     TEXT,
    amount          REAL    DEFAULT 0,
    due_date        TEXT,               -- ISO date string YYYY-MM-DD
    status          TEXT    DEFAULT 'Unpaid',
                                        -- Unpaid | Paid | Disputed | Cancelled
    notes           TEXT,
    photo_path      TEXT,               -- relative to instance/uploads/ledger-photos/
    photo_mime_type TEXT,
    created_at      TEXT    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT    NOT NULL,
    contact         TEXT,               -- name of person/org contacted
    type            TEXT    DEFAULT 'Note',
                                        -- Note | Call | Meeting | Email | Task
    email_direction TEXT,               -- inbound | outbound; only applies to Email
    event_date      TEXT,               -- ISO date string YYYY-MM-DD
    notes           TEXT,
    created_at      TEXT    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ──────────────────────────────────────────────────────────
-- Tasks (personal to-do list, scoped to the owning user)
-- ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id),
                                        -- owner; a task is only ever visible
                                        -- to this user, regardless of role
    title           TEXT    NOT NULL,
    due_date        TEXT,               -- ISO date string YYYY-MM-DD
    priority        TEXT    DEFAULT 'Medium',
                                        -- Low | Medium | High
    status          TEXT    DEFAULT 'Open',
                                        -- Open | In Progress | Done | Cancelled
    notes           TEXT,
    created_at      TEXT    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ──────────────────────────────────────────────────────────
-- Document store (uploaded PDFs / files)
-- ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS documents (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    filename            TEXT    NOT NULL,
    filepath            TEXT    NOT NULL,   -- relative to instance/uploads/
    linked_entity_type  TEXT,               -- 'asset' | 'liability' | 'event' | NULL
    linked_entity_id    INTEGER,
    ingestion_status    TEXT    DEFAULT 'pending',
                                            -- pending | parsed | embedded | error
    ingestion_error     TEXT,               -- error message if status = 'error'
    created_at          TEXT    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ──────────────────────────────────────────────────────────
-- Settings (key-value store for LLM config, etc.)
-- ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS settings (
    key     TEXT PRIMARY KEY,
    value   TEXT
);

-- Seed default LLM settings (only if they don't exist yet).
INSERT OR IGNORE INTO settings (key, value) VALUES
    ('llm_api_base_url', ''),
    ('llm_api_key',      ''),
    ('llm_model_name',   '');

-- ──────────────────────────────────────────────────────────
-- Vector search tables (RAG pipeline)
-- ──────────────────────────────────────────────────────────
-- sqlite-vec stores vectors in a virtual table (vec0).
-- Metadata is kept in a paired regular table joined by rowid.
-- Embedding dimension: 384 (BAAI/bge-small-en-v1.5 via fastembed).

CREATE VIRTUAL TABLE IF NOT EXISTS doc_chunks USING vec0(
    embedding float[384]
);

CREATE TABLE IF NOT EXISTS doc_chunk_meta (
    chunk_id            INTEGER PRIMARY KEY,   -- matches doc_chunks rowid
    document_id         INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    linked_entity_type  TEXT,
    linked_entity_id    INTEGER,
    chunk_text          TEXT    NOT NULL,
    chunk_index         INTEGER DEFAULT 0      -- position within the document
);
"""


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@click.command("init-db")
@with_appcontext
def init_db_command():
    """Create all database tables. Safe to run multiple times (IF NOT EXISTS)."""
    db = open_db()
    try:
        db.executescript(SCHEMA_SQL)
        db.commit()

        # Lightweight migrations for existing databases. CREATE TABLE IF NOT
        # EXISTS above only supplies new columns when creating a fresh DB.
        migrations = (
            ("users", "name", "TEXT"),
            ("assets", "photo_path", "TEXT"),
            ("assets", "photo_mime_type", "TEXT"),
            ("liabilities", "photo_path", "TEXT"),
            ("liabilities", "photo_mime_type", "TEXT"),
            ("events", "email_direction", "TEXT"),
        )
        for table, column, column_type in migrations:
            existing_columns = {
                row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if column not in existing_columns:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
                db.commit()
                click.echo(click.style(f"  [OK] Migrated: added {table}.{column} column.", fg="green"))

        click.echo(click.style("  [OK] Database initialised.", fg="green"))
        click.echo(click.style("       Tables: users, assets, liabilities, events, tasks, documents,", dim=True))
        click.echo(click.style("               settings, doc_chunks, doc_chunk_meta", dim=True))

        # Print sqlite-vec version as confirmation the extension loaded.
        vec_version = db.execute("SELECT vec_version()").fetchone()[0]
        click.echo(click.style(f"  [OK] sqlite-vec loaded — version {vec_version}", fg="green"))

        # Bootstrap the first admin user from .env, if no users exist yet.
        # ADMIN_EMAIL / LIQUIDATOR_PASSWORD come from .env (see .env.example).
        user_count = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if user_count == 0:
            admin_email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
            admin_password = os.environ.get("LIQUIDATOR_PASSWORD", "")
            if admin_email and admin_password:
                db.execute(
                    "INSERT INTO users (email, password_hash, role) VALUES (?, ?, 'admin')",
                    (admin_email, generate_password_hash(admin_password)),
                )
                db.commit()
                click.echo(click.style(f"  [OK] Bootstrapped admin user: {admin_email}", fg="green"))
            else:
                click.echo(click.style(
                    "  [!!] No users exist yet, and ADMIN_EMAIL / LIQUIDATOR_PASSWORD are not "
                    "both set in .env — skipping admin bootstrap. Set both and re-run `flask init-db`.",
                    fg="yellow",
                ))
    finally:
        db.close()


@click.command("verify-vec")
@with_appcontext
def verify_vec_command():
    """Insert a test vector into doc_chunks and run a KNN query to verify sqlite-vec."""
    import json
    import struct

    db = open_db()
    try:
        # Insert 3 test vectors (384 floats each, all zeros with one position set).
        def make_vec(hot_index):
            v = [0.0] * 384
            v[hot_index] = 1.0
            return struct.pack(f"{384}f", *v)

        click.echo("  Inserting 3 test vectors into doc_chunks...")
        db.execute("DELETE FROM doc_chunks WHERE rowid IN (9990, 9991, 9992)")
        db.execute("INSERT INTO doc_chunks(rowid, embedding) VALUES (9990, ?)", [make_vec(0)])
        db.execute("INSERT INTO doc_chunks(rowid, embedding) VALUES (9991, ?)", [make_vec(100)])
        db.execute("INSERT INTO doc_chunks(rowid, embedding) VALUES (9992, ?)", [make_vec(200)])
        db.commit()

        # Query: nearest neighbour to vec hot at index 100 → should return rowid 9991 first.
        query_vec = make_vec(100)
        results = db.execute(
            """
            SELECT rowid, distance
            FROM doc_chunks
            WHERE embedding MATCH ?
              AND k = 3
            ORDER BY distance
            """,
            [query_vec],
        ).fetchall()

        click.echo(click.style("  [OK] KNN query returned:", fg="green"))
        for row in results:
            click.echo(f"       rowid={row[0]}  distance={row[1]:.6f}")

        top_rowid = results[0][0]
        if top_rowid == 9991:
            click.echo(click.style("  [OK] Correct nearest neighbour (rowid 9991). sqlite-vec works.", fg="green"))
        else:
            click.echo(click.style(f"  [!!] Expected rowid 9991 as nearest, got {top_rowid}.", fg="yellow"))

        # Clean up test rows.
        db.execute("DELETE FROM doc_chunks WHERE rowid IN (9990, 9991, 9992)")
        db.commit()
        click.echo("  Test vectors cleaned up.")
    finally:
        db.close()
