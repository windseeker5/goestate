"""Flask CLI commands for database management.

Usage:
    flask init-db        Create all tables (safe to re-run — uses IF NOT EXISTS).
    flask verify-vec     Insert a test vector and run a KNN query to confirm
                         sqlite-vec is working end-to-end.
    flask reindex-all    Re-parse and re-embed every uploaded document. Run this
                         after changing EMBED_MODEL_ID.
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
                                        -- Active | Sold | Transferred | Pending
    sold_by         INTEGER REFERENCES users(id),  -- set automatically when status -> 'Sold'
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
-- Tasks (shared to-do list for the estate)
-- ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id),
                                        -- creator, for attribution only —
                                        -- every task is visible to all users
    assigned_to     INTEGER REFERENCES users(id),
                                        -- who's responsible for doing it;
                                        -- independent of the creator above
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
-- Embedding dimension: 384 (paraphrase-multilingual-MiniLM-L12-v2 via
-- fastembed — multilingual, since the estate corpus is French).

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

-- ──────────────────────────────────────────────────────────
-- Full-text search over the structured records (events, tasks)
-- ──────────────────────────────────────────────────────────
-- Events and tasks are small structured rows, so they are searched with
-- keywords rather than embeddings (see app/routes/chat.py). The naive
-- `LOWER(col) LIKE '%kw%'` this replaces had two fatal flaws: it matched
-- inside words (French "est" hit 10 of 14 events as a substring) and it had
-- no notion of relevance, so results were ordered by date and the one event
-- that actually matched a rare term got crowded out by recent noise.
--
-- FTS5 fixes both. It tokenises on word boundaries, and bm25() ranks rare
-- terms ("bénéficiaire") far above common ones ("est") automatically.
-- `remove_diacritics 2` is the Unicode-aware variant (1 is legacy/ASCII-only)
-- and is what lets an unaccented query — "beneficiaire", how people actually
-- type — match accented text.
--
-- These are external-content tables (content='events'): FTS5 stores only the
-- index and reads column values back from the source table, so there is no
-- duplicated copy of the notes. That makes the sync triggers below mandatory
-- rather than optional — without them the index silently drifts.

CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
    title, contact, notes, type,
    content='events',
    content_rowid='id',
    tokenize="unicode61 remove_diacritics 2"
);

CREATE TRIGGER IF NOT EXISTS events_fts_ai AFTER INSERT ON events BEGIN
    INSERT INTO events_fts(rowid, title, contact, notes, type)
    VALUES (new.id, new.title, new.contact, new.notes, new.type);
END;

-- The 'delete' command row must repeat the OLD column values verbatim so FTS5
-- can find and unpick the exact terms it indexed; passing the new values here
-- is the classic way to corrupt an external-content index.
CREATE TRIGGER IF NOT EXISTS events_fts_ad AFTER DELETE ON events BEGIN
    INSERT INTO events_fts(events_fts, rowid, title, contact, notes, type)
    VALUES ('delete', old.id, old.title, old.contact, old.notes, old.type);
END;

CREATE TRIGGER IF NOT EXISTS events_fts_au AFTER UPDATE ON events BEGIN
    INSERT INTO events_fts(events_fts, rowid, title, contact, notes, type)
    VALUES ('delete', old.id, old.title, old.contact, old.notes, old.type);
    INSERT INTO events_fts(rowid, title, contact, notes, type)
    VALUES (new.id, new.title, new.contact, new.notes, new.type);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS tasks_fts USING fts5(
    title, notes,
    content='tasks',
    content_rowid='id',
    tokenize="unicode61 remove_diacritics 2"
);

CREATE TRIGGER IF NOT EXISTS tasks_fts_ai AFTER INSERT ON tasks BEGIN
    INSERT INTO tasks_fts(rowid, title, notes) VALUES (new.id, new.title, new.notes);
END;

CREATE TRIGGER IF NOT EXISTS tasks_fts_ad AFTER DELETE ON tasks BEGIN
    INSERT INTO tasks_fts(tasks_fts, rowid, title, notes)
    VALUES ('delete', old.id, old.title, old.notes);
END;

CREATE TRIGGER IF NOT EXISTS tasks_fts_au AFTER UPDATE ON tasks BEGIN
    INSERT INTO tasks_fts(tasks_fts, rowid, title, notes)
    VALUES ('delete', old.id, old.title, old.notes);
    INSERT INTO tasks_fts(rowid, title, notes) VALUES (new.id, new.title, new.notes);
END;
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
            ("assets", "sold_by", "INTEGER REFERENCES users(id)"),
            ("liabilities", "photo_path", "TEXT"),
            ("liabilities", "photo_mime_type", "TEXT"),
            ("events", "email_direction", "TEXT"),
            ("tasks", "assigned_to", "INTEGER REFERENCES users(id)"),
        )
        for table, column, column_type in migrations:
            existing_columns = {
                row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if column not in existing_columns:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
                db.commit()
                click.echo(click.style(f"  [OK] Migrated: added {table}.{column} column.", fg="green"))

                if table == "tasks" and column == "assigned_to":
                    # Existing tasks had no assignee concept — keep them
                    # assigned to their creator rather than leaving every
                    # pre-existing task unassigned after this migration.
                    db.execute("UPDATE tasks SET assigned_to = user_id WHERE assigned_to IS NULL")
                    db.commit()
                    click.echo(click.style(
                        "  [OK] Backfilled tasks.assigned_to from tasks.user_id.", fg="green"
                    ))

        # Backfill the FTS indexes. The sync triggers only fire on rows written
        # after the triggers exist, so an existing database would otherwise have
        # empty indexes and chat would find no events or tasks at all.
        #
        # This rebuilds unconditionally rather than skipping when already
        # populated. There is no cheap way to ask an external-content FTS5 table
        # whether its index is empty — COUNT(*) reads through to the content
        # table and reports the source row count even when nothing is indexed —
        # and these tables are small enough that rebuilding is instant. It also
        # makes `init-db` the repair path if an index ever drifts out of sync.
        for fts_table, source in (("events_fts", "events"), ("tasks_fts", "tasks")):
            db.execute(f"INSERT INTO {fts_table}({fts_table}) VALUES('rebuild')")
            db.commit()
            indexed = db.execute(f"SELECT COUNT(*) FROM {source}").fetchone()[0]
            click.echo(click.style(
                f"  [OK] Built {fts_table} search index ({indexed} rows).", fg="green"
            ))

        click.echo(click.style("  [OK] Database initialised.", fg="green"))
        click.echo(click.style("       Tables: users, assets, liabilities, events, tasks, documents,", dim=True))
        click.echo(click.style("               settings, doc_chunks, doc_chunk_meta,", dim=True))
        click.echo(click.style("               events_fts, tasks_fts", dim=True))

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


@click.command("reindex-all")
@with_appcontext
def reindex_all_command():
    """Re-parse and re-embed every uploaded document.

    Needed after changing EMBED_MODEL_ID: the stored vectors keep the same 384
    dimensions, so nothing errors, but they belong to the old model's vector
    space and comparing them against new queries silently returns nonsense.
    The per-document reindex button on /app/documents only handles one file at
    a time, which is impractical for a whole corpus.
    """
    from app.document_storage import documents_uploads_dir
    from app.ingestion import EMBED_MODEL_ID, ingest_document

    db = open_db()
    try:
        docs = db.execute(
            "SELECT id, filename, filepath, linked_entity_type, linked_entity_id "
            "FROM documents ORDER BY id"
        ).fetchall()
        if not docs:
            click.echo(click.style("  [!!] No documents to reindex.", fg="yellow"))
            return

        click.echo(click.style(f"  Embedding model: {EMBED_MODEL_ID}", dim=True))
        click.echo(click.style(
            "  First run downloads the ONNX model (a few hundred MB) — this can take a while.",
            dim=True,
        ))

        uploads_dir = documents_uploads_dir()
        succeeded = failed = missing = 0

        for doc in docs:
            path = os.path.join(uploads_dir, doc["filepath"])
            if not os.path.exists(path):
                db.execute(
                    "UPDATE documents SET ingestion_status = 'error', ingestion_error = ? WHERE id = ?",
                    ("Original file no longer exists on disk.", doc["id"]),
                )
                db.commit()
                missing += 1
                click.echo(click.style(f"  [!!] {doc['filename']} — file missing on disk", fg="yellow"))
                continue

            click.echo(f"  ... {doc['filename']}")
            # Pass the existing linkage through — ingest_document() rebuilds
            # every doc_chunk_meta row for this document from scratch, so
            # omitting these would drop the asset/liability attribution.
            ingest_document(
                db,
                doc["id"],
                path,
                linked_entity_type=doc["linked_entity_type"],
                linked_entity_id=doc["linked_entity_id"],
            )
            row = db.execute(
                "SELECT ingestion_status, ingestion_error FROM documents WHERE id = ?", (doc["id"],)
            ).fetchone()
            # ingest_document() swallows exceptions and records them on the row,
            # so the status column is the only way to tell success from failure.
            if row["ingestion_status"] == "embedded":
                succeeded += 1
                click.echo(click.style(f"  [OK] {doc['filename']}", fg="green"))
            else:
                failed += 1
                click.echo(click.style(
                    f"  [!!] {doc['filename']} — {row['ingestion_error'] or 'unknown error'}", fg="red"
                ))

        chunk_count = db.execute("SELECT COUNT(*) FROM doc_chunk_meta").fetchone()[0]
        click.echo(click.style(
            f"  Done: {succeeded} embedded, {failed} failed, {missing} missing "
            f"— {chunk_count} chunks total.",
            fg="green" if not (failed or missing) else "yellow",
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
