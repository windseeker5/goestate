# Estate Copilot

A specialized, ultra-light, locally hosted web application for executors and
liquidators managing an estate. Combines a simple asset/liability ledger, a
timeline/CRM, and a document RAG system so you can chat with an LLM that
knows every document in the estate file.

See [`Estate_Copilot_PRD.md`](Estate_Copilot_PRD.md) for the full product
requirements.

---

## Stack

| Layer | Technology |
|---|---|
| Server | Flask 3, plain functions (no blueprints) |
| Templates | Jinja2 |
| UI | [Basecoat UI](https://basecoatui.com) — shadcn-compatible |
| Styling | Tailwind CSS v4 (pre-compiled, no Node needed to run) |
| Database | SQLite (stdlib) + [sqlite-vec](https://github.com/asg017/sqlite-vec) for vector search |
| Document parsing | [Docling](https://github.com/docling-project/docling) — preserves table structure as Markdown |
| Embeddings | [fastembed](https://github.com/qdrant/fastembed) `BAAI/bge-small-en-v1.5`, 384-dim, ONNX Runtime (no PyTorch), fully local/offline |
| LLM | Any OpenAI-compatible `/v1/chat/completions` endpoint via raw `requests` — Ollama, LM Studio, OpenRouter, OpenAI |
| Auth | Single-user session auth, password from `.env` |

No React. No TypeScript. No ORM. No blueprints. No LangChain. Server-side
rendered, always.

---

## Quick Start

```bash
python -m venv venv
venv\Scripts\activate            # Windows
pip install -r requirements.txt

copy .env.example .env
# edit .env and set LIQUIDATOR_PASSWORD

flask --app wsgi init-db         # create tables
flask --app wsgi verify-vec      # confirm sqlite-vec works (optional)

python app.py                    # start the dev server (debug mode)
```

Open **http://localhost:5001** (port comes from `FLASK_RUN_PORT` in `.env`,
defaults to 5001 per `.env.example`).

If you are proxying through Docker/Caddy, keep `FLASK_RUN_HOST=0.0.0.0` so the
proxy container can reach the Flask app on the host.

`init-db` and `verify-vec` are CLI-only commands, run via `flask --app
wsgi`. Day-to-day, just use `python app.py` to start the server.

### Gunicorn and scanned PDFs

When running the application through Gunicorn, use the included configuration:

```bash
./venv/bin/gunicorn --config gunicorn.conf.py --bind 127.0.0.1:5001 wsgi:app
```

It gives scanned-PDF OCR and document analysis up to 10 minutes to complete.
Gunicorn's default 30-second request timeout can otherwise end the upload with
an Internal Server Error while Docling is still processing the document.

| Route | What you see |
|---|---|
| `/` | Landing page |
| `/login` | Password login |
| `/app/dashboard` | Estate overview |
| `/app/assets` | Asset ledger (list / detail / create / edit / delete) |
| `/app/liabilities` | Liability ledger |
| `/app/events` | Timeline / CRM log |
| `/app/documents` | Upload PDFs, monitor ingestion status |
| `/app/chat` | Ask questions about ingested documents (RAG) |
| `/app/settings` | Configure LLM provider (base URL, model, API key) |

---

## Project Structure

```
app/
  __init__.py            create_app() factory, global auth guard
  auth.py                session-based single-user auth
  config.py               reads .env
  db.py                   SQLite connection + sqlite-vec extension loading
  commands.py             flask init-db / flask verify-vec
  ingestion.py            Docling -> chunk -> embed -> sqlite-vec pipeline
  llm.py                  raw-requests client for OpenAI-compatible providers
  routes/
    public.py             /  /login  /logout
    dashboard.py           /app/dashboard  /app/settings
    assets.py              /app/assets/*
    liabilities.py         /app/liabilities/*
    events.py               /app/events/*
    documents.py             /app/documents/* (upload, list, delete, reindex)
    chat.py                  /app/chat (RAG question answering)
  templates/
    basecoat/              Vendored Basecoat Jinja macros
    components/            Reusable Jinja macros (page_header, data_table, etc.)
    blocks/                Full page templates
    layouts/                base.html / app.html / public.html
  static/
    css/output.css          Compiled CSS (committed — no Node needed to run)
    js/components/           Reusable KD UI component controllers
    js/vendor/               Basecoat JS runtime
instance/                   SQLite db + uploads (gitignored)
```

There are no blueprints. Every route file exposes a `register(app)` function
that attaches its routes with plain `@app.route`. `url_for()` calls use the
function name directly, e.g. `url_for("list_assets")`.

Reusable UI is promoted to the separate KD UI repository at
`../kdui/flask-shadcn-starter`, whose `/ui` routes are the developer showroom.
Estate Copilot intentionally does not expose component-development pages.

---

## Database

Plain `sqlite3` (stdlib), no ORM. Schema lives in `app/commands.py` under
`SCHEMA_SQL` and is applied with:

```bash
flask --app wsgi init-db
```

Tables: `assets`, `liabilities`, `events`, `documents`, `settings`,
plus `doc_chunks` (a `sqlite-vec` virtual table, 384-dim embeddings) and
`doc_chunk_meta` for the RAG pipeline.

---

## Document RAG Pipeline

Upload a PDF on `/app/documents` and it's ingested synchronously:

1. **Docling** parses the PDF into Markdown, preserving table structure.
2. **HybridChunker** (tokenizer-aware) splits it into chunks sized for the
   embedding model's context window.
3. **fastembed** (`BAAI/bge-small-en-v1.5`) embeds each chunk locally — no
   network calls, no API keys, no PyTorch (runs on ONNX Runtime).
4. Vectors go into `doc_chunks` (sqlite-vec), text + metadata into
   `doc_chunk_meta`.

Embeddings are intentionally always local, independent of whichever LLM
provider is configured for chat. This keeps the vector space stable if you
switch LLM providers later, and avoids sending full document contents to a
third party on every upload — only the top-k retrieved chunks for a
question ever reach the LLM, at chat time.

Ask a question on `/app/chat`:

1. The question is embedded with the same local model.
2. `sqlite-vec` KNN search finds the most relevant chunks.
3. Those chunks + the question are sent to whichever LLM is configured in
   Settings (any OpenAI-compatible `/v1/chat/completions` endpoint).
4. The answer is shown alongside the source excerpts it was built from.

See `scripts/poc/README.md` for the original proof-of-concept and known
Windows gotchas (e.g. `TORCHDYNAMO_DISABLE=1` to avoid needing an MSVC
compiler for Docling's layout model).

### Trying it with a local model (Ollama)

```
API Base URL: http://localhost:11434/v1
Model Name:   llama3.1:latest   (or any model you've pulled)
API Key:      (leave empty)
```

Any Ollama model works, as does LM Studio, OpenRouter, or OpenAI — just
change the three fields in Settings, no code changes needed.

---

## Adding a New Page

1. Add a route file in `app/routes/your_thing.py` with a `register(app)` function.
2. Register it in `app/__init__.py`'s module tuple.
3. Add templates in `app/templates/blocks/`.
4. Add a sidebar entry in `app/templates/layouts/app.html`.
5. Verify the page with Playwright. Promote reusable UI to KD UI.

---

## Rebuilding CSS (optional)

`output.css` is committed so you can run the app with zero Node.js.
Only rebuild when adding new Tailwind classes or upgrading Basecoat:

```bash
npm install
npm run build:css
```

---

## Roadmap

See `Estate_Copilot_PRD.md` section 6. Done: ledger CRUD (assets,
liabilities, events), document upload + Docling → sqlite-vec ingestion,
LLM chat agent with source traceability, Settings hub for LLM config.

Next: linking document uploads directly to ledger entries (so a bill
attached to a liability auto-tags its chunks), background ingestion so
large PDFs don't block the request, and a knowledge base bulk-upload flow
for historical documents not tied to any single ledger entry.
