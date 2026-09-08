# Task: preserve-mode-default

Flip `EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE` to default `True`, and fix the explanation
truncation defect. Both touch `explain_errors/middleware.py`, which is why they ship together.

Full rationale is in `docs/roadmap.md` under item 1. Read it before starting.

Branch from an updated `origin/main`. Do not commit unless asked. Do not push.

README changes are deliberately out of scope and handled in a follow-up task. Do not edit
the README.

## 1. Flip the default

`EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE` defaults to `True`.

Returning a `JsonResponse` from `process_exception` ends exception handling before
`got_request_exception` fires, which breaks Sentry and Rollbar, and leaves no HTML for Debug
Toolbar to inject into. Preserve mode returns `None`, so the middleware chain continues and
Django renders its normal debug page.

**Expect existing tests to fail.** At least one asserts the `JsonResponse` path on default
settings. That is a correct test of the old default, not a bad test. Update it to set
`EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE=False` explicitly via `override_settings` so it still
covers the JSON path, and add a parallel test for the new default. Do not delete or loosen
any assertion. Report every test you changed and the assertion-level reason.

## 2. Truncation fix

At `OPENAI_MAX_TOKENS=150` the model is cut mid-sentence in English on default settings.
`max_tokens` is a hard cut the model never sees, and the current system prompt ("You are a
helpful assistant.") carries no length constraint, so the model plans a long answer and is
guillotined. RAG worsens it by lengthening the intended answer against the same cap.

Both halves ship together. Raising the cap alone yields rambling; constraining the prompt
alone still risks the cut.

Module-level constants in `middleware.py`:

```python
EXPLANATION_WORD_BUDGET = 200
# Generous ceiling, not a target. Billing follows tokens generated, so unused
# headroom is free. The prompt controls length; this only stops runaway
# generation, which matters most for local models behind OPENAI_BASE_URL.
DEFAULT_MAX_TOKENS = 1000
```

`self.max_tokens` reads `OPENAI_MAX_TOKENS` with `DEFAULT_MAX_TOKENS` as fallback.

Replace the system prompt with one that states the budget, interpolating the constant rather
than hardcoding the number:

```python
SYSTEM_PROMPT = (
    "You are a Django expert helping a developer understand an error. "
    f"Answer in under {EXPLANATION_WORD_BUDGET} words: what went wrong, "
    "why, and how to fix it. Be concrete and skip preamble."
)
```

## 3. Truncation warning

`finish_reason` is never inspected, so truncation is silent. Read
`response.choices[0].finish_reason`; when it is `"length"`, log a warning on the
`explain_errors` logger naming `OPENAI_MAX_TOKENS` and its current value. Use `logging`, not
`print`. The explanation still prints as normal; this is diagnostic only.

## 4. Tests

New file `tests/test_truncation.py`. `SimpleTestCase`, all OpenAI calls mocked, no network.

1. `DEFAULT_MAX_TOKENS >= EXPLANATION_WORD_BUDGET * 4`. Guards drift if someone raises the
   word budget without raising the ceiling.
2. `str(EXPLANATION_WORD_BUDGET)` appears in `SYSTEM_PROMPT`. Guards the other half: the cap
   alone does not constrain the model.
3. Default `self.max_tokens` equals `DEFAULT_MAX_TOKENS` and exceeds 150.
4. `OPENAI_MAX_TOKENS` set via `override_settings` reaches `chat.completions.create`.
5. `finish_reason="length"` logs a warning containing "truncated".
6. `finish_reason="stop"` logs no warning.

Cases 1 and 2 matter most long term. Mocking an API response cannot catch drift between the
prompt's stated budget and the token ceiling; only asserting on the constants can.

`assertNoLogs` requires Python 3.10 and the package supports 3.9 (invariant 6). For case 6,
use `assertLogs` with a sentinel record, or guard with `skipIf`. State which you chose.

New file `tests/test_signals.py`: assert `got_request_exception` fires in preserve mode and
does not fire when `EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE=False`. This is what makes Sentry
work and it regresses silently under refactoring.

## 5. CLAUDE.md

Invariant 4: `EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE` now defaults to `True`. Update the
parenthetical to describe the new default path.

Invariant 5: replace with exactly this wording:

> New features must be opt-in via settings flags. Default behavior for existing users must
> not change, with one pre-1.0 exception: `EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE` flips to
> default `True` under `preserve-mode-default` (see roadmap).

Update the test count in `## Commands` to the new total.

## 6. Constraints

- All existing tests pass. Never delete or weaken one to make a change pass.
- Invariant 10: with RAG disabled the prompt must remain byte-identical to the
  traceback-only prompt. The system prompt change applies to both paths equally, so the
  invariant holds, but check whether any existing test asserts an exact prompt string and
  update it if so.
- Invariants 7 and 11: `sanitize_traceback()` still applies to everything leaving the
  process. Do not move or bypass it.
- Invariant 2: no behavior when `DEBUG=False`.
- Run: `DJANGO_SETTINGS_MODULE=test_settings python -m django test tests -v 2`

## 7. Report before finishing

- Which existing tests changed, and why, at the assertion level.
- New test count.
- Whether any existing test asserted an exact prompt string.
- Which approach you used for test case 6.

## 8. On completion

Do not delete this file or edit `docs/roadmap.md` yet. Item 1 stays until the follow-up
README task lands, since that task depends on this spec's decisions.
