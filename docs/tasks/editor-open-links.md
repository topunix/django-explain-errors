# editor-open-links

## Goal

In the explanation banner that `ExplainErrorsExceptionReporter` injects into Django's debug
page, render "open in editor" links to the application's own traceback frames and to the
project source files cited by RAG retrieval. Links are built from a developer-configured URL
template, `EXPLAIN_ERRORS_EDITOR_URL` (for example `"vscode://file/{path}:{line}"`), following
the pattern used by Werkzeug and django-extensions. The setting defaults to off (`None`), and
links only render when `DEBUG` is on. No editor extension is involved.

## Scope

### In

- New setting `EXPLAIN_ERRORS_EDITOR_URL`, read with `getattr(settings, ..., None)`. Default
  `None`. Any falsy value (`None`, `""`) means off.
- The template supports two placeholders, `{path}` and `{line}`. Rendering uses
  `str.format(path=..., line=...)`. `path` is the absolute file path, percent-encoded with
  `urllib.parse.quote(path, safe="/:\\")` so spaces and other unsafe characters are encoded
  while POSIX and Windows paths stay readable to editors. `line` is the integer line number.
- Links are rendered inside the existing banner (`<section id="explain-errors">`), after the
  explanation `<div>`, as a small "Open in editor" block with up to two lists:
  - Traceback frames: each frame of the exception's traceback that is not a library frame
    (use `explain_errors.tracebacks._is_library_frame`), in traceback order (outermost
    first), labeled `<filename>:<lineno> in <function name>`. Frames come from the reporter's
    own `self.tb` via `traceback.extract_tb`, so this works on both the fresh-explanation path
    and the dedup replay path without any middleware state.
  - Retrieved source: each RAG chunk whose header actually made it into the prompt (that is,
    its `--- path:start-end ---` header appears in the string returned by
    `format_chunks_for_prompt`), labeled `<file_path>:<start_line>`, linked at `start_line`.
    Deduplicated on `(file_path, start_line)` in retrieval order.
  - A list with no entries is omitted. If both are empty, no links block is rendered at all.
- Plumbing for cited chunks: `_build_prompt(self, exception, cited=None)` keeps returning the
  prompt string unchanged. When `cited` is a list, it appends `(file_path, start_line)` for
  each included chunk. `process_exception` passes a fresh list and forwards it to
  `_inject_debug_page(request, explanation, cited_files=...)`, which stores it on
  `request._explain_errors_cited_files` alongside the existing
  `request._explain_errors_explanation`. The dedup replay path passes no cited files (see Out).
- All link text and `href` values are HTML-escaped with `django.utils.html.escape`.
- Scheme guard: if the rendered URL, stripped and lower-cased, starts with `javascript:`,
  `data:`, or `vbscript:`, that link is not rendered (log once at debug level, no exception).
- Invalid template: if formatting raises (`KeyError`, `IndexError`, `ValueError`, for example
  an unknown placeholder `{file}`), render no links, log one warning via the
  `explain_errors.debug_page` logger, and still render the banner with the explanation.
- Fail open: any exception while building links leaves the banner exactly as it is without
  links. The debug page must never break because of this code.
- Belt and braces: the links block is not rendered when `settings.DEBUG` is False, even if the
  reporter is somehow used.
- Links get `rel="noopener noreferrer"` and inline styles consistent with the existing banner
  (no external CSS or JS).

### Out

- Editing Django's own frame HTML (the `technical_500.html` frame list). Links live in the
  banner only, which is the part this package owns. Overriding Django's template is fragile
  across Django versions.
- Library frames (site-packages, stdlib, Django). The feature is about navigating to the
  developer's own code.
- Cited-file links on the dedup replay path. The cache stores only the explanation; replay
  shows frame links only. Extending the cache format is a separate change.
- The JSON 500 path (`EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE=False`) and stdout output. No links
  are added there.
- Preset names (for example `"vscode"`, `"pycharm"`) instead of a full template. Only a raw
  template is supported.
- Path mapping for containers or remote dev (rewriting a container path prefix to a host
  path).
- README em dash sweep (roadmap loose end). Not folded into this branch.

## Files touched

- `explain_errors/debug_page.py`: link building and rendering inside the banner.
- `explain_errors/middleware.py`: `_build_prompt` `cited` parameter, `process_exception`
  forwarding, `_inject_debug_page` storing `request._explain_errors_cited_files`.
- `tests/test_editor_links.py` (new).
- `README.md`: new subsection under "Debug page injection" and a Configuration table row.
- `CHANGELOG.md`: entry under `## Unreleased`, `### Added`.
- `CLAUDE.md`: add `EXPLAIN_ERRORS_EDITOR_URL` to the Invariant 4 settings list (default
  `None`; off unless set).

## Acceptance criteria

1. `grep -rn "EXPLAIN_ERRORS_EDITOR_URL" explain_errors/` returns a match.
2. With `EXPLAIN_ERRORS_EDITOR_URL` unset (and with it set to `""`), the injected banner HTML
   is byte-identical to the banner produced on `origin/main` for the same explanation:
   `get_traceback_html()` output contains `BANNER_TEMPLATE.format(explanation=render_explanation_html(text))`
   verbatim and contains no `Open in editor` text. Checked by a test.
3. With `EXPLAIN_ERRORS_EDITOR_URL = "vscode://file/{path}:{line}"` and an exception raised
   from a test module (an application frame), the debug page served through
   `django.test.Client` contains an `href` equal to the escaped
   `vscode://file/<quoted abs path>:<lineno>` of the raising line.
4. Library frames (for example a frame inside the `django` package) produce no link.
5. A path containing a space is rendered with `%20` in the `href`; a Windows-style path such as
   `C:\proj\app\views.py` keeps `C:` and the backslashes unencoded (unit test on the URL
   helper).
6. HTML special characters in the template or path (`"`, `<`, `&`) are escaped in the `href`
   and label; no raw `<` from those inputs appears in the output.
7. A template rendering to `javascript:alert(1)` (any case, leading whitespace) produces no
   link.
8. A template with an unknown placeholder (`"vscode://file/{file}:{line}"`) produces no links,
   logs one warning on the `explain_errors.debug_page` logger, and the banner with the
   explanation is still present.
9. With RAG enabled and `retrieve_chunks` mocked to return chunks, the cited chunks whose
   headers fit in the prompt appear as links at their `start_line`; a chunk truncated out of
   the prompt by `EXPLAIN_ERRORS_RAG_MAX_PROMPT_CHARS` does not. Duplicate
   `(file_path, start_line)` pairs render once.
10. `_build_prompt(exc)` with no `cited` argument returns the same string as before; existing
    `tests/test_process_exception_split.py` passes unchanged.
11. On the dedup replay path the page still shows frame links (and no cited-file list).
12. The async path works: a test through an ASGI request (or the existing async test pattern
    in `tests/test_debug_page.py`) with the setting on shows the frame link.
13. With `DEBUG=False`, no explanation and no links appear (middleware inert; reporter guard
    covered by a direct unit test calling the link builder with `DEBUG=False`).
14. Full suite passes: `DJANGO_SETTINGS_MODULE=test_settings python -m django test tests -v 2`.
    Test count is at least 388 plus the new tests. No existing test is deleted or weakened.
15. `README.md` documents the setting (examples for VS Code `vscode://file/{path}:{line}` and
    PyCharm `pycharm://open?file={path}&line={line}`), states it is off by default and
    DEBUG-only, and the Configuration table has a row for it. `CHANGELOG.md` has an
    `### Added` entry under `## Unreleased`. Neither addition uses em dashes.
16. No `OpenAI(` constructor call is added outside `explain_errors/client.py`; no network in
    tests.

## Tests to add

In `tests/test_editor_links.py` (unittest, `SimpleTestCase`/`IsolatedAsyncioTestCase`,
`override_settings`, `unittest.mock`; mock all OpenAI calls the way `tests/test_debug_page.py`
does, and reuse `tests/urls_debug_page.py` or add views to it as needed):

- Setting off (unset and `""`): banner byte-identical, no `Open in editor`.
- Setting on, request through `django.test.Client`: frame link present with correct `href`.
- Library frame excluded.
- URL helper: space encoded as `%20`; Windows path keeps `C:` and backslashes; HTML escaping of
  quotes and angle brackets.
- `javascript:` scheme blocked, case-insensitive with leading whitespace.
- Unknown placeholder: no links, one warning, banner present.
- RAG cited files: included chunks linked, truncated-out chunk not linked, duplicates once.
- Dedup replay: frame links present, no cited list.
- Async request path: frame link present.
- `DEBUG=False` guard on the link builder.
- Link builder raising (patch an internal helper to raise): banner still present, no links,
  page still renders.

## Docs to update

- `README.md`: "Debug page injection" subsection describing editor links, plus the
  Configuration table row.
- `CHANGELOG.md`: `## Unreleased` / `### Added` entry (user-facing behavior, opt-in).
- `CLAUDE.md`: Invariant 4 settings list.

## Roadmap item addressed

`docs/roadmap.md`, Sequenced queue, **editor-open-links**. Verify marker:
`grep -rn "EXPLAIN_ERRORS_EDITOR_URL" explain_errors/` on `main` returns a match.

## Environment note

On this container, `pip install ".[rag]"` fails with the Debian setuptools
`install_layout` error. Run `pip install --ignore-installed setuptools` first, then
`pip install --upgrade ".[rag]"`.
