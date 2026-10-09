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

1. **django-docs-links**: cross-reference explanations to official Django documentation.
   Ship the cheap version first: a static map of exception type plus context to a docs
   anchor. No index, no embeddings, no build step. A real docs link is verifiable in a way
   generated prose is not, which matters most for the learning audience and shrinks the
   hallucination surface.
   Ships after debug page injection so links render clickable in the browser
   rather than as text in stdout.
   Resolve before starting:
   - Django docs license terms for redistributing a derived map or index.
   - Version pinning. URLs carry a version segment, and serving 5.2 links to a user on 4.2
     is worse than no link.
   - Translated docs coverage, now that `EXPLAIN_ERRORS_LANGUAGE` has shipped.
     `docs.djangoproject.com` has translations with uneven coverage, so a localized link may
     404 or silently fall back.
   Verify: a docs-map module (for example `explain_errors/docs_links.py`) exists on `main`.

2. **README positioning rewrite**: after django-docs-links, when the learning-aid claim is
   backed by shipped behavior. Not before. The README documents shipped behavior; anything
   earlier is a promise that has to be kept.
   Verify: the README lead paragraph describes a grounded Django learning aid rather than an
   error explainer.

   - **Production Safety section**: document the safe middleware registration pattern
     (register only when `DEBUG` is on) plus a CI guard (`manage.py check --deploy`) against
     shipping the middleware to production by accident. Lands on its own branch and commit,
     ahead of the rest of the positioning rewrite.
     Verify: `README.md` on `main` has a `## Production Safety` heading.

3. **editor-open-links**: in the injected debug page, render each traceback frame (and each
   source file cited by retrieval) as an "open in editor" link to that file and line. Use a
   configurable URL template setting, for example
   `EXPLAIN_ERRORS_EDITOR_URL = "vscode://file/{path}:{line}"`, following the pattern used by
   Werkzeug and django-extensions. The setting defaults to off, and links only render when
   DEBUG is on. A small addition that helps the developer navigate from the explanation to
   their own code, it fits the "learning aid grounded in user code" positioning and needs no
   editor extension.
   Sequencing: after the eval harness dev.to article.
   Verify: `grep -rn "EXPLAIN_ERRORS_EDITOR_URL" explain_errors/` on `main` returns a match.

## Conditional or unscheduled

- **pinned-frame-retrieval**: the eval harness showed RAG-on can anchor on an adjacent
  retrieved chunk instead of the one that matters. missing_post_key redirected the fix
  to a retrieved template instead of the view.
  Root cause (code read, Oct 2026): retrieval is vector similarity only. The innermost
  project frame's file and line shape the query text but are never matched against the
  stored `file_path`, `start_line` and `end_line`, so the chunk containing the failing
  line is not guaranteed to be retrieved, and the one-chunk-per-file dedup can drop it.
  Shape: at error time, read the failing project frame's file from disk, parse it with
  `ast`, and find the smallest enclosing function, method, or class containing the failing
  line. If nothing encloses it, or the file fails to parse, fall back to a line window
  around the line. Pin that source first in the prompt, labeled as the failing location,
  ahead of the vector search results. Drop vector results that overlap the pinned range.
  KNN results still follow, since the cause is often in related code the traceback does
  not name (a form, a template, a model).
  Why it reads from disk instead of querying the index by file and line:
  - Index line numbers drift after edits until the index is rebuilt, and `build_index()`
    is a full rebuild with no per-file mtime or hash.
  - Module-level lines have no chunk: a `.py` file with at least one top-level def drops
    its module-level code.
  - A method would return its whole class, since Python chunks are top-level defs only.
  Indexer gap this does not fix: vector chunks are whole top-level classes (precision
  loss, and possible embedding input-limit failure on large classes), and module-level
  code is dropped from `.py` files that have a top-level def. Pinned-frame-retrieval fixes
  the failing frame only, not vector search.
  Works without the `[rag]` extra installed.
  Separate knob: lowering `EXPLAIN_ERRORS_RAG_TOP_K` is tested in the same harness run
  (see retrieval-arms-eval), not bundled into this change.
  Depends on the loose end about two frame classifiers (`_is_project_path` versus the
  `tracebacks.py` heuristic): choosing the failing project frame needs one answer to
  "which frames are project frames", so consolidate first or in the same branch.
  Resolve before starting (owner decisions, no defaults chosen here):
  - (a) Innermost project frame only, or every project frame.
  - (b) Fallback line window size.
  Test with the harness before and after. missing_post_key is the target case. Ship
  gated by the decision rule in retrieval-arms-eval.
  Verify: the retriever path on `main` runs the `ast` pinned-frame lookup by default, with
  no switch or flag gating it.

- **judge-calibration-set** (harness work): measure how often the judge agrees with a
  human, instead of assuming it.
  `evals/spotcheck.py` covers one failure direction only: claims marked "contradicted"
  that still name real source identifiers, on a filtered sample. It never checks claims
  marked "verified" or "absent", and never checks the per-question answers or the A/B
  winner.
  Shape: from one results file, draw 50 claims and 15 pairwise comparisons (one per
  fixture). Stratify the claims so each status (verified, contradicted, absent) has at
  least 10, and fill the rest at random; "contradicted" is rare, so a purely random draw
  would leave it too thin to measure. A script presents each item to the human grader with
  the judge's verdict hidden: the claim plus its source excerpt, or the two explanations
  plus the fixture's known cause. Store the human labels in `evals/calibration/`. A second
  script compares the judge with the human labels and reports agreement overall, per claim
  status, per question, and for the winner.
  Thresholds: at least 85% overall claim agreement, at least 75% for each claim status,
  and winner agreement on at least 12 of 15 comparisons. Below any threshold, results from
  that judge configuration are reported as provisional, not as findings, until the judge
  prompt is fixed and re-calibrated.
  Use: re-run the agreement check whenever the judge prompt or judge model changes. Report
  the agreement rates in `evals/README.md` next to the results they qualify.
  Verify: `evals/README.md` on `main` reports a judge-human agreement rate.

- **retrieval-arms-eval** (harness work): compare retrieval combinations before building
  any of them. Arms:
  - (a) RAG as shipped, the baseline.
  - (b) ast only: pinned frame, no index.
  - (c) ast + RAG.
  - (d) ast + Jedi + RAG.
  - Optionally (e) ast + Jedi, no index.
  - Optionally a lower-top-k variant of (c).
  New fixtures, chosen so that different arms should win:
  - a renamed model field still used in a view (the definition is outside the stack);
  - a `reverse()` URL name typo;
  - a template error that raises, such as an unknown filter or tag (`TemplateSyntaxError`);
  - an error in one method of a large class-based view;
  - an error in module-level code such as `urlpatterns`;
  - a stale-index case where source is edited after `build_index()`.
  Give the judge the retrieved context for every arm, or correct source-derived details
  score as fabrication (ties to the existing judge limitation in `evals/README.md`).
  Record win rate, tokens, latency, and cost per arm in `evals/README.md`.
  Arms (b) through (e) may be implemented harness-side or behind an internal switch;
  nothing user-facing ships from this entry.
  Decision rules:
  - Ship pinned-frame-retrieval if (c) beats (a).
  - Pursue jedi-dependency-resolution only if (d) beats (c).
  - Take up the retrieval-default strategic question if (b) or (e) comes close to (c).
  Run after judge-calibration-set, so the judge is validated before comparing arms.
  Verify: `evals/README.md` records a harness run comparing these arms.

- **jedi-dependency-resolution** (conditional on retrieval-arms-eval, only if arm (d)
  beats arm (c)): from the failing line, resolve referenced Python names to their
  definitions in other project files (example: `post.title` in a view resolves to
  `class Post` in `models.py`). Add those definitions to the prompt after the pinned frame
  from pinned-frame-retrieval, and dedupe against vector results.
  Jedi is used in-process as a library behind an optional extra, not through an LSP or MCP
  server. This is consistent with the Rejected section, which rules out an editor
  extension or MCP server as a second distribution artifact; an optional extra adds none.
  Limits: Jedi cannot follow strings (template paths, `reverse()` names, settings keys,
  `request.POST` keys), so it complements RAG rather than replacing it. It reads from
  disk, so it has no staleness.
  Verify: an `extras_require` entry naming jedi exists in `setup.py`.

- **working-tree-diff-context**: include `git diff HEAD`, scoped to files that appear
  as project frames in the traceback, in the prompt so the explanation can name the
  edit that caused the error. Input to the model only; nothing new is displayed. In
  local development the breaking change is usually uncommitted, which `git log` never
  shows, so the diff is the signal and history is not.
  Unproven: RAG already retrieves current source, but only if the index was rebuilt after
  the edit; pinned-frame-retrieval reads current source from disk. RAG may reach the same
  answer when both sides of a mismatch are retrieved (a renamed model field plus the view still
  using the old name). The diff's distinct value, if any, is where current source alone
  is ambiguous: renamed versus deleted versus never existed.
  Build the harness comparison first: 4 to 5 regression fixtures, each a working
  baseline plus a breaking uncommitted edit, including at least one where RAG alone
  plausibly succeeds. Give the judge the diff, or correct diff-derived details will
  score as fabrication. Ship only if diff-on beats diff-off.
  If shipped: gated behind `EXPLAIN_ERRORS_INCLUDE_DIFF`, default `False`; diff passes
  through existing sanitization before leaving the machine; git only, degrading
  silently with no repository or no `git` binary.
  Verify: `EXPLAIN_ERRORS_INCLUDE_DIFF` appears in `explain_errors/` on `main`.

- **dedup-identical-errors**: LRU hash of exception type plus top frame, so repeated
  identical errors do not burn the sliding-window throttle. Small.
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

- **anthropic-rag-embeddings**: check whether RAG works for Anthropic users.
  The indexer and retriever use the same client and `OPENAI_BASE_URL` as the
  chat call, and Anthropic's OpenAI-compatible API may have no embeddings
  endpoint. If so, `build_error_index` fails for Anthropic users and the
  README's RAG recommendation does not apply to them. Check against the live
  API first. If embeddings are unsupported, either document it in the README
  (Anthropic section and RAG section), or add separate embedding settings
  (for example `EXPLAIN_ERRORS_RAG_EMBED_BASE_URL` and a matching key) so
  chat and embeddings can use different providers. Local embeddings (above)
  would also cover this case.
  Verify: the README's Anthropic section states whether RAG works with it.

- **debug-toolbar-with-banner**: the Compatibility table's Django Debug
  Toolbar row was verified empirically before debug page injection existed.
  Re-verify the toolbar with the banner present, on Django 4.2 and the
  newest supported version, and update the row and the "verified
  empirically" sentence to say so.
  Verify: the Compatibility section states the Debug Toolbar row was
  verified with the explanation banner present.

- **README restructure**: move the "Debug page injection" section up, next to
  Usage, and trim Usage step 2, which repeats it; reduce "Async Support" to
  one line in Features; group the Configuration table by purpose (provider,
  output, limits, redaction, language); shorten the tokenizer detail in
  "Explanation language"; add a Requirements line (Python 3.9+, Django 4.2+);
  give Contributing the dev setup and test command; remove changelog
  wording ("no longer needs to be last", the "Upgrading" paragraph in Debug
  page injection). Also re-record the "Before / after" RAG example on the
  current version, since its traceback-only side predates 0.7.0, and drop
  the note that says so. Coordinate with README positioning rewrite in the
  sequenced queue: do this first, or fold both into one PR, so the README
  is not rewritten twice.
  Verify: README.md has a Requirements line and the "Debug page injection"
  section appears before "Async Support".

- **eval-harness-concurrent-judging**: run the judge phase of `evals/run.py`
  concurrently (thread pool, `--judge-concurrency N`, default around 8, N=1
  reproducing today's behavior). Draw each comparison's A/B position from
  the existing rng up front, in the current order, so position
  randomization is unchanged, and return judgments in the current order.
  Keep the generator phase sequential: `_capture_call` patches
  `explain_errors.middleware.sanitize_traceback` and attaches a handler to
  the shared `explain_errors` logger, so concurrent calls would mix up
  captured tracebacks and token usage, and reported generator latency (a
  number the README cites) would include contention. Record wall-clock time
  per phase in the results file so the gain is measured, not assumed. Watch
  for judge-provider rate limits.
  Verify: `--judge-concurrency` appears in `evals/run.py` on `main`.

- **eval-fix-validity**: a second eval track that asks whether the suggested fix
  works, not just whether the explanation reads better. For each fixture, the
  harness (not the package) turns the explanation's suggested fix into a patch,
  applies it to a temporary copy of `evals/fixture_app/`, re-hits the fixture URL,
  and records the result. Report four rates per condition (RAG-on and RAG-off):
  explained (diagnosis matches `expected_cause`), fix proposed, patch applied
  cleanly, verified.
  The pass condition is the gap to close first. Fixtures are URLs, not tests, and
  "no 500 on re-request" is too weak: a patch that wraps the view in
  `try/except` passes it. Each fixture needs its own oracle (expected status
  code plus a response assertion, or a small test per fixture) written before any
  patch is generated.
  Why it matters: a passing oracle is ground truth that no LLM judge can
  misread, which answers the judge limitation recorded in `evals/README.md`
  (correct source-derived details scored as fabrication). It also tests whether
  RAG's measured value (specificity) carries through to correct fixes, or only
  to more specific prose.
  Scope: harness only. A fix-and-verify mode inside the package (patch, apply in
  a worktree, rerun tests) is out of scope. It overlaps coding agents, which
  already do this loop, it assumes a failing test exists for a live request, and
  executing generated code conflicts with the package's human-reader positioning.
  Sequencing: after the public write-up of the existing harness results, so this
  can be a second write-up rather than competing with the first.
  Verify: `evals/README.md` on `main` reports a verified-fix rate.

- **rag-index-staleness-warning**: warn when the RAG index no longer matches the source
  on disk. Problem: `build_index()` is a full rebuild with no per-file mtime or hash, so
  after edits the index serves old source and old line ranges until someone rebuilds,
  with no signal.
  Shape: at build time, store each indexed file's path, mtime, and content hash in the
  index database. At retrieval time, stat the indexed files; for files whose mtime
  changed, compare the hash to rule out false positives (git checkout, editors touching
  files). If any indexed file changed or was deleted, log a warning through the
  `explain_errors` logger naming the count and the rebuild command
  (`build_error_index`). Detect and warn only: no re-embedding.
  Purpose: measure how often staleness happens in practice before building anything
  heavier (per-file re-embedding, file watching).
  Compatibility: existing indexes have no file metadata. Treat them as unknown freshness
  and warn once to rebuild.
  Decided:
  - Count changed and deleted indexed files only, not new unindexed files. There is no
    directory walk at retrieval time. Directory-mtime detection of new files is a possible
    follow-up.
  - Warn once per process. runserver's autoreload re-arms the warning after Python edits.
  - Known gap: template edits do not trigger a reload, so a stale template does not
    re-warn until the next Python edit.
  Relates to: pinned-frame-retrieval (reads from disk, so unaffected) and
  working-tree-diff-context (its "Unproven" note depends on index freshness).
  When this ships, update the "no per-file mtime or hash" wording in
  pinned-frame-retrieval; its read-from-disk reasoning still holds, since a warning does
  not fix line drift.
  Verify: `explain_errors/rag/` on `main` stores per-file hashes at build time and logs a
  stale-index warning at retrieval time.

- **data-flow-disclosure**: the README does not state what leaves the developer's
  machine, when, or to whom.
  Shape: a short README section stating:
  - at error time, the sanitized traceback and prompt go to the chat endpoint;
  - with RAG enabled, every indexed chunk (sanitized) goes to the embeddings endpoint at
    build time, and retrieved chunks are included in the error-time prompt;
  - pointing `OPENAI_BASE_URL` at a local server (Ollama, LM Studio) keeps all of it on
    the machine;
  - the default excluded directories, and how to extend them with
    `EXPLAIN_ERRORS_RAG_EXCLUDE`;
  - redaction via `EXPLAIN_ERRORS_REDACT_PATTERNS`.
  Factual only: describe shipped behavior, no claims about provider retention or training
  policies.
  Coordinate with README restructure and README positioning rewrite so the README is not
  rewritten twice. Small; can land on its own or fold into either.
  Verify: README.md on `main` has a section describing what data leaves the machine and
  when.

## Rejected (recorded so it does not resurface)
- **Editor extension or MCP server for IDE consumption.** VS Code, PyCharm, and Zed all have
  integrated terminals, so `runserver` output is already inside the editor. The browser, not
  the editor, is the surface a developer is on when a 500 fires. A TypeScript extension is a
  second distribution artifact with its own marketplace listing, release cadence, and
  compatibility surface, roughly doubling maintenance for a placement improvement. Debug page
  injection addresses the same problem without a second artifact. The only thing an extension
  would uniquely provide is a gutter marker on the failing `file:line`. Revisit only if that
  specific capability is repeatedly requested.
- **"Insert at cursor" and "open snippet in editor."** Both require a VS Code extension,
  which was already rejected above. The browser debug page is where developers look when a
  500 fires.
- **Apply fix to exact lines.** Also rejected. It moves the package into coding-agent
  territory, and it conflicts with the decision to keep fix validation as a harness-only eval
  track (`eval-fix-validity`). The eval showed retrieval can anchor on an adjacent chunk
  (`missing_post_key` pointed the fix at a template, not the view), so an auto-apply would
  edit the wrong file.
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

- README typography pass (remove em dashes). The Production Safety section (and its one
  touched line in Installation) were written without em dashes, but the rest of the README
  still uses them throughout. A full sweep replacing em dashes with commas, parentheses,
  colons, or periods is a separate, purely cosmetic concern, not part of production-safety
  docs or the positioning rewrite. Fold into whichever branch next touches `README.md`, or do
  it standalone.
  Verify: no em dash (`—`) appears in `README.md` on `main`.
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
- `evals/run.py` learns each fixture's exception type by monkeypatching
  `explain_errors.middleware.sanitize_traceback`, the one call inside `process_exception`
  where `sys.exc_info()` is still live once `EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE=False` has
  short-circuited `got_request_exception` (that signal only fires when the exception is left
  to propagate). This couples the harness to an internal function's name rather than a public
  seam. Renaming or removing `sanitize_traceback` breaks the harness loudly, with
  `patch.object` raising `AttributeError` at run time, not silently -- but worth knowing
  before any refactor of `middleware.py` touches that name.
  Verify: not applicable. Closes only if `evals/run.py`'s exception-capture mechanism changes
  to something other than patching `sanitize_traceback`.
- Two frame classifiers now exist: `rag/retriever.py`'s `_is_project_path` (`BASE_DIR`-based)
  and `tracebacks.py`'s library-first heuristic (site-packages/dist-packages, the stdlib, and
  Django's own package directory; no `BASE_DIR` dependency). They can disagree on editable
  installs or a virtualenv living inside the project. Consolidate on one, probably the
  `tracebacks.py` version since it doesn't depend on `BASE_DIR`. Fold into whichever branch
  next touches either file.
  Verify: only one frame-classification function exists across
  `explain_errors/rag/retriever.py` and `explain_errors/tracebacks.py`.
- `render()` in `explain_errors/tracebacks.py`: replace manual run
  detection with `itertools.groupby`.
  Why it is worth doing:
  - It removes a class of off-by-one risk. The current nested
    `while i < n and i not in kept` loop has to be simulated by hand to
    confirm it handles the boundaries; `groupby` has no boundary to get
    wrong.
  - The intent is named, not implied. "Group frames into contiguous kept
    and omitted runs" is what `groupby` says on its face.
  - It is nearly free, because the frame-classifier consolidation above
    already opens `tracebacks.py`.
  Shape: `groupby` over frame indices keyed on membership in `kept`, which
  must be a set. Same O(n) behavior and output. Fold into the same branch
  as the frame-classifier consolidation.
  Verify: `render()` in `tracebacks.py` uses `itertools.groupby`.
- Before the app-frame-preserving truncation change, `process_exception`'s tail-slice built the
  traceback from `traceback.format_exc()`, which reads `sys.exc_info()` for the current thread. On
  the async path that call runs inside `sync_to_async`'s worker thread, and under a real ASGI
  server (uvicorn, daphne) that thread has no reason to have `sys.exc_info()` populated, so it may
  have silently produced `"NoneType: None"` instead of a real traceback for every async
  explanation. 0.7.0 fixed the underlying cause (traceback building no longer touches
  `sys.exc_info()` at all) but the earlier failure mode was never confirmed against a real server:
  `django.test.SimpleTestCase`'s async test support bridges the test coroutine through `asgiref`
  in a way that happens to leave `sys.exc_info()` populated on the worker thread regardless,
  masking the difference. Five-minute check: run the fixture app under uvicorn or daphne on a
  checkout from before the truncation change, hit an async view that raises, and read what
  actually got sent. If it was broken, note it in the 0.7.0 release notes retroactively.
  Verify: not applicable. Closes only once someone runs the check and, if warranted, the 0.7.0
  release notes are amended.

## Open strategic questions
- Provider abstraction beyond OpenAI-compatible endpoints. The Anthropic compatibility layer
  covers Claude with no Anthropic-specific code, which weakens rather than strengthens the
  case for a real abstraction. Revisit only if a target provider appears that has no
  OpenAI-compatible surface.
- Whether explain-errors-language plus django-docs-links is enough to carry the learning-aid
  positioning, or whether it needs a third distinguishing feature before the README rewrite
  is credible.
- **retrieval-default**: if ast only (or ast + Jedi) captures most of RAG's measured gain,
  consider making it the zero-setup default with RAG as opt-in, controlled by one setting
  (for example `EXPLAIN_ERRORS_RETRIEVAL`).
  Motivation: RAG's setup cost (the extra, the index build, rebuilds after edits), that
  `build_index()` sends every indexed chunk to the embeddings provider, and the
  anthropic-rag-embeddings gap.
  Positioning implication: the differentiator becomes grounding in the user's own code,
  with RAG as one mechanism. The README positioning text would change accordingly.
  Decide only from retrieval-arms-eval results.

---
