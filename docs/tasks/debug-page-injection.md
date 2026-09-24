# Task: debug-page-injection

Show the explanation on Django's debug page, directly under the exception
headline, in addition to stdout.

Rationale: docs/roadmap.md, "Debug page injection". This spec supersedes that
entry on two points: the default is True, and all explanations are injected,
not only RAG-grounded ones.

## 1. Setting

`EXPLAIN_ERRORS_INJECT_DEBUG_PAGE`, default `True`.

Injection happens only when all of these hold:
- `DEBUG` is True
- `EXPLAIN_ERRORS_INJECT_DEBUG_PAGE` is True
- `EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE` is True (otherwise the JSON 500 path
  runs and there is no debug page; leave that path unchanged)
- an explanation was produced (not throttled, API call succeeded)

## 2. Mechanism

New module `explain_errors/debug_page.py`:

- `ExplainErrorsExceptionReporter(django.views.debug.ExceptionReporter)`
  overriding `get_traceback_html()`: call `super()`, then insert the banner
  immediately before the first `<table class="meta">`. Verified present in
  Django 4.2 and 5.2 technical_500.html. Do not copy or override the template:
  its markup differs between those versions.
- The explanation is read from `self.request._explain_errors_explanation`.
- Fail open: if the request is None, the attribute is missing, the anchor is
  not found, or anything raises, return the `super()` HTML unchanged and log
  one `logger.warning`. The debug page must never break because of this code.
- `get_traceback_text()` is not touched.

In `process_exception`, when the section 1 conditions hold, after the stdout
print:
- set `request._explain_errors_explanation = explanation`
- set `request.exception_reporter_class = ExplainErrorsExceptionReporter`,
  but only if the request has no `exception_reporter_class` already and
  `settings.DEFAULT_EXCEPTION_REPORTER` is Django's default. Otherwise skip
  injection and log at debug level. Never override a user's reporter.

Django's handler then renders through our reporter via
`get_exception_reporter_class(request)`. No user settings change is needed.

The async path needs no change: `process_exception` receives the same request
object under `sync_to_async`.

## 3. Banner

- `<section id="explain-errors">` with inline styles: bordered, light
  background, sans-serif, readable on Django's page.
- Heading: `Explanation (django-explain-errors)`.
- Body: `django.utils.html.escape(explanation)` in a block with
  `white-space: pre-wrap`, `max-height: 16em`, `overflow-y: auto`.
- The explanation is model output: always escape it. Never mark it safe.

## 4. Tests

New file `tests/test_debug_page.py`. Mock all OpenAI calls.

Reporter unit tests:
- banner inserted after `pre.exception_value` and before `table.meta`
- explanation containing `<script>` renders escaped
- no attribute, anchor missing, or an internal exception each return HTML
  identical to the parent reporter's

Request-cycle tests (per CLAUDE.md, Django invokes the reporter, so use
`django.test.Client(raise_request_exception=False)` and `AsyncClient`, under
`override_settings(DEBUG=True)`, with the client built inside the override):
- default settings: 500 HTML contains the banner and stdout still prints
- `INJECT_DEBUG_PAGE=False`: no banner
- `PRESERVE_DEBUG_PAGE=False`: JSON 500 unchanged
- throttle exhausted: plain debug page, no banner
- OpenAI raises: plain debug page, no banner
- custom `DEFAULT_EXCEPTION_REPORTER`: not overridden, no banner
- `Accept: text/plain`: text response unchanged
- async view via `AsyncClient`: banner present

## 5. Docs

- README: document the setting, its default, where the banner appears, the
  preserve-mode dependency, fail-open behavior, the custom-reporter
  limitation, and that upgrading changes the debug page's appearance.
- CLAUDE.md: add the setting to invariant 4's list; amend invariant 5 with a
  second pre-1.0 exception: `EXPLAIN_ERRORS_INJECT_DEBUG_PAGE` defaults to
  `True`.
- docs/roadmap.md: delete the "Debug page injection" entry and renumber.
- Do not bump the version.

## 6. Out of scope

Plain-text debug responses, styling beyond section 3, and any change to the
prompt or the stdout output.
