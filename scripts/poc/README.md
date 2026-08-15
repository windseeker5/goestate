# Phase 3 POC — Docling → sqlite-vec Ingestion Pipeline

Standalone proof-of-concept for the PRD's document RAG pipeline. Does **not**
touch the Flask app or `instance/estate.db` — everything runs against a
throwaway `poc_vectors.db` in this folder.

> **Note:** this POC used `sentence-transformers` (`all-MiniLM-L6-v2`) for
> embeddings, which is what's shown below. The production app
> (`app/ingestion.py`) was later switched to **fastembed**
> (`BAAI/bge-small-en-v1.5`) — same 384 dimensions, better retrieval
> quality, and no PyTorch dependency. The pipeline shape (parse → chunk →
> embed → store → retrieve) is identical either way; only the embedding
> library changed. See `app/ingestion.py` for the current implementation.

## What it proves

1. **Docling** parses a PDF and preserves table structure as clean Markdown
   pipe-table syntax (verified against a mock funeral home invoice with a
   7-row itemized table).
2. **HybridChunker** (Docling's tokenizer-aware chunker) splits the document
   into semantically coherent chunks sized to fit the embedding model's
   context window, and serializes table rows as `Field, Column = Value`
   pairs so each chunk stays self-describing even without the table headers.
3. **sentence-transformers** (`all-MiniLM-L6-v2`, 384-dim) generates fully
   local embeddings — no API calls, no cloud dependency.
4. **sqlite-vec** stores the vectors and a KNN query correctly retrieves the
   chunk containing the answer for targeted questions (e.g. "What is the
   official date of death?" → returns the chunk with the actual date).

## Running it

```bash
# One-time: install the dev-only PDF generator
venv\Scripts\pip.exe install -r scripts\poc\requirements-dev.txt

# Generate the sample PDF (a mock funeral home invoice with an itemized table)
venv\Scripts\python.exe scripts\poc\make_sample_pdf.py

# Run the full pipeline
venv\Scripts\python.exe scripts\poc\ingest_poc.py
```

## Known issue: Windows + torch.compile

Docling's layout model tries to use `torch.compile` (TorchDynamo/Inductor)
for a speed boost. On Windows without an MSVC C++ compiler (`cl.exe`)
installed, this fails with `InvalidCxxCompiler: Compiler: cl is not found`.

`ingest_poc.py` sets `TORCHDYNAMO_DISABLE=1` before importing torch, which
falls back to eager-mode inference. This is slower per page (~10s for a
1-page PDF on CPU) but requires no extra toolchain installs. The same
setting should be applied wherever Docling parsing happens in the real app
(Phase 4).

## Known limitation: large table chunks and specific-value lookups

For documents with many table rows, HybridChunker may group several rows
into one chunk (to hit the target token count), which occasionally makes
KNN retrieval slightly less precise for queries about a single specific
line item buried in a large chunk. Not a pipeline bug — just something to
watch as real documents get bigger. Mitigations to consider in Phase 4 if
this becomes a real problem:
- Increase `max_tokens` headroom so fewer forced splits happen (already
  fairly natural in this test — each item stayed together with its own
  quantity/price).
- Add a re-ranking step after retrieval for numeric/specific queries.
- Chunk large tables per-row instead of per-token-budget when a table has
  more than N rows.

None of this blocks Phase 4 — the core loop (parse → chunk → embed → store →
retrieve) works correctly end-to-end.

## Files

| File | Purpose |
|---|---|
| `make_sample_pdf.py` | Generates a mock funeral home invoice PDF fixture (dev-only, needs `reportlab`) |
| `ingest_poc.py` | The full pipeline: parse → chunk → embed → store → retrieve |
| `sample_docs/` | Generated PDF fixture lives here (gitignored) |
| `poc_vectors.db` | Throwaway sqlite-vec database created by the script (gitignored) |
