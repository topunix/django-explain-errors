# Django Explain Errors Middleware

This Django middleware captures unhandled errors and exceptions, sends them
to a language model for explanation, and prints the explanation to stdout
when debug mode is enabled. It works with the OpenAI API out of the box, with
Anthropic's Claude models through Anthropic's OpenAI-compatible endpoint, and
with any other OpenAI-compatible endpoint (Ollama, LM Studio, Azure, or a
corporate gateway) by setting a base URL, so explanations can run entirely on
a local model if you prefer not to send code off your machine.

It can optionally ground explanations in your own project source using a
local vector index (RAG), so explanations reference the actual code that
failed instead of staying generic.

The middleware supports both synchronous (WSGI) and asynchronous (ASGI)
views. It auto-detects the view chain at startup and routes requests through
the matching path, so no extra configuration is required for either server
type. Tracebacks are sanitized before leaving the process, and API calls are
rate limited.

## Scope

This package explains errors for a person, not for a coding agent to consume
programmatically. The explanation is written for a human reader, in a terminal or an editor's
integrated terminal, and the output format assumes that reader.

If a coding agent is doing the debugging, it does not need this. Agents read tracebacks directly,
and tools that expose live runtime state (debugger-over-MCP servers, `mcp-django`) serve that case
better. This package is not trying to compete there.

Local development only. It requires `DEBUG = True` and is inert otherwise.

## Features

- Captures Django errors and exceptions
- Explains errors using OpenAI, Anthropic's Claude models, or any other
  OpenAI-compatible endpoint (Ollama, LM Studio, Azure, gateways) via `OPENAI_BASE_URL`
- Optional codebase-aware explanations (RAG) backed by a local sqlite-vec index (see the RAG section below)
- Redacts secrets, tokens, and emails from tracebacks before sending
- Rate limits API calls with a configurable sliding window
- Works with both sync (WSGI) and async (ASGI) views
- Securely manages the OpenAI API key using environment variables

## Installation

1. Install django-explain-errors by running:
```bash
pip install django-explain-errors
```

2. **Add the middleware to your Django project**:

   - Open your `settings.py` file and add the middleware to the `MIDDLEWARE` list:

     ```python
     MIDDLEWARE = [
         ...
         'explain_errors.middleware.ExplainErrorsMiddleware',
     ]
     ```

     In the default preserve mode, `process_exception` returns `None`, so exception handling
     continues normally no matter where the middleware sits in the list — it no longer needs
     to be last to avoid pre-empting other packages' error handling. It still needs to sit
     close enough to the view that unhandled exceptions actually reach it, before any other
     middleware that might catch and handle them itself. If you set
     `EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE=False`, keep it last: returning a response there still
     ends exception handling early and pre-empts anything above it in the stack (Debug Toolbar,
     Sentry, Rollbar — see Compatibility below).

3. **Set up environment variables**:

   - Create a `.env` file in your project's root directory and add your OpenAI API key. Alternatively, you can set the API key in `settings.py`:

     ```plaintext
     OPENAI_API_KEY=your_openai_api_key_here
     ```

   The API key is not required if you set `OPENAI_BASE_URL` to a local
   server such as Ollama, which does not authenticate requests.

## Usage

1. **Ensure DEBUG is set to True**:

   Open your `settings.py` file and set:

   ```python
   DEBUG = True
   ```

2. **Trigger an error in your Django application**:

   The middleware captures the error, sends it to OpenAI for explanation, and prints the explanation to stdout. By default (`EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE=True`), it then lets exception handling continue normally, so Django (or whatever else is watching, such as `runserver_plus` or Sentry — see Compatibility below) renders exactly what it would without this middleware installed. Set `EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE = False` to instead get a JSON `500` response containing the error message and the explanation.

## Async Support

The middleware exposes both `sync_capable = True` and `async_capable = True`. At initialization it inspects `get_response` to decide whether it is part of a sync or async chain:

- Under WSGI (for example `runserver` with sync views), requests flow through the synchronous handler.
- Under ASGI (for example with async views), requests are awaited through the async handler. The blocking OpenAI call is offloaded with `asgiref.sync.sync_to_async` so the event loop is not blocked.

No additional settings are needed. See Installation above for where to place the middleware in `MIDDLEWARE`.

## Compatibility

How this middleware interacts with other error-handling and debugging tools, in the default
preserve mode and with `EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE=False`:

| Package | Preserve mode (default) | `EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE=False` |
| ------- | ------------------------ | ------------------------------------------- |
| Django Debug Toolbar | Works — Django renders its normal debug page, and Debug Toolbar injects into it. | Broken — the JSON 500 response has no HTML to inject into. |
| Sentry, Rollbar | Work — the exception propagates and Django re-raises it, so `got_request_exception` fires. | Broken — returning a response ends exception handling before `got_request_exception` fires. |
| Django REST Framework | Partial — only exceptions DRF does not already handle itself reach this middleware. | Partial, same reason. |
| Silk and other profiling panels | Timings are inflated by the OpenAI call, since `process_exception` blocks the request path. | Same. |
| CORS, GZip, WhiteNoise | No interaction. | No interaction. |
| `runserver_plus` / Werkzeug debugger | Works — returning `None` re-raises the original exception, and `django-extensions` replaces Django's debug-page renderer with one that re-raises instead, so Werkzeug's WSGI wrapper catches it and shows the interactive debugger. | Broken — the JSON 500 response ends exception handling before it reaches `runserver_plus`'s exception hook, so the Werkzeug debugger never appears. |

Debug Toolbar, Sentry, and Werkzeug were verified empirically, on both Django 4.2 and Django
6.1, in both modes. DRF, Silk, and CORS/GZip/WhiteNoise are reasoned from the mechanism
rather than tested.

One additional behavior worth knowing, also verified on both Django versions: when the
explanation call itself fails (a bad key, a timeout, an unreachable endpoint), a
Sentry-instrumented project captures a separate event for that failure. `explain_errors`
catches the exception internally, so it never becomes an unhandled exception, but Sentry's
`httpx` integration captures it anyway by instrumenting inside the HTTP client library rather
than relying on `got_request_exception`. That event is unrelated to whatever error the
developer is actually investigating.

## Configuration

| Setting / variable | Required | Description |
| ------------------ | -------- | ----------- |
| `OPENAI_API_KEY` (env or settings) | Yes, when `DEBUG=True` | API key used to authenticate with OpenAI. Read first from the environment, then from `settings`. |
| `DEBUG` | Yes | The middleware is only active when `DEBUG=True`. When `False`, requests pass through untouched. |
| `OPENAI_MODEL` | No | Model used for explanations. Defaults to `gpt-4o-mini`. |
| `OPENAI_MAX_TOKENS` | No | Ceiling on tokens generated for the explanation, not a target — the system prompt itself asks for a concise answer. Defaults to `1000`. |
| `OPENAI_TIMEOUT` | No | Request timeout in seconds for the OpenAI client. Defaults to `10`. |
| `OPENAI_MAX_TRACEBACK_CHARS` | No | Traceback is trimmed to its last N characters before being sent. Defaults to `3000`. |
| `EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE` | No | When `True` (the default), the middleware prints the explanation to stdout and returns `None`, so exception handling continues normally and Django renders its standard debug page. Set to `False` to instead return a JSON 500 response, which ends exception handling early (see Compatibility above). |
| `OPENAI_BASE_URL` (env or settings) | No | Base URL for any OpenAI-compatible API (for example Ollama at `http://localhost:11434/v1`). When set, a missing API key is replaced with a placeholder since local servers do not require one. |

## Using local models (Ollama)

Point `OPENAI_BASE_URL` at any OpenAI-compatible server to run explanations
against a local model instead of the OpenAI API:

```python
OPENAI_BASE_URL = "http://localhost:11434/v1"
OPENAI_MODEL = "llama3.1"
EXPLAIN_ERRORS_RAG_EMBED_MODEL = "nomic-embed-text"
```

With a local endpoint, no traceback or source code leaves your machine,
which matters if you work somewhere that cannot send code to a
third-party API.

If you use the RAG layer, rebuild the index after changing the embedding
model or provider. Stored vectors are model-specific.

## Using Anthropic (Claude) models

Anthropic's Claude models work today through Anthropic's OpenAI-compatible API, with no
Anthropic-specific code required:

```python
OPENAI_BASE_URL = "https://api.anthropic.com/v1/"
OPENAI_API_KEY = "your_anthropic_api_key_here"
OPENAI_MODEL = "..."  # see Anthropic's current model list below
```

`OPENAI_MODEL` is mandatory here: the default (`gpt-4o-mini`) doesn't exist on Anthropic's API
and will 404. Use one of the model names from
[Anthropic's model overview](https://docs.anthropic.com/en/docs/about-claude/models/overview);
model names are versioned and retired over time, so check that page rather than relying on a
name pinned here.

Anthropic documents this compatibility layer as intended primarily for testing and comparing
model capabilities, not as a production integration path. That's an acceptable tradeoff for a
development-only middleware, but worth knowing going in.

Reasoning models behind `OPENAI_BASE_URL` spend part of the token budget on internal reasoning
before producing visible output. At a low `OPENAI_MAX_TOKENS`, the budget can be used up by
reasoning alone, and the explanation comes back empty. Raise `OPENAI_MAX_TOKENS` if you see this.

### A note on API keys and 401s

`explain_errors` reads `OPENAI_API_KEY` (see Configuration above); it does not read
provider-specific variables such as `ANTHROPIC_API_KEY`. If no key is found and
`OPENAI_BASE_URL` is set, the client substitutes a placeholder key rather than raising an
error — a convenience for local servers like Ollama or LM Studio, which ignore the key
entirely. Against a real remote endpoint such as Anthropic's, that placeholder is sent as-is
and rejected, so a missing `OPENAI_API_KEY` shows up as an opaque `401 Unauthorized` rather
than a clear configuration error. If you see a 401 with `OPENAI_BASE_URL` pointed at a remote
provider, check that `OPENAI_API_KEY` — not a provider-specific variable — is actually set.

## Codebase-aware explanations (RAG)

By default, explanations are generated from the traceback alone. With the
optional RAG (retrieval-augmented generation) layer enabled, the middleware
also retrieves the most relevant chunks of your own project's source code
from a local vector index and includes them in the prompt, so explanations
can reference your actual functions and classes instead of guessing at them.

This feature is opt-in and adds no dependencies or behavior unless enabled.

### Install the extra

```bash
pip install django-explain-errors[rag]
```

This pulls in [sqlite-vec](https://github.com/asg017/sqlite-vec), a
single-file, no-server vector store. The core package stays dependency-light
if you don't need RAG.

### Build the index

Add `explain_errors` to `INSTALLED_APPS` (needed for Django to discover the
management command), then run:

```bash
python manage.py build_error_index
```

This walks your project, chunks Python files by top-level function/class
(and other text files by fixed-size line windows), embeds each chunk with
the OpenAI embeddings API, and writes them to a local index file. Re-run it
whenever your source changes meaningfully. Indexing is not automatic.
Rebuilding is idempotent: it builds into a temp file and atomically replaces
the previous index.

### Enable it

```python
# settings.py

EXPLAIN_ERRORS_RAG_ENABLED = True
```

### Settings

| Setting | Default | Description |
| ------- | ------- | ----------- |
| `EXPLAIN_ERRORS_RAG_ENABLED` | `False` | Master switch for the RAG layer. |
| `EXPLAIN_ERRORS_RAG_INDEX_PATH` | `<BASE_DIR>/.explain_errors_index.db` | Path to the local vector index file. |
| `EXPLAIN_ERRORS_RAG_TOP_K` | `4` | Number of chunks retrieved and injected into the prompt. |
| `EXPLAIN_ERRORS_RAG_EMBED_MODEL` | `"text-embedding-3-small"` | OpenAI embedding model used for indexing and retrieval. |
| `EXPLAIN_ERRORS_RAG_INCLUDE` | `None` (defaults to `BASE_DIR`) | List of directories to index. |
| `EXPLAIN_ERRORS_RAG_EXCLUDE` | migrations, venvs, `node_modules`, static, media, `.git` | Directory names to skip while indexing. |
| `EXPLAIN_ERRORS_RAG_MAX_PROMPT_CHARS` | `6000` | Combined character budget for the traceback + retrieved source sections of the prompt. |

Every chunk of source code and every retrieval query is passed through the
same `sanitize_traceback()` redaction used for tracebacks, which strips
patterns that look like secrets, tokens, and emails before anything is sent
to OpenAI or written to the index. It's a pattern-based filter, not a
guarantee: it catches recognizable secret shapes, not arbitrary sensitive
data that doesn't match one.

If RAG is enabled but the index is missing, `sqlite-vec` isn't installed, or
retrieval fails for any reason, the middleware logs a warning and falls back
to the traceback-only prompt. It never breaks error reporting.

RAG-grounded explanations tend to be longer than traceback-only ones. The default `OPENAI_MAX_TOKENS` already leaves generous headroom for this, but if you've lowered it, raise it back up when RAG is enabled so explanations are not truncated.

### .gitignore

The index file is a local build artifact, not something to commit. Add it
to your project's `.gitignore`:

```
.explain_errors_index.db
```

(Adjust the path if you set `EXPLAIN_ERRORS_RAG_INDEX_PATH` to something
else.)

### Before / after

**Without RAG**, traceback only:

> Your `ValueError` is raised because the value passed to `foo()` couldn't
> be converted to an integer. Check where `foo()` is called and make sure
> you're passing a numeric string.

**With RAG**, grounded in the actual function:

> In `myapp/utils.py`, `foo()` calls `int(value)` on line 12 without a
> `try`/`except`, so any non-numeric `value` raises `ValueError` straight
> through to the caller. Since `foo()` is called from `myapp/views.py` with
> unvalidated form input, add validation there or wrap the `int()` call in
> `foo()` with a clear error message.

## Example

Here is an example of how to use the middleware in a Django project:

```python
# settings.py

DEBUG = True

MIDDLEWARE = [
    ...
    'explain_errors.middleware.ExplainErrorsMiddleware',
]

# .env

OPENAI_API_KEY=your_openai_api_key_here
```

When an error occurs, you will see an explanation printed to stdout.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! Please open an issue or submit a pull request for any improvements or bug fixes.

## Acknowledgements

- [Django](https://www.djangoproject.com/)
- [OpenAI](https://www.openai.com/)
- [python-dotenv](https://github.com/theskumar/python-dotenv)
