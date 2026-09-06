# Task: Preserve Django's debug page (opt-in)

One-off task spec. Execute, then delete or archive this file. Do not
merge this content into CLAUDE.md except where noted at the end.

## Context

`process_exception()` in `explain_errors/middleware.py` always returns
`JsonResponse({"error": ...}, status=500)`, replacing Django's technical
debug page (interactive traceback, local vars). Add an opt-in setting
that keeps the stdout explanation but lets the original exception
propagate so Django renders its standard debug page.

New setting: `EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE`, default `False`.
Default behavior must remain byte-identical (CLAUDE.md invariant 5).

## Files to change

- `explain_errors/middleware.py` (only file with logic changes)
- `tests/test_middleware.py` (new test cases)
- `README.md` (Configuration table + Usage note)
- `CLAUDE.md` (settings list, test count)

## Implementation

1. In `process_exception()`, at the final return (currently the
   `return JsonResponse(...)` at the end of the method): when
   `getattr(settings, "EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE", False)` is
   true, return `None` instead of the `JsonResponse`. Everything above
   (throttle, sanitize, RAG, OpenAI call, stdout print) is unchanged.

2. In `_sync_handler()`, the except block currently reads:
       except Exception as exception:
           return self.process_exception(request, exception)
   Change to re-raise when no response is produced:
       except Exception as exception:
           response = self.process_exception(request, exception)
           if response is None:
               raise
           return response

3. In `__acall__()`, apply the same pattern around the existing
   `await sync_to_async(self.process_exception)(request, exception)`
   call. A bare `raise` inside the except block re-raises the original
   exception; do not wrap or re-instantiate it.

4. Note: this also makes the `DEBUG=False` path re-raise instead of
   returning `None` from the handler, which aligns with invariant 2
   (inert in production). This is intentional. If an existing test
   asserts the old `None` behavior at the handler level, stop and flag
   it rather than weakening the test.

## Tests to add (tests/test_middleware.py)

Use the existing patterns in this file: `override_settings`, mocked
OpenAI client, `RequestFactory` / `AsyncRequestFactory`, a view that
raises `ValueError("boom")`.

- `test_preserve_flag_reraises_original_exception_sync`:
  DEBUG=True, flag True. Calling the middleware raises `ValueError`
  (assert same exception instance or class + message).
- `test_preserve_flag_reraises_original_exception_async`:
  same assertion through the async path (`IsolatedAsyncioTestCase`
  style already used in this suite).
- `test_preserve_flag_still_prints_explanation`:
  flag True, mock client returns a canned explanation, capture stdout,
  assert the explanation text is printed before the re-raise.
- `test_preserve_flag_default_off_returns_json_500`:
  flag absent, assert the existing `JsonResponse` 500 behavior.
- `test_debug_false_handler_reraises`:
  DEBUG=False, exception propagates through the handler untouched.

## Docs

- README Configuration table, new row:
  `EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE` | No | When True, the middleware
  prints the explanation to stdout and re-raises the exception so
  Django renders its standard debug page instead of a JSON 500.
  Defaults to False.
- README Usage step 2: add one sentence pointing to the setting.

## CLAUDE.md (durable changes only)

- Add `EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE` to the settings that must
  keep working (invariant 4 area).
- Update the test count in the Commands section to the new total.

## Done when

- `DJANGO_SETTINGS_MODULE=test_settings python -m django test tests -v 2`
  passes with zero failures, no existing test modified or deleted
- Flag off: behavior identical to current main
- Flag on: sync and async paths both surface the original exception
  and still print the explanation
