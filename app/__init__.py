import os

from flask import Flask, redirect, request, url_for

from app.config import DevConfig


def create_app(config_object=DevConfig):
    app = Flask(__name__)
    app.config.from_object(config_object)

    @app.template_global()
    def asset_version(static_relative_path):
        """Return a static file's last-modified time, for cache-busting.

        Used as ?v={{ asset_version('css/output.css') }} on <link>/<script>
        tags in base.html so browsers fetch a fresh copy whenever the file
        actually changes, instead of serving a stale cached version
        indefinitely (which otherwise looks like a JS/CSS edit "didn't
        work" for a browser that already visited the app once before).
        """
        full_path = os.path.join(app.static_folder, static_relative_path)
        try:
            return int(os.path.getmtime(full_path))
        except OSError:
            return 0

    from app.auth import is_logged_in, load_logged_in_user

    @app.before_request
    def _load_user():
        # Populate g.user on every request so templates/routes can check
        # g.user and g.user.role without each route querying it themselves.
        load_logged_in_user()

    @app.before_request
    def _require_login():
        # Every authenticated screen lives under /app.
        # Everything else (/, /login, static files) stays public.
        if request.path.startswith("/app"):
            if not is_logged_in():
                return redirect(url_for("login"))

    # Plain functions, one file per domain. No blueprints — this is a
    # single-user local app, so the extra indirection isn't worth it.
    from app.routes import public, dashboard, assets, liabilities, events, documents, chat, users

    for module in (public, dashboard, assets, liabilities, events, documents, chat, users):
        module.register(app)

    from app.commands import init_db_command, verify_vec_command
    app.cli.add_command(init_db_command)
    app.cli.add_command(verify_vec_command)

    from app.db import init_app as init_db_app
    init_db_app(app)

    return app
