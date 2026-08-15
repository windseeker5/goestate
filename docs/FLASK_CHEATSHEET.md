# Flask Best Practices Cheat Sheet

A personal reference for how — and why — this project (Estate Copilot) is
structured the way it is. Everything here is grounded in Flask's own
official documentation, not just opinion. Code examples are taken directly
from this codebase.

If you've mostly written Flask apps as one big `app.py` with every route
attached to a single global `app` object, this is your bridge to the more
scalable pattern Flask itself recommends once a project grows past a
handful of routes.

---

## 1. The Application Factory Pattern

### What it is

Instead of creating the Flask `app` object at import time (`app = Flask(__name__)`
at the top of a file), you wrap that creation inside a function —
conventionally called `create_app()` — that builds and returns the app.

**Confirmed straight from Flask's official tutorial:**
> *"The `__init__.py` file contains the `create_app` factory function, which
> handles configuration, instance folder creation, and route registration."*
> — [flask.palletsprojects.com/tutorial/factory](https://flask.palletsprojects.com/en/stable/tutorial/factory/)

### Before (what you're used to)

```python
# app.py
from flask import Flask

app = Flask(__name__)

@app.route("/")
def index():
    return "Hello"

if __name__ == "__main__":
    app.run(debug=True)
```

### After (this project)

```python
# app/__init__.py
from flask import Flask
from app.config import DevConfig

def create_app(config_object=DevConfig):
    app = Flask(__name__)
    app.config.from_object(config_object)
    # ... register routes, db, etc. (see Section 2) ...
    return app
```

```python
# app.py  (project root — your day-to-day entry point)
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=app.config["PORT"])
```

### Why bother?

- **Different configs for different situations.** `create_app(DevConfig)` vs.
  `create_app(ProdConfig)` vs. `create_app(TestConfig)` — same app code,
  different settings, without duplicating anything.
- **Testability.** You can call `create_app()` fresh inside each test to get
  a clean app instance, instead of one global `app` shared across every test.
- **No import-order headaches.** Nothing tries to use `app` before it fully
  exists, because nothing outside `create_app()` ever references it directly.

### How it looks in this project

| File | Role |
|---|---|
| `app/__init__.py` | Defines `create_app()`. The one place the `Flask()` object gets built. |
| `app.py` | Day-to-day dev entry point. `python app.py` → calls `create_app()`, runs the dev server. |
| `wsgi.py` | Same idea, but for `flask --app wsgi <command>` CLI usage (`init-db`, `verify-vec`) and production WSGI servers. |

---

## 2. Organizing Routes Across Files

### Flask's own recommended structure

From Flask's official tutorial project layout:

```text
flaskr/
├── __init__.py      ← create_app()
├── db.py
├── auth.py          ← login/register routes
├── blog.py          ← blog post routes
├── templates/
└── static/
```

Confirmed directly from Flask's docs:
> *"While a Flask application can be contained in a single file, larger
> projects benefit from using Python packages to organize code into
> multiple modules. This approach improves maintainability and
> scalability as the application grows."*

`auth.py` and `blog.py` are literally the same idea as this project's
`app/routes/assets.py`, `app/routes/liabilities.py`, `app/routes/chat.py`,
etc. — one file per feature area.

### How this project does it

Flask's official tutorial uses **Blueprints** for the multi-file split.
This project uses a **simpler, hand-rolled equivalent** instead, on
purpose — see the comparison in Section 3.

Real example from `app/routes/dashboard.py`:

```python
from flask import redirect, render_template, request, url_for
from app.db import get_db

def register(app):
    @app.route("/app/dashboard")
    def dashboard():
        db = get_db()
        asset_count = db.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        return render_template("blocks/dashboard.html", asset_count=asset_count)

    @app.route("/app/settings", methods=["GET", "POST"])
    def settings():
        ...
```

Notice: it's the **exact same** `@app.route(...)` decorator and view
function style you already know. The only difference is that the routes
live inside a `register(app)` function instead of at module level, because
at the time this file is *written*, the `app` object doesn't exist yet —
it only exists once `create_app()` runs.

Then in `app/__init__.py`, once `app` is built, every route module gets
"switched on" by calling its `register(app)`:

```python
from app.routes import public, dashboard, assets, liabilities, events, documents, chat, gallery

for module in (public, dashboard, assets, liabilities, events, documents, chat, gallery):
    module.register(app)
```

### Adding a new page — step by step

1. Create `app/routes/my_thing.py`:
   ```python
   from flask import render_template

   def register(app):
       @app.route("/app/my-thing")
       def my_thing():
           return render_template("blocks/my_thing.html")
   ```
2. Add `my_thing` to the import + tuple in `app/__init__.py`.
3. Create `app/templates/blocks/my_thing.html`.
4. Add a sidebar link in `app/templates/layouts/app.html`.
5. (Optional) Document it in `app/templates/gallery/blocks.html`.

---

## 3. This Pattern vs. Official Flask Blueprints

If you look at Flask tutorials, courses, or most real-world codebases,
you'll usually see **Blueprints** for multi-file routing, not the
`register(app)` style used here. It's worth knowing the difference so
nothing looks unfamiliar if you read Flask docs elsewhere.

| | This project (`register(app)`) | Official Flask Blueprint |
|---|---|---|
| Declare routes in a separate file | `def register(app): @app.route(...)` | `bp = Blueprint("name", __name__)` then `@bp.route(...)` |
| Wire it into the app | `module.register(app)` | `app.register_blueprint(bp)` |
| `url_for()` naming | Flat: `url_for("list_assets")` | Namespaced: `url_for("assets.list_assets")` |
| URL prefixing | Manual (write the full path in each `@app.route`) | Built-in (`Blueprint("assets", __name__, url_prefix="/app/assets")`) |
| Per-group `before_request` hooks | Not built-in (this project uses one global hook in `create_app()`) | `@bp.before_request` |
| Best for | Small, single-developer, single-purpose apps | Larger apps, multi-team codebases, reusable packages, apps needing URL namespacing |

**Why this project skips Blueprints:** it's a single-user, local-only app.
Blueprint's extra naming (`"assets.list_assets"`) and prefixing machinery
add a layer of indirection that isn't worth it here — see the comment in
`app/__init__.py`:

```python
# Plain functions, one file per domain. No blueprints — this is a
# single-user local app, so the extra indirection isn't worth it.
```

**When you'd want real Blueprints instead:** if this app grows into
multiple teams working on different sections, needs true URL namespacing
(e.g. an `/api/v1` blueprint separate from the web UI), or you want to
package up a feature (like the chat module) to reuse in another Flask app.

---

## 4. Config & Secrets

### The pattern

- Secrets and environment-specific values (passwords, API keys, DB paths,
  ports) live in a `.env` file — **never committed to git** (`.gitignore`
  covers it).
- `.env.example` is committed, documenting every variable with a
  placeholder value, so anyone cloning the repo knows what to set.
- `python-dotenv`'s `load_dotenv()` reads `.env` into `os.environ`.
- A `Config` class (and subclasses like `DevConfig`/`ProdConfig`) reads
  from `os.environ` with sensible defaults, and gets passed to
  `app.config.from_object(...)`.

### Real example — `app/config.py`

```python
import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    LIQUIDATOR_PASSWORD = os.environ.get("LIQUIDATOR_PASSWORD")
    DATABASE_PATH = os.environ.get("DATABASE_PATH", "instance/estate.db")
    PORT = int(os.environ.get("FLASK_RUN_PORT", 5000))

class DevConfig(Config):
    DEBUG = True

class ProdConfig(Config):
    DEBUG = False
```

Anywhere in the app, config values are read via `current_app.config["KEY"]`
or, inside `create_app()`, directly via `app.config["KEY"]`.

### Why this matters

- Changing the dev server port, the database path, or the admin password
  never requires touching code — just `.env`.
- Different environments (dev vs. prod) can use different config classes
  without duplicating logic.

---

## 5. Database Pattern — No ORM, Flask's `g` Object

This project uses plain `sqlite3` (Python's standard library), not an ORM
like SQLAlchemy. That's a deliberate simplicity choice for this project,
but the **connection lifecycle pattern** (open once per request, close on
teardown) is straight out of Flask's own tutorial.

### Real example — `app/db.py`

```python
from flask import current_app, g
import sqlite3

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
    app.teardown_appcontext(close_db)
```

`g` is Flask's built-in per-request storage object — it lives only for the
duration of one request, then gets thrown away. Using it for the DB
connection means:

- Every route just calls `get_db()` and gets the *same* connection if
  called multiple times in one request (no wasted re-connects).
- The connection always gets cleanly closed at the end of the request via
  `teardown_appcontext`, even if the route raised an error.

### Usage in a route

```python
from app.db import get_db

def register(app):
    @app.route("/app/assets")
    def list_assets():
        db = get_db()
        rows = db.execute("SELECT * FROM assets").fetchall()
        return render_template("blocks/assets_list.html", assets=rows)
```

---

## 6. CLI Commands

Flask lets you register custom commands runnable via `flask --app <module> <command>`.
This project uses it for database setup instead of a separate script.

### Real example — `app/commands.py`

```python
import click
from flask.cli import with_appcontext
from app.db import open_db

@click.command("init-db")
@with_appcontext
def init_db_command():
    """Create all database tables. Safe to run multiple times."""
    db = open_db()
    db.executescript(SCHEMA_SQL)
    db.commit()
```

Registered in `create_app()`:

```python
from app.commands import init_db_command, verify_vec_command
app.cli.add_command(init_db_command)
app.cli.add_command(verify_vec_command)
```

Run with:

```bash
flask --app wsgi init-db
flask --app wsgi verify-vec
```

`wsgi.py` (not `app.py`) is used here because Flask's CLI needs a module
that exposes something it can import to find `create_app()` — it doesn't
actually start the dev server for these commands.

---

## 7. Project Structure Reference

```text
Liquidator/
├── app.py                 ← python app.py (day-to-day dev server)
├── wsgi.py                ← flask --app wsgi <command> (CLI + prod WSGI)
├── requirements.txt
├── .env                   ← secrets, gitignored
├── .env.example           ← committed template
├── app/
│   ├── __init__.py        ← create_app()
│   ├── config.py          ← Config / DevConfig / ProdConfig
│   ├── auth.py            ← session login helpers
│   ├── db.py              ← get_db() / close_db() / init_app()
│   ├── commands.py        ← flask init-db / verify-vec
│   ├── ingestion.py        ← Docling -> chunk -> embed -> sqlite-vec
│   ├── llm.py              ← raw-requests LLM client
│   ├── routes/
│   │   ├── public.py       ← /  /login  /logout
│   │   ├── dashboard.py    ← /app/dashboard  /app/settings
│   │   ├── assets.py       ← /app/assets/*
│   │   ├── liabilities.py  ← /app/liabilities/*
│   │   ├── events.py       ← /app/events/*
│   │   ├── documents.py    ← /app/documents/*
│   │   ├── chat.py         ← /app/chat
│   │   └── gallery.py      ← /ui/*
│   ├── templates/
│   │   ├── layouts/        ← base.html, app.html, public.html
│   │   ├── components/     ← reusable Jinja macros
│   │   ├── blocks/         ← full page templates
│   │   └── gallery/        ← UI catalog pages
│   └── static/
│       ├── css/
│       └── js/
└── instance/               ← SQLite db + uploads, gitignored
```

### Quick lookup — "I want to..."

| Task | Where |
|---|---|
| Add a new page/route | New file in `app/routes/`, register it in `app/__init__.py` |
| Change the dev server port | `.env` → `FLASK_RUN_PORT` |
| Add a new config value | `app/config.py` → `Config` class, reads from `os.environ` |
| Add a database table | `app/commands.py` → `SCHEMA_SQL`, then `flask --app wsgi init-db` |
| Query the database in a route | `from app.db import get_db`, then `get_db().execute(...)` |
| Add a new CLI command | `app/commands.py`, register in `app/__init__.py` |
| Add a new page's HTML | `app/templates/blocks/your_page.html`, extend `layouts/app.html` |
| Add a sidebar nav link | `app/templates/layouts/app.html` → `nav_items` list |

---

## 8. Quick Reference Table

| If you want to... | Flask-idiomatic way |
|---|---|
| Create the app | `create_app()` factory function, not a module-level `app = Flask(...)` |
| Organize many routes | Split into files, either official `Blueprint`s or (for small apps) a simpler `register(app)` convention like this project |
| Store secrets | `.env` file + `python-dotenv`, never hardcoded, never committed |
| Vary behavior per environment | Config subclasses (`DevConfig`, `ProdConfig`) passed into `create_app()` |
| Hold a per-request resource (DB connection, etc.) | Flask's `g` object + `teardown_appcontext` |
| Add a management script (seed DB, migrate, etc.) | Custom Flask CLI command via `@click.command()` + `app.cli.add_command()` |
| Run the dev server day-to-day | A small `app.py` at the root calling `create_app()` and `app.run()` |
| Run CLI-only commands / deploy to production | `wsgi.py` exposing `app = create_app()` for `flask --app wsgi ...` or a WSGI server (gunicorn, etc.) |

---

## Summary

Everything in this cheat sheet — the factory pattern, splitting routes into
files, the `g`-based DB connection lifecycle, `.env`-driven config, custom
CLI commands — is either directly from Flask's own official tutorial, or a
simplified variant of it made on purpose for this project's size (single
developer, single user, local-only). Learning this pattern here transfers
directly to any other Flask project you look at, including ones that use
"real" Blueprints — the mental model is the same, just with `Blueprint`
instead of `register(app)`.
