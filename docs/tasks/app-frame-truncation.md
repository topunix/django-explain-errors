# Task: app-frame-truncation

Trim tracebacks by frame, keeping the application's own frames, instead of slicing the last
N characters.

Rationale is in `docs/roadmap.md` item 1. Read it, and read the `missing_fk` example in the
main README's "Before / after" section, which shows the defect: a 3000-character tail of a
Django ORM stack contains no application code at all.

Branch from an updated `origin/main`. Do not commit unless asked.

## 1. The defect

`middleware.py` line 133 onward:

```python
tb = traceback.format_exc()
max_tb_chars = getattr(settings, "OPENAI_MAX_TRACEBACK_CHARS", 3000)
if len(tb) > max_tb_chars:
    tb = "...(truncated)...\n" + tb[-max_tb_chars:]
```

For a Django stack, the application frame is near the top and the ORM, template engine, and
database driver frames pile up below it. Keeping the tail keeps the internals and drops the
one frame that names the failing function. Every explanation is affected, RAG on or off.

## 2. The change

New module `explain_errors/tracebacks.py` with one public function:

```python
def format_traceback(exc: BaseException, max_chars: int) -> str
```

Behavior:

- Extract frames with `traceback.extract_tb(exc.__traceback__)`. Classify each frame as
  library or application.
- Always keep the exception header line (type and message) and every application frame.
- Spend whatever budget remains on library frames, nearest the raise point first, since
  those are the most relevant internals.
- Where library frames are dropped, insert one line in their place:
  `  ... N library frames omitted ...`
- Render in standard Python traceback format so the model sees what it is used to. Use
  `traceback.format_list` on the kept `FrameSummary` objects plus
  `traceback.format_exception_only` for the header.
- If the result still exceeds `max_chars` because the application frames alone are too
  long, fall back to the current behavior: keep the tail. Application code should win over
  the budget only up to a point.
- If `exc.__traceback__` is `None`, fall back to `traceback.format_exc()` with the current
  tail-slice.

`process_exception` calls `format_traceback(exception, max_tb_chars)` in place of the
current three lines. `sanitize_traceback()` still runs on the result, after, exactly as now.
Do not rename or move `sanitize_traceback`; the eval harness spies on it by that name.

## 3. Classifying frames

A frame is library if its filename is under any of:

- a `site-packages` or `dist-packages` directory
- the standard library, via `sysconfig.get_paths()["stdlib"]` and `["platstdlib"]`
- Django's own package directory, `os.path.dirname(django.__file__)`

Everything else is application. No new setting. Do not use `BASE_DIR`: a project's
virtualenv commonly lives inside it, so "under `BASE_DIR`" would classify installed packages
as application code. The site-packages check must take precedence over any path-prefix
logic.

Editable installs (`pip install -e`) will classify as application. That is acceptable and
arguably correct; note it in the report.

## 4. Tests

New file `tests/test_tracebacks.py`. `SimpleTestCase`, no network.

1. A real exception raised through a nested call in the test module: the test module's own
   frames are kept and appear in the output.
2. Classification: construct `FrameSummary` objects by hand with filenames under a fake
   `site-packages` path, under the stdlib path, under Django's path, and under a plain
   project path. Assert the first three are library and the last is application.
3. The site-packages-inside-project case: a filename like `/proj/.venv/lib/.../site-packages/x.py`
   is library, even though it is under `/proj/`.
4. Budget: with a tiny `max_chars`, library frames are dropped and the omitted-frames line
   appears, while application frames are retained.
5. Over-budget fallback: when application frames alone exceed `max_chars`, the output ends
   with the tail of the traceback and starts with `...(truncated)...`.
6. `exc.__traceback__ is None` falls back to the tail-slice path without raising.
7. The exception header line is always present, on every path above.

Existing tests: at least `test_rag.py::test_rag_disabled_by_default_prompt_is_byte_identical`
asserts the user-message prompt shape. The traceback content changes, but both RAG paths
receive the same traceback, so invariant 10 holds. Confirm rather than assume, and if the
test constructs its expected string from `traceback.format_exc()`, update it to use
`format_traceback` so it keeps asserting the invariant rather than the old formatting.

## 5. Constraints

- Invariants 7 and 11: `sanitize_traceback()` still applies to everything that leaves the
  process. The new function runs before it, never after.
- Invariant 2: no behavior when `DEBUG=False`.
- `OPENAI_MAX_TRACEBACK_CHARS` keeps its name and meaning: total character budget.
- All existing tests pass. Never delete or weaken one to make a change pass.
- Run: `DJANGO_SETTINGS_MODULE=test_settings python -m django test tests -v 2`

## 6. Documentation

README Configuration table, `OPENAI_MAX_TRACEBACK_CHARS` row: replace "trimmed to its last N
characters" with a description of the new behavior. Application frames are always kept;
library frames fill the remaining budget nearest the error first; the total stays under N.

Do not touch the eval results sections in either README. Those change after the maintainer
reruns the harness.

## 7. Eval

You cannot run the harness. The maintainer runs `python evals/run.py --runs 3` after merge
and compares against `evals/results/20260916T235351Z.json`. The interesting number is
RAG-off `points_to_fix_location`, previously 6 of 30 and 8 of 15. If it rises substantially,
part of the RAG gap was the trim, and both READMEs get restated.

Also check whether the RAG retrieval query is built from the traceback text. If it is, better
frames mean a better query, which would improve RAG-on as well. Say what you found.

## 8. Report before finishing

- New test count.
- Whether any existing test needed updating, and why at the assertion level.
- Whether the RAG retrieval query uses the traceback, and what that implies for the rerun.
- Any case where the classifier's heuristic seems likely to misfire.

## 9. On completion

Do not delete this file or edit `docs/roadmap.md`. The item closes when the maintainer has
rerun the harness and the eval sections are restated. Separate follow-up.
