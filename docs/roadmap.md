# django-explain-errors roadmap

## How to read this file
This file records decisions, not release state, and it is not guaranteed current.
Before acting on any item, check its "Verify" marker against `main`. If the marker is
present, the item has shipped and this file is stale; say so and skip it.

State sources:
- `main` itself, for whether a setting, module, or file exists
- PyPI: https://pypi.org/pypi/django-explain-errors/json (`info.version`)
- GitHub releases and tags: `topunix/django-explain-errors`

Deleting shipped items is housekeeping, not correctness. Doing it in the same PR that
ships the work keeps the file short; skipping it costs one lookup.

Items reference each other by name, never by number, so renumbering stays safe.
Task specs live in `docs/tasks/` in the repo, versioned, deleted after execution. They do
not live here and they do not live in `CLAUDE.md`.

## Positioning
The package is a Django learning aid for humans, not an error explainer for agents.
Explanations are grounded in the user's own code and (planned) the official Django docs.
"AI error explainer" is a commoditized category; "grounded Django learning aid" is not.
Sequencing below follows from that.

## Sequenced queue

1. **Eval harness**: fixture set of real tracebacks plus a scored RAG-on vs RAG-off
   comparison. Currently there is no regression guard on explanation quality, only on
   plumbing. Load-bearing for django-docs-links, explanation-levels, and debug page
   injection, not just RAG.
   Ships with cost and latency instrumentation. Latency is not hypothetical: `process_exception`
   blocks the request path today, so every 500 already waits on the API call. The numbers gate
   the debug page injection decision and tell you whether the blocking design needs replacing
   regardless of that item.
   Rejected shape: a harness that only checks the API returned something.
   Verify: an `evals/` or `tests/evals/` directory exists on `main`.

2. **django-docs-links**: cross-reference explanations to official Django documentation.
   Ship the cheap version first: a static map of exception type plus context to a docs
   anchor. No index, no embeddings, no build step. A real docs link is verifiable in a way
   generated prose is not, which matters most for the learning audience and shrinks the
   hallucination surface.
   Resolve before starting:
   - Django docs license terms for redistributing a derived map or index.
   - Version pinning. URLs carry a version segment, and serving 5.2 links to a user on 4.2
     is worse than no link.
   - Translated docs coverage, now that `EXPLAIN_ERRORS_LANGUAGE` has shipped.
     `docs.djangoproject.com` has translations with uneven coverage, so a localized link may
     404 or silently fall back.
   Verify: a docs-map module (for example `explain_errors/docs_links.py`) exists on `main`.

3. **README positioning rewrite**: after django-docs-links, when the learning-aid claim is
   backed by shipped behavior. Not before. The README documents shipped behavior; anything
   earlier is a promise that has to be kept.
   Verify: the README lead paragraph describes a grounded Django learning aid rather than an
   error explainer.

## Conditional or unscheduled

- **Debug page injection**: append the explanation into the debug page HTML rather than only
  stdout. Build only if the eval harness shows p50 latency low enough to tolerate.

  The placement argument is sound: when a 500 fires the developer is in the browser, not the
  terminal, and stdout requires a context switch to a console that may not be visible.

  The cost is narrower than it first appears. `process_exception` runs synchronously in the
  request path, so the debug page already does not render until the API call returns, in
  preserve mode as much as in default mode. That tax is paid today on every 500. Injection
  adds no new blocking; it only changes the destination of text the developer already waited
  for. What it adds is the expectation that the wait produced something worth reading on that
  surface, since text arriving in a terminal the developer is not looking at costs nothing
  when it is mediocre.

  Decision rule, using the eval harness latency instrumentation:
  - p50 around 1 to 2 seconds: build the blocking version. Simple and worth it.
  - p50 above roughly 5 seconds: the blocking request path is itself the problem, not
    injection specifically. Fix it at the source (async fill-in, where the page renders
    immediately with an empty panel filled from a dev-only view over fetch) or accept that
    the package is slow on every 500 regardless of destination. The async version costs one
    URL, one view, and some JS, and it fixes the underlying tax rather than only the
    injection case.

  Because the tax is already being paid, the eval harness latency numbers are load-bearing
  sooner than this item. They measure current behavior, not a hypothetical, and the generous
  token ceiling from preserve-mode-default raises them.

  Ship gated behind `EXPLAIN_ERRORS_INJECT_DEBUG_PAGE`, default `False`, whichever version is
  built. Usage then answers whether it was worth building.

  Only inject what stdout cannot already show. Identical text is redundant; explanations
  grounded in the user's own code via RAG are something the debug page genuinely lacks.

  Implementation is less fragile than it looks. `DEFAULT_EXCEPTION_REPORTER` and
  `ExceptionReporter.html_template_path` are supported extension points and the package
  already requires Django 4.2+, so the cheap version is a subclass plus a template override,
  not surgery.
  Verify: `EXPLAIN_ERRORS_INJECT_DEBUG_PAGE` appears in `explain_errors/` on `main`.

- **dedup-identical-errors**: LRU hash of exception type plus top frame, so repeated
  identical errors do not burn the sliding-window throttle. Small, slot in anywhere.
  Verify: an LRU or hash-based seen-errors cache exists in `explain_errors/middleware.py`.

- **explanation-levels**: `eli5` through `senior`. Build only if the eval harness shows the
  levels genuinely diverge. The real split is intent, not verbosity: a beginner wants the
  concept, a senior wants cause and fix in two lines. Build as distinct prompts, not a tone
  dial. Levels multiply the eval matrix, which is a second reason it waits.
  Verify: `EXPLAIN_ERRORS_LEVEL` appears in `explain_errors/middleware.py` on `main`.

- **CLAUDE.md self-maintenance**: have Claude Code propose its own `CLAUDE.md` updates at the
  end of each task instead of manual promotion. The stale test count encountered during
  v0.3.1 is exactly what this prevents. Process change, slot in anywhere.
  Verify: `CLAUDE.md` on `main` contains an instruction to propose updates to itself at task
  end.

- **Semantic retrieval over full Django docs**: same pipeline as the source-code index,
  pointed at a second corpus. Only if the static map in django-docs-links shows people want it.
  Verify: a second corpus or docs index target exists under `explain_errors/rag/` on `main`.

- **Local embeddings**: sentence-transformers behind an optional extra. Motivated by the
  privacy argument, not by adding PyTorch to a resume. Depends on `OPENAI_BASE_URL`, which is
  what makes fully-local operation coherent.
  Verify: an `extras_require` entry naming sentence-transformers exists in `setup.py`.

## Rejected (recorded so it does not resurface)
- **Editor extension or MCP server for IDE consumption.** VS Code, PyCharm, and Zed all have
  integrated terminals, so `runserver` output is already inside the editor. The browser, not
  the editor, is the surface a developer is on when a 500 fires. A TypeScript extension is a
  second distribution artifact with its own marketplace listing, release cadence, and
  compatibility surface, roughly doubling maintenance for a placement improvement. Debug page
  injection addresses the same problem without a second artifact. The only thing an extension
  would uniquely provide is a gutter marker on the failing `file:line`. Revisit only if that
  specific capability is repeatedly requested.
- **Dynamic `max_tokens` expansion at runtime.** The cap is a ceiling, not an allocation, and
  billing follows tokens generated, so a generous ceiling is already dynamic and there is
  nothing to build. All three shapes are worse than raising the default: retrying on
  `finish_reason == "length"` pays for the truncated output plus the full regeneration and
  doubles latency on the slowest requests; a continuation call costs two round trips and
  stitches explanatory prose badly across the boundary; scaling the cap to traceback length is
  backwards, since a 200-frame traceback usually needs a shorter explanation than a subtle
  two-frame one.

## Loose ends (not tasks)
Fold each into whichever branch already touches the relevant file.

- No CI test matrix. `setup.py` declares `Django>=4.2` and `python_requires>=3.9`, but the
  classifiers stop at Django 5.1 and the suite is only run against one Python/Django
  combination. Django 6.1 requires Python 3.12, so the matrix has to model Python and Django
  together, not either alone, or it will silently skip the combinations that actually matter.
  Cheap to add, since the suite is fully mocked and has no network dependency.
  Verify: a CI workflow running `tests` against more than one Python/Django combination
  exists under `.github/workflows/` on `main`.
- The `description` in `setup.py` and the GitHub repository description are intentionally
  identical, so they don't drift apart again. Change both together. `setup.py`'s copy is the
  PyPI summary line, frozen per version, so it can only change in a release commit.
  Verify: not applicable. This is a standing convention, not a state to check off.
- Sentry captures a spurious event when the explanation call itself fails (bad key, timeout,
  unreachable endpoint), via its `httpx` integration instrumenting inside the HTTP client,
  even though `explain_errors` catches the exception internally and it never becomes an
  unhandled exception. Documented in the README's Compatibility section. Consider whether the
  client should suppress or tag this so it doesn't read as an application bug in a
  Sentry-instrumented project.
  Verify: not applicable. Closes only if `client.py` is changed to address it.
- `__acall__` dispatches to `sync_to_async(self.process_exception)` before checking `DEBUG`;
  the guard is inside `process_exception`. Costs a thread-pool round-trip per exception when
  the middleware is left installed with `DEBUG=False`. Two-line fix. Was intended to fold into
  preserve-debug-page and did not, so it still needs a home.
  Verify: a `DEBUG` check precedes the `sync_to_async(self.process_exception)` call in
  `explain_errors/middleware.py`.
- `PLACEHOLDER_API_KEY` substitution in `explain_errors/client.py` is unconditional on
  `base_url`. It is correct for Ollama and LM Studio, which ignore the key, and wrong for
  authenticating remote endpoints such as Anthropic or Azure, where it converts a missing key
  into an opaque 401. Either restrict the placeholder to loopback and private-network hosts,
  or catch the 401 and raise a message naming `OPENAI_API_KEY`. Folds into whichever branch
  next touches `client.py`.
- `_compiled_patterns()` in `explain_errors/sanitize.py` reports an invalid
  `EXPLAIN_ERRORS_REDACT_PATTERNS` entry with a bare `print`, not the `explain_errors`
  logger. Inconsistent with the truncation warning in `middleware.py` and invisible to
  anyone configuring logging. One-line fix, folds into whichever branch next touches
  `sanitize.py`.
  Verify: `sanitize.py` uses `logging` rather than `print` for the invalid-pattern case.
- `docs/hardening-design.md.` has a trailing dot in the filename.
  Verify: no file with a trailing dot exists under `docs/` on `main`.
- `docs/design.md` does not exist on `main`. It was written in a prior session and never
  committed. Its settings table was flagged as unverified against implementation.
  Verify: `docs/design.md` exists on `main` and its settings table matches the settings
  actually read in `explain_errors/`.
- Pushes from Claude Code to `origin` return 403 for tag creation and branch deletion, while
  branch creation and updates succeed. Likely a ruleset on ref deletion plus a `v*` tag rule.
  Not blocking, since releases go through the GitHub Release UI. Only matters if release or
  cleanup is ever scripted.
  Verify: not applicable. Closes only if release automation or scripted branch cleanup is
  ever built.

## Open strategic questions
- Provider abstraction beyond OpenAI-compatible endpoints. The Anthropic compatibility layer
  covers Claude with no Anthropic-specific code, which weakens rather than strengthens the
  case for a real abstraction. Revisit only if a target provider appears that has no
  OpenAI-compatible surface.
- Whether explain-errors-language plus django-docs-links is enough to carry the learning-aid
  positioning, or whether it needs a third distinguishing feature before the README rewrite
  is credible.

---
