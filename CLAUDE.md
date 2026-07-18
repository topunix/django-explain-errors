# CLAUDE.md

Project context for Claude Code sessions on django-explain-errors.

## What this package is

Django middleware (`explain_errors.ExplainErrorsMiddleware`) that captures
unhandled exceptions in local development and calls the OpenAI API to print
a plain-language explanation to stdout. Active only when `DEBUG=True`.

## Layout

- `explain_errors/rag/`: opt-in RAG layer (store.py, indexer.py, retriever.py)
- `explain_errors/management/commands/build_error_index.py`: index build command
- `explain_errors/middleware.py`: the middleware (sync + async paths)
- `explain_errors/sanitize.py`: `sanitize_traceback()` — redacts secrets/PII
  before a traceback leaves the process
- `explain_errors/throttle.py`: `SlidingWindowThrottle` — in-process, per-worker
  call-rate limiter used by the middleware
- `tests/`: unittest-based suite (Django `SimpleTestCase`,
  `IsolatedAsyncioTestCase`, `unittest.mock`, `override_settings`)
- `setup.py`: packaging metadata
- `.github/workflows/publish.yml`: PyPI Trusted Publishing (OIDC) on release

## Commands

- Run tests: `DJANGO_SETTINGS_MODULE=test_settings python -m django test tests -v 2`
- All existing tests (28) must pass before any commit. Never delete or
  weaken an existing test to make a change pass.

## Invariants (do not break)

1. Middleware must remain both sync and async capable:
   `async_capable = True`, `sync_capable = True`, coroutine detection via
   `asyncio.iscoroutinefunction`, async bridging via
   `asgiref.sync.sync_to_async`.
2. No behavior when `DEBUG=False`. The middleware must be inert in
   production.
3. OpenAI API key resolution order: `OPENAI_API_KEY` env var, then
   `settings.OPENAI_API_KEY`. Never log or print the key.
4. Existing settings must keep working unchanged: `OPENAI_MODEL`,
   `OPENAI_TIMEOUT`, `OPENAI_MAX_TOKENS`, `OPENAI_MAX_TRACEBACK_CHARS`.
RAG settings (all optional, see docs/rag-design.md):
- `EXPLAIN_ERRORS_RAG_ENABLED` (default False)
- `EXPLAIN_ERRORS_RAG_INDEX_PATH`, `EXPLAIN_ERRORS_RAG_TOP_K`,
  `EXPLAIN_ERRORS_RAG_EMBED_MODEL`, `EXPLAIN_ERRORS_RAG_INCLUDE`,
  `EXPLAIN_ERRORS_RAG_EXCLUDE`, `EXPLAIN_ERRORS_RAG_MAX_PROMPT_CHARS`
5. New features must be opt-in via settings flags. Default behavior for
   existing users must not change.
6. Supported: Python 3.9+, Django 4.2+.
7. All externally transmitted or persistently indexed traceback text must
   pass through `explain_errors.sanitize.sanitize_traceback()`. This
   includes the future RAG index build. Apply it to the truncated payload
   (post `OPENAI_MAX_TRACEBACK_CHARS`), not the raw traceback.
8. Rate limiting is per-worker-process only (`SlidingWindowThrottle`, no
   cross-process coordination). Settings:

   | Setting | Default | Purpose |
   |---|---|---|
   | `EXPLAIN_ERRORS_MAX_CALLS` | `5` | Max explanations per window |
   | `EXPLAIN_ERRORS_WINDOW_SECONDS` | `60` | Window length |
   | `EXPLAIN_ERRORS_REDACT_PATTERNS` | `[]` | Extra regex strings, appended after defaults |
   | `EXPLAIN_ERRORS_REDACT_REPLACEMENT` | `"[REDACTED]"` | Replacement token |
   | `EXPLAIN_ERRORS_REDACT_DISABLE_DEFAULTS` | `False` | Escape hatch; user patterns only |

9. RAG is opt-in and default-off. With `EXPLAIN_ERRORS_RAG_ENABLED=False`,
   the prompt must remain byte-identical to the traceback-only prompt.
10. All text derived from tracebacks or project source must pass through
   `explain_errors.sanitize.sanitize_traceback()` before being embedded,
   stored in the index, or sent to any API.
11. sqlite-vec is an optional dependency (`[rag]` extra). Core imports must
   never require it; RAG failures log one warning and fall back to the
   traceback-only prompt. RAG tests skip when it is absent.

## Resolved: `api_called` scope

Previously an open question (single-explanation-per-worker flag with
undecided scope). Resolved 2026-07-17: removed in favor of
`SlidingWindowThrottle` — intended behavior is "limit API spend," not
"explain exactly one error per worker lifetime." See
`docs/hardening-design.md.` for the full rationale.

## Style

- Standard library `unittest`, not pytest.
- Mock all OpenAI calls in tests. No network calls in the test suite.
- Keep dependencies minimal; anything heavy must be an optional extra in
  `setup.py` (e.g. `pip install django-explain-errors[rag]`).

## Releases

Tag `vX.Y.Z` on main. GitHub Actions publishes to PyPI via Trusted
Publishing (OIDC). No API tokens are stored; do not add any.
