# Task: OPENAI_BASE_URL support via shared client factory

One-off task spec. Execute, then delete or archive this file.

## Context

Support any OpenAI-compatible endpoint (Ollama, LM Studio, gateways)
via a configurable base URL. There are currently two independent client
construction sites that must not drift:

- `explain_errors/middleware.py`, `__init__`: key resolution then
  `self.openai_client = OpenAI(api_key=openai_api_key, timeout=timeout)`
- `explain_errors/rag/indexer.py`, `get_openai_client()` (around line
  65): lazy `from openai import OpenAI`, same key resolution, returns
  `OpenAI(api_key=api_key)`

Consolidate into one factory and add `OPENAI_BASE_URL`.

New config: `OPENAI_BASE_URL`. Resolution order matches the API key:
env var first, then `settings.OPENAI_BASE_URL`, default `None`.
Default behavior (unset) must be unchanged.

## Files to change

- `explain_errors/client.py` (new)
- `explain_errors/middleware.py`
- `explain_errors/rag/indexer.py`
- `tests/test_client.py` (new)
- `README.md`, `CLAUDE.md`

## Implementation

1. Create `explain_errors/client.py`:

       import os

       from django.conf import settings
       from dotenv import load_dotenv, find_dotenv

       PLACEHOLDER_API_KEY = "sk-no-key-required"

       def get_openai_client(timeout=None):
           """Build the OpenAI client used by the middleware and the
           RAG indexer. Honors OPENAI_BASE_URL for OpenAI-compatible
           servers (Ollama, LM Studio, gateways)."""
           load_dotenv(find_dotenv(usecwd=True))
           from openai import OpenAI

           base_url = os.getenv(
               "OPENAI_BASE_URL", getattr(settings, "OPENAI_BASE_URL", None)
           )
           api_key = os.getenv(
               "OPENAI_API_KEY", getattr(settings, "OPENAI_API_KEY", None)
           )
           if not api_key:
               if base_url:
                   # Local OpenAI-compatible servers ignore the key but
                   # the SDK requires one.
                   api_key = PLACEHOLDER_API_KEY
               else:
                   raise ValueError(
                       "OpenAI API key not found. Please set the "
                       "OPENAI_API_KEY environment variable."
                   )
           kwargs = {"api_key": api_key}
           if base_url:
               kwargs["base_url"] = base_url
           if timeout is not None:
               kwargs["timeout"] = timeout
           return OpenAI(**kwargs)

   The ValueError message must stay exactly as above; existing tests
   assert on it. Never log or print the key (invariant 3).

2. `explain_errors/middleware.py`: in `__init__`, delete the
   `load_dotenv(...)`, the `openai_api_key = os.getenv(...)` block, the
   `if not openai_api_key: raise ValueError(...)` block, and the
   `self.openai_client = OpenAI(api_key=..., timeout=timeout)` line.
   Replace with:
       self.openai_client = get_openai_client(timeout=timeout)
   Keep the `self.model`, `self.max_tokens`, and `timeout` lines.
   Remove now-unused imports (`OpenAI`, `load_dotenv`, `find_dotenv`,
   possibly `os`) only if nothing else in the module uses them.

3. `explain_errors/rag/indexer.py`: reduce `get_openai_client()` to a
   delegation, keeping the name so existing imports in tests keep
   working:
       from ..client import get_openai_client  # at top, or:
   Alternatively keep the local function and have its body
   `return _shared_get_openai_client()`. Either is fine; do not change
   the public name `explain_errors.rag.indexer.get_openai_client`.

4. Do not add base URL handling anywhere else. The embedding model
   override already exists (`EXPLAIN_ERRORS_RAG_EMBED_MODEL`); base_url
   plus that setting is the full local-model story.

## Tests to add (tests/test_client.py)

Patch `openai.OpenAI` (or the symbol as imported inside the factory)
and assert constructor kwargs. Use `override_settings` and
`unittest.mock.patch.dict(os.environ, ...)` as done elsewhere in the
suite.

- `test_default_no_base_url_kwarg`: key set, base URL unset; client
  called with api_key and no base_url key in kwargs
- `test_base_url_from_settings_passed_to_client`
- `test_base_url_env_var_takes_precedence_over_settings`
- `test_missing_key_with_base_url_uses_placeholder`: no key anywhere,
  base URL set; no ValueError; api_key equals the placeholder
- `test_missing_key_without_base_url_raises`: exact existing message
- `test_timeout_forwarded_when_provided`
- `test_middleware_uses_factory`: patch
  `explain_errors.middleware.get_openai_client`, init middleware with
  DEBUG=True, assert it was called with the resolved timeout
- `test_indexer_uses_factory`: same idea for the indexer path

## Docs

- README Configuration table, new row:
  `OPENAI_BASE_URL` (env or settings) | No | Base URL for any
  OpenAI-compatible API (for example Ollama at
  `http://localhost:11434/v1`). When set, a missing API key is replaced
  with a placeholder since local servers do not require one.
- README: short new subsection "Using local models (Ollama)" after the
  Configuration table, with a settings example:
      OPENAI_BASE_URL = "http://localhost:11434/v1"
      OPENAI_MODEL = "llama3.1"
      EXPLAIN_ERRORS_RAG_EMBED_MODEL = "nomic-embed-text"
  Include a note that the RAG index must be rebuilt after changing the
  embedding model or provider, since stored vectors are model-specific.

## CLAUDE.md (durable changes only)

- Invariant 3 becomes: key resolution order unchanged; add that
  `OPENAI_BASE_URL` follows the same env-then-settings order, and that
  all OpenAI clients must be constructed via
  `explain_errors.client.get_openai_client` (no direct `OpenAI(...)`
  calls elsewhere).
- Add `OPENAI_BASE_URL` to the settings that must keep working.
- Update the test count in the Commands section.

## Done when

- `DJANGO_SETTINGS_MODULE=test_settings python -m django test tests -v 2`
  passes with zero failures, no existing test modified or deleted
- `grep -rn "OpenAI(" explain_errors/ | grep -v client.py` returns
  nothing
- With no base URL configured, constructed client kwargs are identical
  to current main
