# Task: readme-compatibility

README pass following `preserve-mode-default`. Documents behavior that has already shipped.
No code changes; if this task appears to need one, stop and report rather than making it.

Read `docs/tasks/preserve-mode-default.md` and `docs/roadmap.md` item 1 first. Both record
decisions this task documents.

Branch from an updated `origin/main`. Do not commit unless asked.

The README ships with the package and PyPI metadata is immutable per version, so this is the
last chance to touch it before the next release commit.

## 1. Compatibility section

New section. For each package, state behavior in the default (preserve mode on) and in
`EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE=False`:

- Django Debug Toolbar: works by default. With the flag off, broken, since a JSON 500 leaves
  no HTML to inject into.
- Sentry, Rollbar: work by default. With the flag off, broken, since returning a response
  ends exception handling before `got_request_exception` fires.
- Django REST Framework: partial in both modes. Only exceptions DRF does not handle itself
  reach the middleware.
- Silk and other profiling panels: timings inflated by the API call in both modes, since
  `process_exception` blocks the request path.
- CORS, GZip, WhiteNoise: no interaction in either mode.
- `runserver_plus` and the Werkzeug debugger: see section 2.

Write it as prose or a table, whichever reads better at the width the rest of the README uses.

## 2. Werkzeug verification

**Do not document this case without verifying it.** Marking something unsupported is a
support commitment, and the expectation may be wrong.

The expectation: Django converts the exception into a response before it can propagate out
to the Werkzeug middleware, so the Werkzeug debugger never sees it, in preserve mode as much
as with the flag off.

Verify empirically in a scratch project with `django-extensions` installed, running under
`runserver_plus`, in both modes. Report what you observed.

- If broken in both, document as unsupported.
- If it works in preserve mode, that is a better outcome than the roadmap predicted. Document
  it as working and say so in your report, since it changes the roadmap entry.
- If you cannot verify it, leave the case out of the README entirely and report that. An
  undocumented case is better than a wrong one.

## 3. Install instruction

The README currently instructs installing the middleware last in `MIDDLEWARE`. That is what
makes `process_exception` run first, since Django calls the hook in reverse order, which
pre-empts other packages' hooks.

Preserve mode returns `None`, so the chain continues and ordering matters far less. Revise
the instruction to match. State the remaining ordering consideration if there is one, rather
than replacing a wrong rule with no rule.

## 4. Scope wording

The Scope section currently reads as ruling out all non-console surfaces. The intent was to
rule out consumption by AI agents only. Explanations rendered for a human in an editor or a
browser are in scope. This is a correction to existing text, not a new claim; do not expand
it into a positioning statement.

## 5. Provider notes

Anthropic works today through the OpenAI compatibility layer and is currently undocumented.
Add configuration:

- `OPENAI_BASE_URL` set to `https://api.anthropic.com/v1/`
- `OPENAI_API_KEY` set to an Anthropic key
- `OPENAI_MODEL` set to a Claude model name

`OPENAI_MODEL` is mandatory here: the default of `gpt-4o-mini` will 404 against Anthropic.
Say so explicitly rather than leaving it to be inferred.

Note that Anthropic positions the compatibility layer as primarily for testing and comparing
model capabilities rather than as a production solution. Acceptable for a development-only
middleware; state the caveat rather than omitting it.

Also document: reasoning models behind `OPENAI_BASE_URL` spend the token budget on internal
reasoning and can return empty content at low `OPENAI_MAX_TOKENS` values.

## 6. Settings table

Update every place the README documents defaults:

- `EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE` now defaults to `True`
- `OPENAI_MAX_TOKENS` now defaults to `DEFAULT_MAX_TOKENS` (1000)

Read the current values from `explain_errors/middleware.py` rather than trusting this spec,
and report any other setting whose documented default no longer matches the code.

## 7. Known bug to document, not fix

`PLACEHOLDER_API_KEY` in `explain_errors/client.py` substitutes whenever no API key is found,
regardless of `base_url`. That is correct for Ollama and LM Studio, which ignore the key, and
wrong for authenticating remote endpoints. A user who sets `OPENAI_BASE_URL` to Anthropic but
puts their key in `ANTHROPIC_API_KEY` gets an opaque 401 instead of a clear configuration
error.

Document the symptom and the fix (set `OPENAI_API_KEY`) in the provider section. Do not
change `client.py`; the code fix is a separate loose end.

## 8. Constraints

- Both `README.md` and `README.rst` exist and are currently byte-identical despite the
  extension. `setup.py` reads `README.rst` for the PyPI long description. Determine what the
  correct handling is and report before editing: keeping two copies in sync by hand is a
  standing defect, and a prior release shipped a stale `README.rst` because of it.
- No changes under `explain_errors/`.
- Existing tests must still pass. Run:
  `DJANGO_SETTINGS_MODULE=test_settings python -m django test tests -v 2`

## 9. Report before finishing

- What you observed for Werkzeug in both modes, and in what environment.
- Any documented default that did not match the code.
- Your recommendation on the `README.md` / `README.rst` duplication.

## 10. On completion

In the same branch:

- Delete `docs/tasks/preserve-mode-default.md` and `docs/tasks/readme-compatibility.md`.
- Delete item 1 (`preserve-mode-default`) from `docs/roadmap.md` and renumber the sequenced
  queue.
- Delete the "README Scope section wording" loose end, absorbed by this task.
- Delete the Anthropic documentation loose end, absorbed by this task.
- If Werkzeug turned out to work in preserve mode, note it in your report so the roadmap can
  be corrected separately.
