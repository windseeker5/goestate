"""Phase 3 proof-of-concept: Docling -> chunk -> embed -> sqlite-vec.

Standalone script (does NOT touch the Flask app or the real instance/estate.db)
that proves the full ingestion pipeline works end-to-end, per the PRD's
Phase 6.3 "Pipeline Proof of Concept":

    1. Parse a PDF with Docling, preserving table structure, into Markdown.
    2. Chunk the resulting document with Docling's HybridChunker (tokenizer-
       aware, so chunks fit the embedding model's context window).
    3. Generate local embeddings for each chunk with sentence-transformers
       (all-MiniLM-L6-v2, 384-dim, fully offline after the first download).
    4. Store vectors in a throwaway sqlite-vec database and metadata in a
       paired table (mirrors the schema in app/commands.py).
    5. Run a sample retrieval query to prove the RAG loop works and that the
       itemized table survived parsing intact.

Usage:
    venv\\Scripts\\python.exe scripts\\poc\\ingest_poc.py

This script is intentionally decoupled from the Flask app so it can be run
and iterated on independently before wiring it into the real upload flow
(Phase 4).
"""

import os
import sqlite3
import struct
import sys
import time

# Docling's layout model tries to use torch.compile (TorchDynamo/Inductor)
# for a speed boost, which requires an MSVC C++ compiler (cl.exe) on
# Windows. Most dev machines don't have that installed, so we disable
# dynamo up front and fall back to plain eager-mode inference — slower per
# page, but fully functional and dependency-free.
os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")

import sqlite_vec

POC_DIR = os.path.dirname(__file__)
SAMPLE_PDF = os.path.join(POC_DIR, "sample_docs", "funeral_home_invoice.pdf")
POC_DB = os.path.join(POC_DIR, "poc_vectors.db")
EMBED_MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM = 384


def step(label):
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")


def main():
    if not os.path.exists(SAMPLE_PDF):
        print(f"Sample PDF not found at {SAMPLE_PDF}")
        print("Run: venv\\Scripts\\python.exe scripts\\poc\\make_sample_pdf.py")
        sys.exit(1)

    # ── Step 1: Parse PDF with Docling ──────────────────────────────────
    step("STEP 1 — Parsing PDF with Docling")
    from docling.document_converter import DocumentConverter

    t0 = time.time()
    converter = DocumentConverter()
    result = converter.convert(SAMPLE_PDF)
    doc = result.document
    parse_time = time.time() - t0

    markdown = doc.export_to_markdown()
    print(f"Parsed in {parse_time:.2f}s. Markdown length: {len(markdown)} chars.")
    print("\n--- Markdown preview (first 1500 chars) ---")
    print(markdown[:1500])

    has_table_markers = "|" in markdown and "---" in markdown
    print(f"\nTable structure preserved (markdown pipe syntax found): {has_table_markers}")

    # ── Step 2: Chunk with HybridChunker ────────────────────────────────
    step("STEP 2 — Chunking with Docling HybridChunker")
    from docling.chunking import HybridChunker
    from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
    from transformers import AutoTokenizer

    tokenizer = HuggingFaceTokenizer(tokenizer=AutoTokenizer.from_pretrained(EMBED_MODEL_ID))
    chunker = HybridChunker(tokenizer=tokenizer)
    chunks = list(chunker.chunk(dl_doc=doc))

    print(f"Document split into {len(chunks)} chunks.")
    chunk_texts = []
    for i, chunk in enumerate(chunks):
        contextualized = chunker.contextualize(chunk=chunk)
        chunk_texts.append(contextualized)
        n_tokens = tokenizer.count_tokens(contextualized)
        print(f"  [{i}] {n_tokens} tokens: {contextualized[:90]!r}...")

    # ── Step 3: Generate embeddings locally ─────────────────────────────
    step("STEP 3 — Generating local embeddings (sentence-transformers)")
    from sentence_transformers import SentenceTransformer

    t0 = time.time()
    model = SentenceTransformer(EMBED_MODEL_ID)
    embeddings = model.encode(chunk_texts)
    embed_time = time.time() - t0
    print(f"Encoded {len(chunk_texts)} chunks in {embed_time:.2f}s. Shape: {embeddings.shape}")
    assert embeddings.shape[1] == EMBED_DIM, f"Expected {EMBED_DIM}-dim, got {embeddings.shape[1]}"

    # ── Step 4: Store in sqlite-vec (throwaway POC db) ──────────────────
    step("STEP 4 — Storing vectors in sqlite-vec")
    if os.path.exists(POC_DB):
        os.remove(POC_DB)

    db = sqlite3.connect(POC_DB)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)

    db.executescript(f"""
        CREATE VIRTUAL TABLE doc_chunks USING vec0(
            embedding float[{EMBED_DIM}]
        );
        CREATE TABLE doc_chunk_meta (
            chunk_id    INTEGER PRIMARY KEY,
            chunk_text  TEXT NOT NULL,
            chunk_index INTEGER
        );
    """)

    for i, (text, emb) in enumerate(zip(chunk_texts, embeddings)):
        packed = struct.pack(f"{EMBED_DIM}f", *emb.tolist())
        db.execute("INSERT INTO doc_chunks(rowid, embedding) VALUES (?, ?)", (i, packed))
        db.execute(
            "INSERT INTO doc_chunk_meta(chunk_id, chunk_text, chunk_index) VALUES (?, ?, ?)",
            (i, text, i),
        )
    db.commit()
    print(f"Stored {len(chunk_texts)} vectors in {POC_DB}")

    # ── Step 5: Retrieval test ───────────────────────────────────────────
    step("STEP 5 — Retrieval test")
    test_queries = [
        "What is the official date of death?",
        "What was the total cost of the casket?",
        "How many death certificates were ordered?",
    ]

    for query in test_queries:
        query_emb = model.encode([query])[0]
        query_packed = struct.pack(f"{EMBED_DIM}f", *query_emb.tolist())
        rows = db.execute(
            """
            SELECT m.chunk_text, d.distance
            FROM doc_chunks d
            JOIN doc_chunk_meta m ON m.chunk_id = d.rowid
            WHERE d.embedding MATCH ? AND k = 2
            ORDER BY d.distance
            """,
            (query_packed,),
        ).fetchall()

        print(f"\nQuery: {query!r}")
        for text, distance in rows:
            print(f"  distance={distance:.4f}  chunk={text[:120]!r}")

    db.close()

    step("DONE")
    print("Pipeline verified: PDF -> Docling (Markdown + table) -> HybridChunker")
    print("-> sentence-transformers embeddings -> sqlite-vec -> KNN retrieval.")
    print(f"\nThrowaway POC database left at: {POC_DB}")
    print("(safe to delete — this script does not touch instance/estate.db)")


if __name__ == "__main__":
    main()
