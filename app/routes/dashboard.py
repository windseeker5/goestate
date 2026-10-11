from datetime import date

from flask import g, redirect, render_template, request, url_for

from app import money
from app.auth import admin_required
from app.db import get_db
from app.document_storage import reindex_all_documents


def register(app):
    @app.route("/app/dashboard")
    def dashboard():
        db = get_db()
        asset_count = db.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        liability_count = db.execute(
            "SELECT COUNT(*) FROM liabilities WHERE status NOT IN ('Paid', 'Cancelled')"
        ).fetchone()[0]
        net = money.net_estate(db)
        event_count = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        document_count = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

        # Cash the current user holds for the estate (sales collected minus
        # deposits and bills paid with it) — see app/money.py.
        held_cents = money.held_balance(db, g.user["id"])
        bank_cents = money.bank_balance(db)
        db.commit()  # bank_balance() creates the bank account row on first use
        users = db.execute("SELECT id, name, email FROM users ORDER BY name").fetchall()

        open_task_count = db.execute(
            "SELECT COUNT(*) FROM tasks WHERE status NOT IN ('Done', 'Cancelled')"
        ).fetchone()[0]

        # "Mine" is a display-only toggle on the Upcoming Tasks widget, not a
        # visibility boundary — the dashboard stays estate-wide by default.
        my_tasks = request.args.get("my_tasks") == "1"
        upcoming_tasks = db.execute(
            "SELECT t.*, u.name AS assigned_to_name, u.email AS assigned_to_email "
            "FROM tasks t LEFT JOIN users u ON u.id = t.assigned_to "
            "WHERE t.status NOT IN ('Done', 'Cancelled') "
            + ("AND t.assigned_to = ? " if my_tasks else "")
            + "ORDER BY (t.due_date IS NULL), t.due_date ASC LIMIT 5",
            (g.user["id"],) if my_tasks else (),
        ).fetchall()

        # Sort by event_date DESC so the most recently *dated* entry is
        # always at the top — a call logged for Aug 14 should appear above
        # one logged for Aug 10, regardless of which was entered first.
        # Falls back to created_at DESC for entries without a date so new
        # backdated entries don't silently disappear from the top.
        recent_events = db.execute(
            "SELECT * FROM events ORDER BY COALESCE(event_date, created_at) DESC LIMIT 6"
        ).fetchall()

        display_name = ""
        if g.user:
            display_name = g.user["name"] or g.user["email"].split("@")[0]

        return render_template(
            "blocks/dashboard.html",
            asset_count=asset_count,
            liability_count=liability_count,
            event_count=event_count,
            document_count=document_count,
            held_cents=held_cents,
            bank_cents=bank_cents,
            net=net,
            users=users,
            recent_events=recent_events,
            display_name=display_name,
            open_task_count=open_task_count,
            upcoming_tasks=upcoming_tasks,
            my_tasks=my_tasks,
            today=date.today().isoformat(),
        )

    @app.route("/app/settings", methods=["GET", "POST"])
    @admin_required
    def settings():
        db = get_db()
        saved = False

        if request.method == "POST":
            base_url = request.form.get("llm_api_base_url", "").strip()
            api_key = request.form.get("llm_api_key", "").strip()
            model_name = request.form.get("llm_model_name", "").strip()

            for key, value in (
                ("llm_api_base_url", base_url),
                ("llm_api_key", api_key),
                ("llm_model_name", model_name),
            ):
                db.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )
            db.commit()
            saved = True
            return redirect(url_for("settings", saved=1))

        saved = request.args.get("saved") == "1"
        reindexed = request.args.get("reindexed") == "1"
        restored = request.args.get("restored")
        restore_error = request.args.get("restore_error")
        reindex_stats = {
            key: request.args.get(key, type=int) or 0
            for key in ("total", "succeeded", "failed", "missing", "chunks")
        }

        rows = db.execute(
            "SELECT key, value FROM settings WHERE key IN "
            "('llm_api_base_url', 'llm_api_key', 'llm_model_name')"
        ).fetchall()
        current = {row["key"]: row["value"] for row in rows}

        return render_template(
            "blocks/settings_page.html",
            settings=current,
            saved=saved,
            reindexed=reindexed,
            reindex_stats=reindex_stats,
            restored=restored,
            restore_error=restore_error,
        )

    @app.route("/app/settings/reindex", methods=["POST"])
    @admin_required
    def reindex_documents():
        stats = reindex_all_documents(get_db())
        return redirect(url_for("settings", reindexed=1, **stats))
