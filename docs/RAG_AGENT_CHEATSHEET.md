# RAG, Agent Personality & Answer Rendering — Cheat Sheet

This document explains, in plain language, how questions asked in the Chat
page (`/app/chat`) turn into answers — and the three places you can
customize that behavior: **retrieval**, **agent personality/skills**, and
**answer rendering**.

It maps directly to the real files in this codebase:
- `app/routes/chat.py` — orchestrates search + prompt building + LLM call
- `app/llm.py` — talks to the LLM provider over HTTP
- `app/ingestion.py` — PDF → Markdown → embeddings → SQLite-Vec
- `app/templates/blocks/chat_page.html` — renders the answer

---

## 1. The Big Picture (RAG Pipeline)

```
PDF Upload → Docling (PDF → Markdown) → FastEmbed (Markdown → Vectors)
           → SQLite-Vec (stores vectors)

User Question
     │
     ▼
[ SEARCH PHASE ]
     ├─ search_chunks()  → vector search over PDF chunks (SQLite-Vec)
     └─ search_events()  → keyword SQL search over the events timeline
     │
     ▼
[ PROMPT BUILDER ]  →  _build_prompt() sandwiches Question + Context
     │
     ▼
[ AGENT INJECTION ] →  SYSTEM_PROMPT (personality, guardrails, skills)
     │
     ▼
[ LLM CALL ] → chat_completion() in app/llm.py (raw HTTP, OpenAI-compatible)
     │
     ▼
[ RENDER ANSWER ] → chat_page.html (Markdown → HTML → styled with Tailwind)
```

This is a **hybrid RAG** pattern:
- **Vector search** (`search_chunks`) is great for conceptual/semantic
  matches in long unstructured PDF text.
- **Keyword SQL search** (`search_events`) is far more precise for small,
  structured data (names, dates, IDs) where embeddings are overkill.
- Both result sets are merged into one `sources` list and fed into the same
  prompt — this is standard practice, not a workaround.

---

## 2. Where the Agent's Personality Lives

Today, the entire personality/guardrail definition is one Python string:

`app/routes/chat.py` (top of file):
```python
SYSTEM_PROMPT = (
    "You are Estate Copilot, an assistant helping an executor manage an estate. "
    "Answer the user's question using ONLY the excerpts provided below — these come "
    "from uploaded PDF documents AND from the executor's own logged timeline events "
    "(calls, meetings, notes). If the answer isn't in the excerpts, say so clearly "
    "instead of guessing. Be precise with dates, amounts, and names — this is used "
    "for legal/financial estate administration."
)
```

This string is sent as the `"role": "system"` message alongside the user's
question + retrieved context:

```python
answer = chat_completion(
    db,
    messages=[
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ],
)
```

### Best-practice structure for a System Prompt

Use clear sections instead of one long paragraph:

```python
SYSTEM_PROMPT = """
# ROLE
You are The Liquidator, an elite, no-nonsense financial and legal AI assistant.

# GUARDRAILS (CRITICAL)
1. ONLY answer based on the provided excerpts.
2. If the excerpts don't contain the answer, reply: "I cannot find this in
   the provided context." Do not guess.
3. Do not offer legal advice.

# SKILLS & FORMATTING
- Compare numbers → output a Markdown table.
- Summarize an event → bullet points with Date, Contact, Type.
"""
```

### Options for managing personality/skills

| Option | What | Pros | Cons |
|---|---|---|---|
| **A — Hardcoded** | Edit `SYSTEM_PROMPT` in `chat.py` | Fast, simple | Requires code change + redeploy |
| **B — Database-driven** | Store prompt in `settings` table (like `llm_model_name`), edit via Settings page | Change personality without touching code | Slightly more plumbing |
| **C — Multi-agent** | New `app/agents/` folder, multiple prompt files (e.g. `financial_analyst.py`), user picks agent from a dropdown in chat UI | True "skills" system, different agents for different tasks | Most engineering effort |

Start with **A**, move to **B** once you're iterating on tone/rules often,
consider **C** only if you need genuinely different specialist behaviors
(e.g. "Financial Analyst" vs "Timeline Summarizer").

---

## 3. Rendering the Answer (Markdown → HTML)

### The problem

LLMs write **Markdown** (`**bold**`, `# Header`, tables, lists). Your
template currently does:

```html
<p class="whitespace-pre-wrap">{{ answer }}</p>
```

This prints the raw string — asterisks and all — because HTML doesn't
interpret Markdown syntax on its own.

### The fix: parse → sanitize → style

1. **Parse** — convert Markdown text into HTML tags (`**x**` → `<strong>x</strong>`).
2. **Sanitize** — strip any dangerous tags/attributes before rendering,
   since the LLM's output is untrusted input (prompt injection risk).
3. **Style** — Tailwind resets default tag styling, so rendered HTML needs
   a typography wrapper (e.g. the `prose` class) or custom CSS to look good.

### Implementation plan

**Dependencies** (both already available in this environment):
```
pip install markdown bleach
```
Add them to `requirements.txt`.

**Custom Jinja filter** — add to `app/__init__.py`:
```python
import markdown
import bleach
from markupsafe import Markup

def register_markdown_filter(app):
    @app.template_filter("markdown")
    def render_markdown(text):
        if not text:
            return ""

        raw_html = markdown.markdown(
            text,
            extensions=["tables", "fenced_code", "nl2br"],
        )

        allowed_tags = bleach.sanitizer.ALLOWED_TAGS | {
            "p", "h1", "h2", "h3", "h4", "h5", "h6",
            "table", "thead", "tbody", "tr", "th", "td",
            "pre", "span", "div", "br", "hr", "img",
        }
        allowed_attrs = {
            "*": ["class", "id"],
            "a": ["href", "title", "target"],
            "img": ["src", "alt", "title"],
        }

        clean_html = bleach.clean(raw_html, tags=allowed_tags, attributes=allowed_attrs)
        return Markup(clean_html)
```
Call `register_markdown_filter(app)` from the `create_app()` factory.

**Template change** — `app/templates/blocks/chat_page.html`, replace:
```html
<p class="whitespace-pre-wrap">{{ answer }}</p>
```
with:
```html
<div class="prose prose-sm max-w-none dark:prose-invert">
    {{ answer | markdown }}
</div>
```

> Note: `prose` classes come from the Tailwind Typography plugin. If it's
> not installed, either add it or hand-write minimal CSS rules for
> `strong`, `em`, `table`, `code`, `ul`/`ol` inside rendered answers.

### Beyond text: charts, tables, custom widgets

Markdown handles bold/italic/lists/tables out of the box. For genuinely
rich output (charts, KPI cards, etc.) you have two paths, consistent with
this project's "server-rendered first" philosophy (see `AGENTS.md`):

- **Structured LLM output → server-rendered component.** Ask the LLM to
  return JSON (e.g. `{"type": "chart", "labels": [...], "values": [...]}`)
  instead of free text, parse it in `chat.py`, and render it with an
  existing Basecoat/Tailwind component or a small chart partial — not by
  letting the LLM emit raw HTML/JS.
- **Markdown tables for anything tabular.** Cheapest option, works today
  with the filter above, and covers most "compare these numbers" cases.

Avoid letting the LLM generate raw `<script>`/canvas code directly — always
sanitize, and prefer structured data the server turns into UI.

---

## 4. Quick Reference: "I want to..."

| Goal | Where to change it |
|---|---|
| Change the agent's tone/personality | `SYSTEM_PROMPT` in `app/routes/chat.py` |
| Add a guardrail (e.g. "never guess") | Add a rule to `SYSTEM_PROMPT` |
| Add a new "skill" (e.g. always output a table) | Add a formatting rule to `SYSTEM_PROMPT` |
| Let personality be edited from the UI | Move `SYSTEM_PROMPT` into the `settings` table (Option B) |
| Support multiple specialist agents | Create `app/agents/`, add a picker in `chat_page.html` (Option C) |
| Fix `**bold**` showing as raw asterisks | Add the `markdown` Jinja filter + `prose` wrapper div |
| Render tables/lists from the LLM | Same filter — `tables` extension already covers it |
| Render charts/graphs | Have the LLM return JSON, render with a real chart component server-side |
