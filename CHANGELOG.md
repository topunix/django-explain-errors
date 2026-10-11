# Changelog

## 0.11.0

### Added

- `EXPLAIN_ERRORS_RAG_EMBED_BASE_URL` and `EXPLAIN_ERRORS_RAG_EMBED_API_KEY`, which let the embeddings provider differ from the chat provider.
- Local embeddings backend: `EXPLAIN_ERRORS_RAG_EMBED_BACKEND = "local"`, installed with the `local-embeddings` extra, defaulting to the `all-MiniLM-L6-v2` model.
- Index metadata (backend, model, dimension) with a mismatch warning and fallback to the traceback-only prompt.
- A clear `EmbeddingsNotSupportedError` when Anthropic is used for embeddings.
- README Privacy section.
- Manual workflow for testing against real embeddings.

### Changed

- An embeddings key is now required when the embeddings URL differs from the chat URL.
- README reorganized: features list, section order, and Anthropic RAG options.

### Fixed

- The redactor now catches suffixed secret names (`SECRET_KEY`, `AWS_SECRET_ACCESS_KEY`), quoted dict keys, whole quoted values, URL credentials, and common API key shapes, with an allowlist for non-secret Django settings such as `AUTH_USER_MODEL`.
- Production Safety no longer claims that local variables are sent.
- The test suite works with openai 3.x (httpx2).
