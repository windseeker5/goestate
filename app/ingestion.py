"""Document ingestion pipeline: PDF -> Docling -> chunks -> embeddings -> sqlite-vec.

This is the production version of the logic proven out in
scripts/poc/ingest_poc.py. It writes into the real database (documents,
doc_chunks, doc_chunk_meta) instead of a throwaway one.

Embeddings are generated locally with fastembed (ONNX Runtime, no PyTorch)
rather than sentence-transformers. This matches how most local-first RAG
stacks are built: embeddings stay a separate, always-local concern from the
LLM used for chat, which can be any provider the user configures. Keeping
embeddings local avoids sending entire document contents to a third party
on every upload, and avoids re-indexing headaches from switching LLM
providers later — the vector space stays stable regardless of which chat
model is active.

Heavy-ish dependencies (docling, fastembed) are imported lazily inside
functions so that importing this module — and therefore starting the Flask
app — stays fast even before a document is ever uploaded. The embedding
model is cached at module level once loaded so repeated ingestion/query
calls in the same process don't reload it.
"""

import os
import struct

# Docling's layout model tries to use torch.compile (TorchDynamo/Inductor)
# for a speed boost, which requires an MSVC C++ compiler (cl.exe) on
# Windows. Most dev machines don't have that installed, so we disable
# dynamo up front and fall back to plain eager-mode inference. See
# scripts/poc/README.md for details.
os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")

# fastembed model — 384-dim, runs on ONNX Runtime, no PyTorch required.
#
# Multilingual on purpose. The previous model here was BAAI/bge-small-en-v1.5,
# where the "-en-" means English-only, while this estate's documents, timeline
# notes and questions are all French. It half-worked on shared Latin roots but
# handicapped every French query.
#
# paraphrase-multilingual-MiniLM-L12-v2 is also 384-dim, so doc_chunks stays
# `float[384]` and pack_embedding()'s struct.pack("384f") is unchanged — no
# vector-table migration. But same dimension is NOT the same vector space:
# vectors written by the old model are meaningless to this one and will not
# raise an error, they will just quietly return bad matches. After changing
# this constant you MUST run `flask --app wsgi reindex-all`.
EMBED_MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBED_DIM = 384

_embed_model = None


def get_embed_model():
    """Lazily load and cache the fastembed embedding model."""
    global _embed_model
    if _embed_model is None:
        from fastembed import TextEmbedding

        _embed_model = TextEmbedding(model_name=EMBED_MODEL_ID)
    return _embed_model


def embed_texts(texts):
    """Embed a list of strings, returning a list of 384-dim float lists."""
    model = get_embed_model()
    embeddings = list(model.embed(texts))
    return [emb.tolist() for emb in embeddings]


def pack_embedding(values):
    """Pack a list of floats into the binary format sqlite-vec expects."""
    return struct.pack(f"{EMBED_DIM}f", *values)


def parse_and_chunk(filepath):
    """Parse a document with Docling and split it into chunks.

    Returns a list of chunk text strings (contextualized — i.e. including
    any relevant section heading context, which is what should be embedded
    per Docling's own recommendation).
    """
    from docling.chunking import HybridChunker
    from docling.document_converter import DocumentConverter
    from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
    from transformers import AutoTokenizer

    converter = DocumentConverter()
    result = converter.convert(filepath)
    doc = result.document

    tokenizer = HuggingFaceTokenizer(tokenizer=AutoTokenizer.from_pretrained(EMBED_MODEL_ID))
    chunker = HybridChunker(tokenizer=tokenizer)
    chunks = list(chunker.chunk(dl_doc=doc))

    return [chunker.contextualize(chunk=chunk) for chunk in chunks]


def ingest_document(db, document_id, filepath, linked_entity_type=None, linked_entity_id=None):
    """Run the full ingestion pipeline for a single document row.

    Updates the documents.ingestion_status field as it progresses:
    pending -> parsed -> embedded, or -> error with ingestion_error set.

    Safe to call multiple times for the same document_id — old chunks for
    that document are deleted first.
    """
    try:
        chunk_texts = parse_and_chunk(filepath)

        db.execute(
            "UPDATE documents SET ingestion_status = 'parsed' WHERE id = ?",
            (document_id,),
        )
        db.commit()

        if not chunk_texts:
            db.execute(
                "UPDATE documents SET ingestion_status = 'error', ingestion_error = ? WHERE id = ?",
                ("Docling produced no chunks (empty or unparseable document).", document_id),
            )
            db.commit()
            return False

        embeddings = embed_texts(chunk_texts)

        # Remove any previous chunks for this document (safe re-ingestion).
        old_ids = [
            row[0]
            for row in db.execute(
                "SELECT chunk_id FROM doc_chunk_meta WHERE document_id = ?", (document_id,)
            ).fetchall()
        ]
        for cid in old_ids:
            db.execute("DELETE FROM doc_chunks WHERE rowid = ?", (cid,))
        db.execute("DELETE FROM doc_chunk_meta WHERE document_id = ?", (document_id,))

        for i, (text, emb) in enumerate(zip(chunk_texts, embeddings)):
            cur = db.execute(
                "INSERT INTO doc_chunk_meta (document_id, linked_entity_type, linked_entity_id, "
                "chunk_text, chunk_index) VALUES (?, ?, ?, ?, ?)",
                (document_id, linked_entity_type, linked_entity_id, text, i),
            )
            chunk_id = cur.lastrowid
            db.execute(
                "INSERT INTO doc_chunks (rowid, embedding) VALUES (?, ?)",
                (chunk_id, pack_embedding(emb)),
            )

        db.execute(
            "UPDATE documents SET ingestion_status = 'embedded' WHERE id = ?",
            (document_id,),
        )
        db.commit()
        return True

    except Exception as exc:  # noqa: BLE001 - want to record any failure reason
        db.execute(
            "UPDATE documents SET ingestion_status = 'error', ingestion_error = ? WHERE id = ?",
            (str(exc)[:2000], document_id),
        )
        db.commit()
        return False


def search_chunks(db, query_text, top_k=5):
    """Embed a query and return the top_k nearest chunks with metadata.

    Returns a list of dicts: {chunk_text, distance, document_id, filename,
    linked_entity_type, linked_entity_id}.
    """
    query_emb = embed_texts([query_text])[0]
    query_packed = pack_embedding(query_emb)

    rows = db.execute(
        """
        SELECT
            m.chunk_text,
            d.distance,
            m.document_id,
            doc.filename,
            m.linked_entity_type,
            m.linked_entity_id
        FROM doc_chunks d
        JOIN doc_chunk_meta m ON m.chunk_id = d.rowid
        JOIN documents doc ON doc.id = m.document_id
        WHERE d.embedding MATCH ? AND k = ?
        ORDER BY d.distance
        """,
        (query_packed, top_k),
    ).fetchall()

    return [
        {
            "chunk_text": row[0],
            "distance": row[1],
            "document_id": row[2],
            "filename": row[3],
            "linked_entity_type": row[4],
            "linked_entity_id": row[5],
        }
        for row in rows
    ]
