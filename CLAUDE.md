# CLAUDE.md

Project context for Claude Code sessions on django-explain-errors.

## What this package is

Django middleware (`explain_errors.ExplainErrorsMiddleware`) that captures
unhandled exceptions in local development and calls the OpenAI API to print
a plain-language explanation to stdout. Active only when `DEBUG=True`.

## Layout

- `explain_errors/middleware.py`: the middleware (sync + async paths)
- `tests/`: unittest-based suite (Django `SimpleTestCase`,
  `IsolatedAsyncioTestCase`, `unittest.mock`, `override_settings`)
- `setup.py`: packaging metadata
- `.github/workflows/publish.yml`: PyPI Trusted Publishing (OIDC) on release

## Commands

- Run tests: `DJANGO_SETTINGS_MODULE=test_settings python -m django test tests -v 2`
- All existing tests (13) must pass before any commit. Never delete or
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
5. New features must be opt-in via settings flags. Default behavior for
   existing users must not change.
6. Supported: Python 3.9+, Django 4.2+.

## Known open question

The `api_called` flag currently limits explanations to the first error per
worker process. Its intended scope is undecided. Do not silently change
this behavior; flag it if a task touches it.

## Style

- Standard library `unittest`, not pytest.
- Mock all OpenAI calls in tests. No network calls in the test suite.
- Keep dependencies minimal; anything heavy must be an optional extra in
  `setup.py` (e.g. `pip install django-explain-errors[rag]`).

## Releases

Tag `vX.Y.Z` on main. GitHub Actions publishes to PyPI via Trusted
Publishing (OIDC). No API tokens are stored; do not add any.
