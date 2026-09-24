# Task: print-stdout-setting

Let developers turn off the stdout copy of the explanation. With debug page
injection on by default, browser-first users otherwise see every explanation
twice.

## 1. Setting

`EXPLAIN_ERRORS_PRINT_STDOUT`, default `True`. Default behavior is unchanged.
Read at call time with `getattr(settings, ..., True)`, matching the other
EXPLAIN_ERRORS_ flags.

## 2. Behavior

- When `False`, skip only the `print("Error Explanation by OpenAI:\n", ...)`
  call in `process_exception`.
- The failure message (`print("Failed to get an explanation from OpenAI:", e)`)
  always prints. A failure must stay visible regardless of this setting.
- Debug page injection and the JSON 500 response are unaffected.
- The explanation is still produced (the API is still called) when the setting
  is False.

## 3. Startup warning

In `__init__`, inside the existing `if settings.DEBUG:` block: if
`EXPLAIN_ERRORS_PRINT_STDOUT` is False, `EXPLAIN_ERRORS_INJECT_DEBUG_PAGE` is
False, and `EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE` is True, log one
`logger.warning`: explanations will be generated but not shown anywhere.
No warning in any other combination. The JSON 500 response carries the
explanation, so PRESERVE_DEBUG_PAGE=False is a visible channel.

## 4. Tests

Mock all OpenAI calls. Use the request-cycle pattern from
tests/test_debug_page.py (Client with raise_request_exception=False,
AsyncClient, override_settings(DEBUG=True), client built inside the override).

- default: explanation printed to stdout
- PRINT_STDOUT=False: explanation not printed; banner still present
- PRINT_STDOUT=False and OpenAI raises: failure message still printed
- PRINT_STDOUT=False with PRESERVE_DEBUG_PAGE=False: JSON 500 still contains
  the explanation
- startup warning logged for PRINT=False, INJECT=False, PRESERVE=True
- no startup warning for PRINT=False with INJECT=True, or with PRESERVE=False
- async view with PRINT_STDOUT=False: not printed, banner present

## 5. Docs

- README Features: replace
  "- Always prints the explanation to stdout, for terminal workflows and logs"
  with
  "- Prints the explanation to stdout, for terminal workflows and logs
    (`EXPLAIN_ERRORS_PRINT_STDOUT`, on by default)"
- README Configuration table: add a row for `EXPLAIN_ERRORS_PRINT_STDOUT`
  after `EXPLAIN_ERRORS_INJECT_DEBUG_PAGE`. State the default, that failure
  messages always print, and that stdout is the only channel for non-HTML
  requests (API clients, fetch/HTMX), so turn it off only in browser-first
  workflows.
- CLAUDE.md: add the setting to invariant 4's list.
- CLAUDE.md project description: replace "a plain-language explanation to
  stdout" so it says the explanation is shown on Django's debug page and
  printed to stdout (each controllable by its setting).

## 6. Out of scope

Switching print() to logging, and any change to the message format.
