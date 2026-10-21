# Changelog

## Unreleased

### Fixed

- OpenAI reasoning models (for example `gpt-6-luna`) reject `max_tokens`, so no explanation was generated. The middleware still sends `max_tokens` by default; if the API rejects that parameter, it retries once with `max_completion_tokens` and uses it for later calls.
- An empty or `None` model response (for example, reasoning consuming the whole token budget) is now reported with the usual "Failed to get an explanation" message instead of printing `None` or showing a blank banner on the debug page. After the `max_completion_tokens` fallback, the truncation warning notes that reasoning tokens count against `OPENAI_MAX_TOKENS`.

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
