# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Estate Copilot — a locally hosted Flask app for executors/liquidators managing an
estate: an asset/liability ledger, a timeline/CRM, and a document RAG system for
chatting with an LLM that knows every uploaded document. See `PRD.md` for full
product requirements and `AGENTS.md` for detailed UI development rules (this file
summarizes and supplements it — read `AGENTS.md` for the full UI rulebook).

## Stack

Flask 3 (plain functions, no blueprints, no ORM) + Jinja2 + Basecoat UI
(shadcn-compatible) + Tailwind CSS v4 (precompiled, no Node needed to run) +
SQLite (stdlib `sqlite3`) + sqlite-vec for vector search + Docling for PDF
parsing + fastembed (ONNX, no PyTorch) for local embeddings + any
OpenAI-compatible `/v1/chat/completions` endpoint for chat, called via raw
`requests` (no LangChain, no SDKs). No React, no TypeScript, no SPA framework.

## Commands

```bash
# setup
python -m venv venv
venv\Scripts\activate            # Windows; assume venv already activated otherwise
pip install -r requirements.txt
copy .env.example .env           # then set ADMIN_EMAIL / LIQUIDATOR_PASSWORD

flask --app wsgi init-db         # create tables + bootstrap first admin user (idempotent)
flask --app wsgi verify-vec      # confirm sqlite-vec KNN search works end-to-end
flask --app wsgi reindex-all     # re-parse + re-embed every document (after an EMBED_MODEL_ID change)

python app.py                    # start the dev server (debug mode, auto-reloads)
```

- `wsgi.py` is CLI/production-WSGI-only (`flask --app wsgi <cmd>`, gunicorn). Day-to-day dev always uses `python app.py`.
- Server runs at `http://127.0.0.1:<FLASK_RUN_PORT>` (default 5001, check `.env`).
  Keep `FLASK_RUN_HOST=127.0.0.1` unless you've confirmed the reverse proxy needs
  otherwise — `python app.py` runs with `DEBUG=True`, and the Werkzeug debugger
  exposes frame locals (including the `Authorization` header built in
  `app/llm.py` with the LLM API key) to anyone who can reach the port. See
  `docs/DEPLOYMENT_NETWORKING.md` for how this interacts with a Caddy/Docker
  reverse proxy and how to triage "site unreachable from outside" reports.
- **Do not start/stop/restart the dev server or kill its port** — assume it's already
  running under the developer's control. Under `python app.py` debug mode auto-reloads
  on save. If a change genuinely needs a manual restart, say so and stop.
- **Before verifying anything in the browser, check what is actually serving the port:**
  ```bash
  ps -eo pid,lstart,cmd | grep "wsgi:app\|app\.py" | grep -v grep
  ```
  If **Gunicorn** is serving it, that is a dev misconfiguration, not something to work
  around: Gunicorn has no `reload` (`gunicorn.conf.py`), so it serves whatever code it
  loaded at startup and **your edits will not take effect**. Compare its start time to
  the file mtimes. Tell the developer to restart it (`pkill -f "gunicorn.*wsgi:app"`,
  then `python app.py`) — don't do it yourself, and don't report a browser result from
  a server older than your edits. This has already caused one full verification pass to
  report a working fix as broken.
- Module-level caches are **per process**: `_embed_model` in `app/ingestion.py` holds the
  loaded embedding model for the life of the worker. Changing `EMBED_MODEL_ID` therefore
  needs a fresh process, not just a template/code reload — otherwise queries get embedded
  with the old model and compared against new vectors, which fails silently with
  plausible-looking but wrong results.
- Production/Gunicorn only: `./venv/bin/gunicorn --config gunicorn.conf.py --bind 127.0.0.1:5001 wsgi:app`. The config sets a 600s timeout — scanned-PDF OCR via Docling can take minutes, and Gunicorn's default 30s timeout would otherwise 500 the upload mid-processing. The Flask dev server has no request timeout, so long OCR uploads work there too — there is no reason to run Gunicorn in development.
- Rebuild CSS only when adding new Tailwind classes or upgrading Basecoat (`output.css` is committed, so this is optional for running the app): `npm install && npm run build:css` (or `npm run watch:css`).
- No automated test suite exists. Verify changes by driving the running app with the Playwright MCP browser tool — do not claim a UI change works without checking it in the browser.
- **Ad-hoc scripts and screenshots go in `test/`, never the repo root.** Any throwaway Python script or screenshot produced while debugging/verifying belongs in `test/` (gitignored) — don't leave `*.png` or one-off `.py` files loose at the top level.

## Architecture

**No blueprints.** Every file in `app/routes/` exposes a `register(app)` function
that attaches routes with plain `@app.route`, called from the module tuple in
`app/__init__.py`. `url_for()` uses the route function name directly (e.g.
`url_for("list_assets")`, not `url_for("assets.list_assets")`).

```
app/
  __init__.py     create_app() factory; before_request hooks load g.user and
                  gate everything under /app behind login (see auth below)
  auth.py         session auth + role checks (login_required, admin_required)
  config.py       reads .env via python-dotenv into Config/DevConfig/ProdConfig
  db.py           per-request SQLite connection (g.db) with sqlite-vec loaded;
                  open_db() for use outside a request context (CLI commands)
  commands.py     SCHEMA_SQL (source of truth for the schema) + flask init-db /
                  flask verify-vec / flask reindex-all CLI commands
  ingestion.py    Docling -> HybridChunker -> fastembed -> sqlite-vec pipeline
  llm.py          raw-requests client for any OpenAI-compatible provider
  photo_storage.py  asset/liability photo upload validation, storage, serving
  routes/*.py     one file per domain, each with a register(app) function
  templates/
    basecoat/     vendored Basecoat Jinja macros (import, don't rebuild)
    components/   reusable Jinja macros (page_header, data_table, stat_card, ...)
    blocks/       full page templates
    layouts/      base.html / app.html / public.html
  static/
    css/output.css   compiled CSS, committed — no Node required to run the app
    js/components/   small KD UI component controllers
    js/vendor/       Basecoat JS runtime (all.min.js: dialog, dropdown, sidebar,
                      tabs, select, combobox, toast, popover, slider — don't reimplement)
instance/          SQLite db + uploads (gitignored)
```

### Auth & roles

Email + password session auth with two roles, **not single-user** despite what
older docs (README.md/AGENTS.md) say — `users` table, `/app/users` CRUD, admin
vs viewer:
- `admin` — full read/write everywhere, manages users.
- `viewer` — read-only: can browse and use chat, cannot create/edit/delete.

`flask init-db` bootstraps exactly one admin user from `ADMIN_EMAIL` /
`LIQUIDATOR_PASSWORD` in `.env`, but only if the `users` table is empty. After
that, admins manage users from `/app/users` — no CLI needed. Everything under
`/app` requires login (enforced in `app/__init__.py`'s `before_request`); use
the `@admin_required` decorator from `app/auth.py` on any mutating route, and
`@login_required` where read access alone should still require auth.

### Database

Plain `sqlite3` (stdlib), no ORM. The schema in `app/commands.py::SCHEMA_SQL`
is the single source of truth; `flask init-db` is safe to re-run (`CREATE
TABLE IF NOT EXISTS`) and also runs small manual `ALTER TABLE` migrations for
columns added after a table already existed — when adding a column to an
existing table, add both to `SCHEMA_SQL` *and* to the `migrations` tuple in
`init_db_command`. Tables: `users`, `assets`, `liabilities`, `events`, `tasks`,
`documents`, `settings` (LLM provider config), plus the RAG pair `doc_chunks`
(sqlite-vec virtual table, 384-dim float embeddings) / `doc_chunk_meta`, plus
the FTS5 search indexes `events_fts` / `tasks_fts`.

The FTS5 indexes are external-content tables kept in sync by `AFTER
INSERT/UPDATE/DELETE` triggers declared next to them in `SCHEMA_SQL` — so
event/task routes need no index-maintenance code. `init-db` rebuilds both every
run (also the repair path if one drifts). If you add a searchable column to
`events` or `tasks`, add it to the FTS table **and** to all three triggers; a
`'delete'` command row must repeat the OLD values verbatim or the index
corrupts.

### Document RAG pipeline

Upload on `/app/documents` ingests synchronously (`app/ingestion.py`):
Docling parses the PDF to Markdown (preserving table structure) → HybridChunker
splits it token-aware for the embedding model's context window → fastembed
(`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, 384-dim, ONNX
Runtime — no PyTorch, no network) embeds each chunk → vectors go to
`doc_chunks`, text+metadata to `doc_chunk_meta`. The model is **multilingual on
purpose** — the estate corpus is French. Embeddings are always local,
deliberately decoupled from whichever chat LLM is configured — this keeps the
vector space stable across LLM provider switches and avoids sending full
documents to a third party.

Changing `EMBED_MODEL_ID` invalidates every stored vector (same dimension ≠
same vector space — it degrades silently rather than erroring), so always
follow it with `flask --app wsgi reindex-all`.

Chat (`/app/chat`, `app/routes/chat.py`) embeds the question with the same
local model, does a sqlite-vec KNN search (`ingestion.search_chunks`) plus an
FTS5 full-text search over the events timeline and the user's tasks
(`search_events` / `search_tasks`, ranked by `bm25()` — see the `events_fts` /
`tasks_fts` tables and their sync triggers in `app/commands.py`), sandwiches
question + retrieved context into a prompt with a system-prompt agent
personality, and calls
`app/llm.py::chat_completion()` against whatever provider is set on
`/app/settings`. See `docs/RAG_AGENT_CHEATSHEET.md` for the full flow and the
three customization points (retrieval, agent personality/skills, answer
rendering). See `scripts/poc/README.md` for the original proof-of-concept and
Windows gotchas (`TORCHDYNAMO_DISABLE=1`, already set in `ingestion.py`, to
avoid Docling needing an MSVC compiler).

Heavy deps (`docling`, `fastembed`) are imported lazily inside functions so
importing `app/ingestion.py` — and thus starting Flask — stays fast.

### LLM provider

`app/llm.py` is a minimal raw-HTTP client (no SDK) against any
OpenAI-compatible `/v1/chat/completions` endpoint (Ollama, LM Studio,
OpenRouter, OpenAI). Config (base URL, API key, model name) lives in the
`settings` table, editable at `/app/settings` — switching providers is a
UI-only change, no code changes needed.

## UI conventions

Full rules in `AGENTS.md`; key points:
- Prefer server-side rendering via Flask routes + Jinja re-render over
  client-side JS for search/filter/sort/pagination (query params, e.g.
  `?q=&sort=&order=&page=`).
- Use Basecoat primitives (`btn`, `card`, `dialog`, `sidebar`, `select`,
  `combobox`, `toast`, etc.) — don't rebuild them. Import macros from
  `app/templates/basecoat/*.html.jinja`.
- Check `app/templates/components/` and `app/templates/blocks/` for an
  existing component/block before writing a new one.
- Reusable UI is promoted to the separate KD UI repo (`../kdui/flask-shadcn-starter`)
  per `docs/KDUI_Component_Workflow.md`; Estate Copilot itself has no `/ui`
  component gallery.
- Adding a page: route file with `register(app)` in `app/routes/`, register it
  in `app/__init__.py`'s module tuple, add a template in `app/templates/blocks/`,
  add a sidebar entry in `app/templates/layouts/app.html`, verify with Playwright.

## Local test login

Sign in as `kdresdell@gmail.com` / `admin123` for local Playwright verification
unless told otherwise — check `.env` for the actual current admin credentials
first, since `ADMIN_EMAIL`/`LIQUIDATOR_PASSWORD` there is the source of truth.
