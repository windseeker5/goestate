import re

from flask import render_template, request

from app.db import get_db
from app.ingestion import search_chunks
from app.llm import LLMConfigError, LLMRequestError, chat_completion

SYSTEM_PROMPT = (
    "You are Estate Copilot, an assistant helping an executor manage an estate. "
    "Answer the user's question using ONLY the excerpts provided below — these come "
    "from uploaded PDF documents AND from the executor's own logged timeline events "
    "(calls, meetings, notes). If the answer isn't in the excerpts, say so clearly "
    "instead of guessing. Be precise with dates, amounts, and names — this is used "
    "for legal/financial estate administration."
)

# Vector search (sqlite-vec) excels at conceptual/semantic matches inside long
# PDF text but is unreliable for exact keyword lookups (names, IDs). The
# `events` timeline table is small structured data, so a plain SQL keyword
# search against it is instant and far more precise for "did I log a call
# with X" style questions than trying to embed every event. Combining both
# (hybrid search) is standard RAG practice: vector search for unstructured
# documents, keyword/SQL search for structured records, merged into one
# context window for the LLM.
STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "what", "when", "where",
    "who", "which", "how", "did", "do", "does", "of", "in", "on", "for",
    "with", "and", "or", "to", "my", "me", "i", "that", "this", "about",
    "tell", "give", "find", "please", "you", "any",
}


def _extract_keywords(question):
    """Pull out meaningful search terms (names, subjects) from a question."""
    words = re.findall(r"[^\W\d_]+", question.lower(), flags=re.UNICODE)
    return [w for w in words if len(w) >= 3 and w not in STOPWORDS]


def search_events(db, question, limit=3):
    """Keyword-search the timeline (events) table for rows matching the question.

    Returns results shaped like ingestion.search_chunks() output (chunk_text,
    filename, etc.) so they can be merged with vector search results and fed
    into the same prompt-building / template-rendering code paths.
    """
    keywords = _extract_keywords(question)
    if not keywords:
        return []

    clauses = []
    params = []
    for kw in keywords:
        clauses.append(
            "(LOWER(title) LIKE ? OR LOWER(contact) LIKE ? OR LOWER(notes) LIKE ? OR LOWER(type) LIKE ?)"
        )
        like = f"%{kw}%"
        params.extend([like, like, like, like])

    rows = db.execute(
        f"SELECT id, title, contact, type, event_date, notes FROM events "
        f"WHERE {' OR '.join(clauses)} ORDER BY event_date DESC LIMIT ?",
        (*params, limit),
    ).fetchall()

    results = []
    for r in rows:
        text = (
            f"Type: {r['type']}\n"
            f"Contact: {r['contact'] or '—'}\n"
            f"Date: {r['event_date'] or '—'}\n"
            f"Notes: {r['notes'] or '(no notes)'}"
        )
        results.append(
            {
                "chunk_text": text,
                "distance": None,  # keyword match, not a vector distance
                "document_id": None,
                "filename": f"Timeline: {r['title']}",
                "linked_entity_type": "event",
                "linked_entity_id": r["id"],
            }
        )
    return results


def _build_prompt(question, chunks):
    if not chunks:
        context = "(No relevant documents or timeline events found.)"
    else:
        context = "\n\n".join(
            f"[Source: {c['filename']}]\n{c['chunk_text']}" for c in chunks
        )
    return (
        f"Excerpts:\n{'-' * 40}\n{context}\n{'-' * 40}\n\n"
        f"Question: {question}"
    )


def register(app):
    @app.route("/app/chat", methods=["GET", "POST"])
    def chat():
        db = get_db()
        answer = None
        sources = []
        question = ""
        error = None

        if request.method == "POST":
            question = request.form.get("question", "").strip()
            if question:
                doc_count = db.execute("SELECT COUNT(*) FROM documents WHERE ingestion_status = 'embedded'").fetchone()[0]
                event_matches = search_events(db, question, limit=3)

                if doc_count == 0 and not event_matches:
                    error = "No documents have been ingested yet. Upload a PDF on the Documents page first."
                else:
                    try:
                        # top_k=5 is the standard RAG sweet spot for
                        # paragraph-sized chunks: enough of a safety net for
                        # the LLM to find the right answer even when vector
                        # similarity scoring doesn't perfectly track logical
                        # relevance (see chat_page.html's "Retrieved Context"
                        # copy), without triggering "lost in the middle"
                        # degradation from stuffing in too much text. The UI
                        # keeps this readable regardless via the collapsed
                        # accordion cards — what the LLM sees should always
                        # match what's shown here, so don't cap the display
                        # count separately from top_k.
                        doc_matches = search_chunks(db, question, top_k=5) if doc_count else []
                        sources = doc_matches + event_matches
                        prompt = _build_prompt(question, sources)
                        answer = chat_completion(
                            db,
                            messages=[
                                {"role": "system", "content": SYSTEM_PROMPT},
                                {"role": "user", "content": prompt},
                            ],
                        )
                    except LLMConfigError as exc:
                        error = str(exc)
                    except LLMRequestError as exc:
                        error = f"LLM request failed: {exc}"

        return render_template(
            "blocks/chat_page.html",
            question=question,
            answer=answer,
            sources=sources,
            error=error,
        )
