# RAG Layer Design Spec

Task: add an opt-in RAG layer that grounds OpenAI error explanations in
the user's actual project code. Implement exactly as specified below.

## Goal

Today the explanation prompt contains only the traceback. With RAG
enabled, the prompt also includes the most relevant source code chunks
from the user's project, retrieved from a local vector index at exception
time. Explanations become codebase-aware instead of generic.

## Module layout

    explain_errors/
        rag/
            __init__.py
            store.py       # vector store wrapper (sqlite-vec)
            indexer.py     # file discovery, chunking, embedding
            retriever.py   # traceback frame extraction + top-k retrieval
        management/
            commands/
                build_error_index.py

## Vector store

Use sqlite-vec (single-file DB, no server, no heavy deps). Declare
`sqlite-vec` and the embedding client under an optional extra:
`django-explain-errors[rag]`. Core install stays dependency-light.

Index location: `settings.EXPLAIN_ERRORS_RAG_INDEX_PATH`, defaulting to
`<BASE_DIR>/.explain_errors_index.db`. Add the default filename to the
suggested .gitignore entry in the README.

## Settings API (all new, all optional)

    EXPLAIN_ERRORS_RAG_ENABLED = False        # master switch, default off
    EXPLAIN_ERRORS_RAG_INDEX_PATH = None      # see default above
    EXPLAIN_ERRORS_RAG_TOP_K = 4              # chunks injected into prompt
    EXPLAIN_ERRORS_RAG_EMBED_MODEL = "text-embedding-3-small"
    EXPLAIN_ERRORS_RAG_INCLUDE = None         # list of dirs; default: BASE_DIR
    EXPLAIN_ERRORS_RAG_EXCLUDE = [...]        # migrations, venvs, node_modules,
                                              # static, media, .git by default

## Indexing (build_error_index command)

1. Walk `EXPLAIN_ERRORS_RAG_INCLUDE` dirs, filter by exclude list, take
   `.py`, `.html`, `.txt` template and source files.
2. Chunk Python files by top-level function/class using `ast`, falling
   back to fixed-size line windows (80 lines, 20 overlap) for other files.
3. Store per chunk: file path, start line, end line, chunk text,
   embedding vector. Sanitize chunk text with `sanitize_traceback()`
   before embedding and storage, so secrets in source files never
   reach the API or the index.
4. Embed via the OpenAI embeddings API using the existing API key
   resolution logic. Batch requests.
5. Command is idempotent: rebuilding replaces the index atomically
   (write to temp file, rename).
6. Print a summary: files scanned, chunks embedded, index path.

## Retrieval at exception time

1. In `process_exception`, if `EXPLAIN_ERRORS_RAG_ENABLED` and the index
   file exists: parse the traceback, extract file paths and line numbers
   of frames inside the project (skip site-packages and stdlib frames).
2. Build the retrieval query from the exception type, message, and the
   source lines of the innermost project frame.
2b. Pass the retrieval query through
    `explain_errors.sanitize.sanitize_traceback()` before embedding.
    Nothing derived from a traceback leaves the process unsanitized.
3. Embed the query, retrieve top-k chunks, deduplicate by file path.
4. Inject into the prompt under a clearly delimited section:
   "Relevant project source:" with file path and line range headers per
   chunk. Respect `OPENAI_MAX_TRACEBACK_CHARS` style truncation with a
   new combined character budget.
5. Fallback rules (must never raise):
   - RAG disabled, index missing, sqlite-vec not installed, or any
     retrieval error: log a single warning and proceed with the current
     traceback-only prompt.

## Async path

Retrieval involves an embeddings API call. In the async middleware path,
wrap the entire retrieve step with `sync_to_async`, mirroring how the
existing explanation call is bridged. No event-loop blocking.

## Tests (new file: tests/test_rag.py)

Mock all OpenAI calls (embeddings and chat). Cover at minimum:

1. Indexer: chunking of a sample module by function/class boundaries;
   exclude-list filtering; idempotent rebuild.
2. Retriever: traceback frame extraction skips non-project frames;
   top-k retrieval returns expected chunks from a fixture index.
3. Middleware integration, RAG enabled: prompt sent to the chat API
   contains the retrieved chunk text.
4. Middleware integration, RAG disabled (default): prompt is byte-for-byte
   the current traceback-only prompt. Zero behavior change.
5. Fallbacks: missing index file, sqlite-vec import failure, and
   retrieval exception each fall back cleanly and still produce an
   explanation.
6. Async: RAG retrieval works under `IsolatedAsyncioTestCase` with an
   async get_response chain.

All 28 existing tests must remain green and unmodified.

## README updates

Add a "Codebase-aware explanations (RAG)" section: install extra,
settings table, `build_error_index` usage, .gitignore note, and a
before/after example of an explanation.

## Out of scope

- Indexing third-party package source or Django docs (future work)
- ChromaDB backend (sqlite-vec only for now)
- Auto-reindexing on file change
