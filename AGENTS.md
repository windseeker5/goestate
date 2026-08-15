# AGENTS.md — UI Development Rules

This file instructs AI coding agents (OpenCode, Cursor, Copilot, etc.) on how
to work within this Flask/Jinja starter.

---

## Stack

- **Flask** + **Jinja** = server-side rendering, always.
- **Basecoat UI** = shadcn-compatible visual engine (buttons, cards, dialogs,
  sidebar, forms, dark mode, etc.).
- **Tailwind CSS** = layout and application-specific adjustments only.
- **No React, Vue, Svelte, or any SPA framework** unless there is an explicit,
  documented architectural requirement.

---

## UI Development Rules

1. **Use Flask and Jinja as the default rendering architecture.**
   Do not reach for client-side rendering when Flask can handle it.

2. **Prefer server-side behavior over client-side JavaScript.**
   - Search → `/customers?q=acme`
   - Filter → `/customers?status=active`
   - Sort   → `/customers?sort=name&order=asc`
   - Pagination → `/customers?page=3`
   All implemented with Flask routes + Jinja re-render.

3. **Use Basecoat UI for visual primitives.**
   Do not rebuild: `btn`, `card`, `badge`, `input`, `label`, `table`,
   `alert`, `empty`, `dialog`, `dropdown-menu`, `sidebar`, `tabs`,
   `select`, `combobox`, `toast`, `pagination`-style links.

4. **Before creating new UI, inspect existing components and blocks.**
   - Components → `app/templates/components/`
   - Blocks     → `app/templates/blocks/`
   - Basecoat macros → `app/templates/basecoat/`

5. **Reuse an existing block whenever possible.**
   Compose from blocks before writing a new one-off page.

6. **Do not create a new low-level component if Basecoat already provides it.**
   Extend Basecoat's HTML patterns instead.

7. **Use Tailwind utilities primarily for layout and application-specific
   adjustments**, not to rebuild what Basecoat's semantic classes already cover.
   Avoid long chains of Tailwind utility soup.

8. **Keep JavaScript minimal.**
   Basecoat's `all.min.js` handles: dialog, dropdown, sidebar toggle,
   tabs, select, combobox, toast, popover, slider.
   Do not rewrite these.

9. **When a UI pattern will likely be reused, create or improve a Block**
   rather than embedding a one-off implementation into a page.

10. **Maintain the shadcn/Basecoat visual language across all screens.**
    Use the same spacing, border-radius, colors, and typography conventions.

11. **New Blocks must be demonstrated in the UI gallery** (`/ui/blocks`).
    Document: file path, extends, and expected template variables.

12. **Basecoat Jinja macros** are in `app/templates/basecoat/`.
    Import them like:
    ```jinja
    {% from "basecoat/sidebar.html.jinja" import sidebar %}
    {% from "basecoat/tabs.html.jinja"    import tabs %}
    {% from "basecoat/dialog.html.jinja"  import dialog %}
    ```

---

## Project File Map

```
app/
  __init__.py     create_app() factory, global auth guard (before_request)
  auth.py         session-based single-user auth
  config.py       reads .env
  db.py           SQLite connection + sqlite-vec extension loading
  commands.py     flask init-db / flask verify-vec
  routes/
    public.py       /  /login  /logout
    dashboard.py     /app/dashboard  /app/settings
    assets.py        /app/assets/*
    liabilities.py   /app/liabilities/*
    events.py        /app/events/*
    gallery.py       /ui/*
  templates/
    layouts/      base.html | app.html | public.html
    components/   stat_card | page_header | data_table | pagination |
                  search_filter | card | alert | empty_state
    blocks/       dashboard | assets_* | liabilities_* | events_* |
                  settings_page | login_page | landing_page
    basecoat/     sidebar | tabs | dialog | select | combobox |
                  dropdown-menu | toast | popover | command
    gallery/      index | components | blocks
  static/
    css/output.css   compiled Tailwind + Basecoat (committed)
    js/vendor/basecoat.all.min.js
instance/          SQLite db + uploads (gitignored)
```

**No blueprints.** Every file in `app/routes/` exposes a `register(app)`
function that attaches routes with plain `@app.route`. This is a single-user
local app — blueprint indirection isn't worth the complexity. `url_for()`
calls use the route function name directly (e.g. `url_for("list_assets")`,
not `url_for("assets.list_assets")`).

---

## Starting a New Project

```bash
git clone <this-repo> my-new-project
cd my-new-project
python -m venv venv && venv\Scripts\activate   # Windows
pip install -r requirements.txt
copy .env.example .env   # set LIQUIDATOR_PASSWORD
flask --app wsgi init-db
flask --app wsgi run --debug
```

To add a new page:
1. Create `app/routes/my_thing.py` with a `register(app)` function.
2. Register it in the module tuple in `app/__init__.py`.
3. Create `app/templates/blocks/my_thing_list.html` (extends `layouts/app.html`).
4. Add a sidebar entry in `app/templates/layouts/app.html`.
5. Document it in `app/templates/gallery/blocks.html`.

---

## Python Environment Rules

- Every project **must** have a `venv` virtual environment at the project root
  (folder name: `venv`, not `.venv`).
- If `venv` does not exist, create it before anything else:
  ```bash
  python -m venv venv
  ```
- Activate on Windows:
  ```bash
  venv\Scripts\activate
  ```
- Install all dependencies inside the venv:
  ```bash
  pip install -r requirements.txt
  ```
- **Never install packages globally.** All `pip` commands assume `venv` is active.
- When running any Python or Flask command, always assume `venv` is already activated.

---

## Development Server Rules

- Day-to-day, the server is started with:
  ```bash
  python app.py
  ```
  This always runs in debug mode on the port set by `FLASK_RUN_PORT` in
  `.env` (defaults to 5001 per `.env.example`).
- `wsgi.py` exists only for CLI commands (`flask --app wsgi init-db`,
  `flask --app wsgi verify-vec`) and production WSGI servers — it is not
  the normal way to start the dev server.
- **Do NOT start, stop, or restart the server.** Assume it is already
  running, and never send a kill command to any port — the developer
  manages the server themselves.
- The dev server is available at `http://127.0.0.1:5001` (or whatever
  `FLASK_RUN_PORT` is set to in `.env` — check before assuming the port).
- Debug mode auto-reloads on file save — no manual restarts are needed.
- If a change genuinely requires a restart, note it to the developer and stop. Do not attempt it yourself.

---

## Testing & Validation Rules

- **Always test and validate your work** after every change. Never leave work unverified.
- Use the **Playwright MCP** browser tool to test UI, routes, forms, and all behavior.
- Login is password-only (single user). The password is whatever is set in
  `.env` as `LIQUIDATOR_PASSWORD` — check that file for the current value.
- Navigate to `http://127.0.0.1:5001` (or the configured `FLASK_RUN_PORT`) as the starting point.
- Report findings from Playwright — do not guess behavior from code alone.

---

## MCP (Future — v2)

Do not build MCP tooling in v1.
Once the block catalog is stable, MCP becomes an interface over the content
already in `app/templates/components/` and `app/templates/blocks/`.
