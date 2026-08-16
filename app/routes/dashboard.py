from datetime import date

from flask import g, redirect, render_template, request, url_for

from app.auth import admin_required
from app.db import get_db


def register(app):
    @app.route("/app/dashboard")
    def dashboard():
        db = get_db()
        asset_count = db.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        asset_total = db.execute(
            "SELECT COALESCE(SUM(estimated_value), 0) FROM assets"
        ).fetchone()[0]
        liability_count = db.execute(
            "SELECT COUNT(*) FROM liabilities WHERE status != 'Paid'"
        ).fetchone()[0]
        liability_total = db.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM liabilities WHERE status != 'Paid'"
        ).fetchone()[0]
        event_count = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        document_count = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

        open_task_count = db.execute(
            "SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status NOT IN ('Done', 'Cancelled')",
            (g.user["id"],),
        ).fetchone()[0]
        upcoming_tasks = db.execute(
            "SELECT * FROM tasks WHERE user_id = ? AND status NOT IN ('Done', 'Cancelled') "
            "ORDER BY (due_date IS NULL), due_date ASC LIMIT 5",
            (g.user["id"],),
        ).fetchall()

        # Sort by event_date DESC so the most recently *dated* entry is
        # always at the top — a call logged for Aug 14 should appear above
        # one logged for Aug 10, regardless of which was entered first.
        # Falls back to created_at DESC for entries without a date so new
        # backdated entries don't silently disappear from the top.
        recent_events = db.execute(
            "SELECT * FROM events ORDER BY COALESCE(event_date, created_at) DESC LIMIT 10"
        ).fetchall()

        display_name = ""
        if g.user:
            display_name = g.user["name"] or g.user["email"].split("@")[0]

        return render_template(
            "blocks/dashboard.html",
            asset_count=asset_count,
            # Raw floats — the template applies the |money / |money_compact
            # filters, so display rules live in one place (app/formatting.py)
            # rather than being split between here and the list templates.
            asset_total=asset_total,
            liability_count=liability_count,
            liability_total=liability_total,
            event_count=event_count,
            document_count=document_count,
            recent_events=recent_events,
            display_name=display_name,
            open_task_count=open_task_count,
            upcoming_tasks=upcoming_tasks,
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

        rows = db.execute(
            "SELECT key, value FROM settings WHERE key IN "
            "('llm_api_base_url', 'llm_api_key', 'llm_model_name')"
        ).fetchall()
        current = {row["key"]: row["value"] for row in rows}

        return render_template("blocks/settings_page.html", settings=current, saved=saved)
