# Product Requirements Document (PRD): Estate Copilot

## 1. Introduction & Problem Statement
Being named the liquidator or executor of an estate (especially in jurisdictions like Quebec) is an administratively heavy and emotionally draining responsibility. Executors must manage a massive amount of bureaucratic paperwork, interact with notaries, banks, and government entities, and keep a precise accounting of assets and liabilities. Traditional tools like Google Sheets often fail because they cannot effectively handle the unstructured data (PDFs, scanned bills, long email threads, audio notes) alongside the structured accounting data. 

**Estate Copilot** is a specialized, ultra-light, locally hosted web application designed to solve this. It combines a simple CRM, an asset/liability ledger, and a Retrieval-Augmented Generation (RAG) system. This allows the executor to manage tasks, track finances, and instantly chat with an LLM agent that knows every detail of the estate's documents.

## 2. Core Principles
*   **Ultra-Light & Local:** Must run easily on a single local machine without complex Docker orchestration or heavy background services.
*   **Open Source Stack:** Relies strictly on open-source libraries and databases.
*   **Agnostic LLM Integration:** The user must be able to plug in *any* LLM of their choice (via OpenRouter, local Ollama models, or direct API providers) without breaking the system.
*   **No Bloat:** Avoids heavy AI frameworks (like LangChain or LangGraph) in favor of simple, maintainable Python integrations.

## 3. Technology Stack (The "Ultra-Light Local" Stack)
*   **Frontend UI:** Python Flask templates utilizing custom Jinja components (styled to replicate Shadcn UI) and Tailwind CSS. No blueprints — plain route modules with `register(app)` functions (see AGENTS.md).
*   **Backend Framework:** Python Flask.
*   **Database (Relational + Vector):** 
    *   Pure local `SQLite` for structured data (ledger, events).
    *   `sqlite-vec` extension for lightweight, entirely local vector search and RAG storage.
*   **Document Parsing:** `Docling` (by IBM) – specifically chosen for its ability to accurately parse complex, table-heavy scanned documents (like bank statements and notary acts) into clean Markdown.
*   **Embedding Model:** `fastembed` (by Qdrant) running `BAAI/bge-small-en-v1.5`, 384 dimensions.
    *   Chosen over `sentence-transformers` because it runs on ONNX Runtime instead of PyTorch (~200MB install vs. ~2GB), better matching the "ultra-light, no bloat" principle above, while offering better retrieval quality than the smaller `all-MiniLM-L6-v2` model.
    *   **Always local, regardless of which LLM provider is configured for chat.** This is a deliberate architectural separation: the embedding model and the chat LLM are independent, separately-swappable concerns. Embeddings never call out to OpenRouter, Ollama, or any other provider — only the LLM chat step does. This keeps the vector space stable if the user switches LLM providers later, and avoids sending entire document contents to a third party on every upload (only the top-k retrieved chunks for a given question ever reach the LLM, at chat time — see Section 4.3).
*   **LLM Interaction:** Raw, direct Python requests (no SDKs, no LangChain) to any OpenAI-compatible `/v1/chat/completions` endpoint (Ollama, LM Studio, OpenRouter, OpenAI, etc.), configured by the user in the Settings Hub (Section 4.4).

## 4. Core Modules & Features

### 4.1. The Asset & Liability Ledger (Accounting & CRM)
This is the central workspace of the application. It acts as the executor's daily dashboard.
*   **Assets:** Track items to sell or distribute (e.g., House, Vehicles, Accounts), including estimated values, sale prices, and current status.
*   **Liabilities & Bills:** Track incoming bills (funeral home, utilities), amounts due, and deadlines. 
*   **Timeline / CRM:** Log all communications, calls, and meetings with estate stakeholders (notaries, government agents).
*   **Integration:** Every entry allows for direct PDF/document attachments, which automatically triggers the RAG ingestion pipeline.

### 4.2. Document Ingestion Pipeline (RAG System)
A streamlined, background process to make unstructured documents searchable.
*   **Workflow:**
    1.  User uploads a PDF (e.g., a scanned hydro bill) attached to a ledger entry.
    2.  `Docling` extracts the document, maintaining table structures and formatting it into Markdown.
    3.  The system chunks the Markdown text and generates vector embeddings.
    4.  Vectors are saved into the `sqlite-vec` database.
    5.  **Crucial Step:** Vectors are explicitly tagged with metadata linking them back to their specific ledger entry ID.

### 4.3. The LLM Chat Agent
The conversational interface for querying the estate file.
*   **Functionality:** The user can ask complex questions ("What were the exact itemized costs on the funeral home bill?", "What is the official date of death?").
*   **RAG Execution:** The system converts the query to a vector, searches `sqlite-vec` for the most relevant document chunks, and passes those chunks + the user prompt to the active LLM.
*   **Traceability:** The UI will display the LLM's answer alongside links to the specific ledger entries or documents it used to generate the answer.

### 4.4. The Settings Hub
A dedicated interface ensuring the application remains flexible and private.
*   **LLM Configuration:** 
    *   Inputs for `API Base URL`, `API Key`, and `Model Name`.
    *   Allows seamless switching between local models (e.g., via Ollama/LM Studio), aggregator APIs (OpenRouter), or direct providers (OpenAI/Anthropic).
*   **Knowledge Base Manager:** 
    *   A dashboard to monitor the status of parsed and ingested documents.
    *   Allows bulk uploading of historical estate PDFs that are not tied to a specific new ledger entry.
    *   Options to re-index or delete vectors.

## 5. Non-Functional Requirements
*   **Performance:** UI interactions and database saves should be near-instantaneous. Document parsing (Docling) may take a few seconds but should not block the main UI thread.
*   **Security & Privacy:** Because estate data contains highly sensitive PII (Social Insurance Numbers, banking details), the system must be completely local-first. Vector data must never leave the machine unless the user explicitly configures a cloud LLM provider.
*   **Extensibility:** The document parser and embedding modules must be modular, allowing the developer to swap `Docling` for a newer tool in the future without rewriting the RAG logic.

## 6. Next Steps for Development
1.  **Database Design:** Define the exact SQLite tables for `assets`, `liabilities`, `events`, and the `sqlite-vec` setup.
2.  **UI Prototyping:** Map the Flask routes to the existing Jinja/Shadcn component library.
3.  **Pipeline Proof of Concept:** Write a standalone Python script testing the Docling -> sqlite-vec flow to ensure table structures are preserved.

## 7. Implementation Status

*   **Done:**
    *   Database schema (Section 6.1) — `assets`, `liabilities`, `events`, `documents`, `settings`, plus `doc_chunks`/`doc_chunk_meta` for vectors.
    *   Ledger CRUD — Assets, Liabilities, Timeline (Section 4.1), server-rendered with search/filter/sort/pagination.
    *   Docling → chunk → embed → sqlite-vec ingestion pipeline (Section 4.2), synchronous on upload.
    *   LLM Chat Agent (Section 4.3) with source-chunk traceability, tested end-to-end against a local Ollama model.
    *   Settings Hub LLM configuration (Section 4.4) — base URL / API key / model name, tested with Ollama.
*   **Not yet done:**
    *   Attaching documents directly to a specific ledger entry (Section 4.1 "Integration" and 4.2 step 5 — `linked_entity_type`/`linked_entity_id` columns exist in the schema but nothing in the UI sets them yet; all uploads today are unlinked/standalone).
    *   Knowledge Base Manager bulk upload / re-index / delete UI (Section 4.4) — basic per-document upload, re-index, and delete exist on the Documents page, but there's no dedicated bulk-upload flow yet.
    *   Background ingestion so large PDFs don't block the request (Section 5, Performance) — ingestion currently runs synchronously in the request; acceptable for the single-PDF, few-seconds case tested so far, but not yet validated against large or complex scanned documents.
    *   Testing against real-world scanned/complex PDFs (multi-page, low-quality scans, mixed layouts) — validated so far only against one clean, digitally-generated test PDF with a simple table.
