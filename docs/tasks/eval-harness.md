# Task: eval-harness

Build an evaluation harness that measures explanation quality, with a scored RAG-on vs
RAG-off comparison across a fixed set of Django failures.

Full rationale is in `docs/roadmap.md` item 1. Read it before starting.

Branch from an updated `origin/main`. Do not commit unless asked.

## What this is and is not

This is a tool the maintainer runs by hand when a change might affect explanation quality.
It costs money, hits real APIs, and gives slightly different answers each run.

It is **not** part of the test suite. Nothing under `evals/` runs under
`python -m django test tests`. The test suite stays fully mocked and free.

**You cannot run this harness for real.** There are no API keys in this environment and the
judge endpoint is not reachable. Build it, prove every code path works with mocked API
clients, and the maintainer runs it. Do not fabricate a results file.

## Layout

```
evals/
  README.md            how to run it, what the numbers mean
  fixture_app/         a small Django project that breaks on purpose
    manage.py
    settings.py
    urls.py
    blog/
      models.py
      views.py
      urls.py
      templates/blog/
  fixtures.py          the fixture registry
  run.py               the harness entry point
  judge.py             judge prompt and client
  results/             gitignored
```

## 1. The fixture app

A small blog. Enough real structure that the RAG index has something to retrieve: three or
four models with relations (`Author`, `Post`, `Comment`, `Profile` as a one-to-one on
`Author`), views that exercise them, a few templates. Written the way a learner would write
it, not minimal stubs.

Each fixture is a URL in `blog/urls.py` that triggers one specific failure. The failing code
must be realistic: a plausible mistake in an otherwise-correct view, not a view that exists
only to raise. The RAG chunker splits on top-level functions and classes, so each failing
view should be its own function with a descriptive name.

Use SQLite. The harness creates the database and seeds it with a few rows at startup, so
fixtures that need data have it.

## 2. The fixtures

`fixtures.py` is a list of records:

```python
Fixture(
    name="none_attribute",
    url="/posts/latest/",
    group="A",
    expected_exception="AttributeError",
    expected_cause="Post.objects.filter(...).first() returned None and the view used the "
                   "result without checking",
    expected_fix_location="blog/views.py:latest_post",
)
```

`expected_fix_location` is `file:function`, not a line number, since line numbers shift.

Group A, prediction: RAG helps, because the cause lives in the app's own source.

1. `none_attribute`. `AttributeError` on `None`. View does `.filter().first()` then uses the
   result. Fix in `views.py`.
2. `str_recursion`. `RecursionError`. A model `__str__` calls a property that calls
   `__str__`. Fix in `models.py`.
3. `bad_lookup`. `FieldError`. Queryset lookup uses a keyword that is not a field or a
   valid relation path. Fix in `views.py`.
4. `missing_profile`. `RelatedObjectDoesNotExist`. View accesses `author.profile` for an
   author with no profile row. Fix in `views.py`.
5. `non_unique_get`. `MultipleObjectsReturned`. `.get()` on a non-unique field. Fix in
   `views.py`.
6. `unexpected_kwarg`. `TypeError`. URL pattern captures a kwarg the view signature does not
   accept. Fix in `views.py` or `urls.py`; the traceback does not say which.
7. `unvalidated_int`. `ValueError`. `int(request.GET["page"])` with no validation. Fix in
   `views.py`.
8. `missing_fk`. `IntegrityError`. View creates a `Post` without setting `author`. Fix in
   `views.py`; the traceback points at the database layer.
9. `missing_post_key`. `KeyError`. `request.POST["email"]` on a form that does not send it.
   Fix in `views.py`.
10. `get_not_404`. `Post.DoesNotExist`. `.get()` where `get_object_or_404` was wanted. Fix
    in `views.py`.

Group B, prediction: RAG neutral, because the traceback already says everything.

11. `template_typo`. `TemplateDoesNotExist`. Typo in a template name passed to `render()`.
12. `url_name_typo`. `NoReverseMatch`. Typo in a `{% url %}` tag.
13. `missing_tag_library`. `TemplateSyntaxError`. `{% load humanize %}` with
    `django.contrib.humanize` not in `INSTALLED_APPS`.
14. `import_typo`. `ModuleNotFoundError`. Typo in an import inside `views.py`.
15. `unclosed_tag`. `TemplateSyntaxError`. Missing `{% endif %}`.

Fixture 14 needs care: an import error at module load breaks every view in the file. Put the
bad import inside the failing view's function body so it fails only when that view runs.

## 3. Capturing explanations

Run the fixture app with `EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE=False`. In that mode the
middleware returns a JSON 500 whose body contains the explanation, so the harness reads it
from the response rather than scraping stdout. Hit each fixture URL with `django.test.Client`
with `raise_request_exception=False`.

Set `EXPLAIN_ERRORS_MAX_CALLS` high enough in the fixture settings that the sliding-window
throttle never fires during a run.

Each fixture runs twice per pass: once with `EXPLAIN_ERRORS_RAG_ENABLED=False`, once with it
`True`. The RAG index must be built against `evals/fixture_app/` before the RAG-on pass;
`run.py` does this itself using the existing `build_error_index` command, pointed at the
fixture app via `EXPLAIN_ERRORS_RAG_INCLUDE`.

Record per call: fixture name, RAG on or off, the explanation text, wall-clock latency, and
token usage if available. Assert the exception type in the traceback matches
`expected_exception`, and fail loudly if not, since that means the fixture is broken.

### Token usage

The middleware does not currently expose `response.usage`. Add one `logger.debug` line in
`middleware.py` after the completion call, logging prompt and completion token counts on the
`explain_errors` logger. The harness captures it with a logging handler. One line, one test
in `tests/` that asserts the log record is emitted with the expected fields, mocked as usual.
No other middleware changes.

## 4. The judge

A second model, deliberately different from the generator, compares the two explanations for
one fixture against the fixture's known answer.

Client: OpenAI SDK pointed at `EVAL_JUDGE_BASE_URL` with `EVAL_JUDGE_API_KEY` and
`EVAL_JUDGE_MODEL`, all read from the environment. The maintainer will point this at
OpenRouter. Never reuse the generator's settings for the judge.

The judge sees: the traceback, `expected_cause`, `expected_fix_location`, and two
explanations labelled A and B. **Which of RAG-on and RAG-off is A is randomized per
comparison** and the mapping recorded, so position bias cannot leak into the result.

It answers four questions about each, then picks a winner or declares a tie:

1. Does it correctly identify the cause stated in `expected_cause`?
2. Does it point the developer to `expected_fix_location`, by file and function?
3. Does it propose a fix that would actually resolve the error?
4. Is it written for someone learning Django, rather than assuming they already know?

Response must be JSON: per-explanation yes/no on each question, a winner (`A`, `B`, or
`tie`), and one sentence of reasoning. Parse it strictly; a malformed response is recorded as
a judge failure, not silently dropped.

Draft the judge prompt and include it in your report. It will need iteration once the
maintainer sees real output, so make it a module-level string in `judge.py` that is easy to
edit.

## 5. Output

`run.py` writes `evals/results/<timestamp>.json` containing every call, every judgment, the
generator and judge model names, and the git SHA. It then prints a summary:

- Wins for RAG-on, RAG-off, and ties, split by group A and group B.
- Per-question yes counts for each side, split by group.
- Mean and p50 latency for each side.
- Total token usage and estimated cost if usage was captured.

The interesting comparison is "RAG won 8 of 10 in group A and 1 of 5 in group B," not the
aggregate. Make the group split the headline.

`--runs N` repeats every fixture N times, since output is nondeterministic. Default 1.
`--fixture NAME` runs one fixture, for debugging.

`evals/results/` goes in `.gitignore`.

## 6. Tests

The harness plumbing gets unit tests in `tests/`, fully mocked, because it is code that can
be wrong:

- Fixture registry: every fixture has all fields, names are unique, groups are `A` or `B`.
- Every fixture URL resolves in the fixture app's URLconf.
- Randomization: over many comparisons, RAG-on lands in position A roughly half the time,
  and the recorded mapping inverts correctly.
- Judge response parsing: valid JSON parses, malformed JSON is recorded as a failure.
- Tally: given a hand-built list of judgments, the per-group counts are right.

Plus one end-to-end run with both the generator and the judge mocked, asserting a results
file is written with the expected shape. This is the test that proves you built something
runnable without being able to run it.

## 7. Constraints

- Nothing under `evals/` is imported by anything under `explain_errors/` or `tests/` except
  the tests named above.
- The fixture app must not be picked up by the package build. Check `find_packages`
  excludes it and `MANIFEST.in` does not include it.
- The only change to `explain_errors/` is the one usage log line in section 3.
- All existing tests pass. Run:
  `DJANGO_SETTINGS_MODULE=test_settings python -m django test tests -v 2`

## 8. evals/README.md

How to set the environment variables, how to run it, what `--runs` does, and how to read the
summary. State plainly that it costs money and roughly how much per run at current pricing,
so nobody runs `--runs 20` by accident.

## 9. Report before finishing

- The judge prompt.
- Which fixtures you had to adjust from the spec and why, with particular attention to
  whether each still fails with the expected exception type.
- New test count.
- Anything you could not verify because the real APIs were unavailable.

## 10. On completion

Do not delete this file or edit `docs/roadmap.md`. The roadmap item closes when the
maintainer has run the harness for real and has a result, not when the code merges. That is
a separate follow-up.
