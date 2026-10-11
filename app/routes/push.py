import os

from flask import current_app, g, jsonify, request, send_from_directory

from app.auth import login_required
from app.db import get_db


def register(app):
    @app.route("/sw.js")
    def service_worker():
        # Served at the root path (not /static/sw.js) so its default scope
        # covers the whole app ("/") rather than just /static/ — a push
        # subscription made against a narrower scope couldn't show
        # notifications tied to page URLs under /app/.
        response = send_from_directory(
            os.path.join(current_app.root_path, "static"), "sw.js", mimetype="application/javascript"
        )
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.route("/app/push/subscribe", methods=["POST"])
    @login_required
    def push_subscribe():
        data = request.get_json(silent=True) or {}
        endpoint = data.get("endpoint")
        keys = data.get("keys") or {}
        p256dh = keys.get("p256dh")
        auth = keys.get("auth")
        if not (endpoint and p256dh and auth):
            return jsonify({"error": "Invalid subscription."}), 400

        db = get_db()
        db.execute(
            "INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(endpoint) DO UPDATE SET user_id = excluded.user_id, "
            "p256dh = excluded.p256dh, auth = excluded.auth",
            (g.user["id"], endpoint, p256dh, auth),
        )
        db.commit()
        return jsonify({"ok": True})

    @app.route("/app/push/unsubscribe", methods=["POST"])
    @login_required
    def push_unsubscribe():
        data = request.get_json(silent=True) or {}
        endpoint = data.get("endpoint")
        if endpoint:
            db = get_db()
            db.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
            db.commit()
        return jsonify({"ok": True})
