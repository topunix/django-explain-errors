# judge-retrieved-context

## Goal

Let the eval judge verify claims that come from project configuration the generator
can read through RAG but the judge was not shown, and record the judge's evidence per
fixture in the results JSON.

Today `_build_judge_source` in `evals/run.py` sends the judge only the failing function,
its `urls.py` route, and any templates the fixture names. For `missing_tag_library`, whose
`expected_fix_location` is `fixture_app/settings.py:INSTALLED_APPS`, the judge never sees
`INSTALLED_APPS`, so a correct RAG-on claim about it is marked "absent" or "contradicted"
(Evals #9, missing_tag_library run 1). The judge source section is also rebuilt on every
run and never persisted, so a score change cannot be checked against a change in the
judge's evidence.

## Scope

### In

1. `_build_judge_source(fixture, exception)` appends one extra `(label, content)` pair when
   the fixture's `expected_fix_location` points at a settings file:
   - Parse `expected_fix_location` as `<path>:<name>` (split on the last `:`).
   - It applies only when the basename of `<path>` is `settings.py`. Every other
     location (`blog/views.py:...`, `blog/models.py:...`, template paths) adds nothing.
   - Resolve `<path>` relative to `EVALS_DIR` (so `fixture_app/settings.py` is
     `evals/fixture_app/settings.py`, i.e. `FIXTURE_APP_DIR / "settings.py"`).
   - Extract only the named top-level setting: the exact source text (via
     `ast.get_source_segment`) of the module-level `ast.Assign` or `ast.AnnAssign` whose
     target is an `ast.Name` equal to `<name>`. Not the whole file, not nested
     assignments. Put this in a new pure helper `_extract_setting(settings_text, name)`
     that returns the source text or `None`, next to `_extract_url_pattern`.
   - Label: `f"{path} ({name})"`, for missing_tag_library exactly
     `fixture_app/settings.py (INSTALLED_APPS)`.
   - Raise `RuntimeError` (same style as the existing "out of sync" errors) if a
     settings location has no `:<name>` part, the file does not exist, or the setting is
     not found. Never fall back to sending the whole file.
   - Appended after the templates, so existing entries keep their order.
2. The source section stays symmetric: it is built once per fixture and the same text goes
   to the judge for both sides of every comparison, as today. Nothing RAG-on retrieved is
   added per side.
3. Record the judge's evidence in the results JSON. Add a new top-level key
   `"judge_sources"` to the dict written by `main()`: a mapping from fixture name to
   `{"sha256": <hex sha256 of the UTF-8 source_text>, "text": <source_text>}`, one entry per
   fixture that was run, where `source_text` is exactly the string passed to
   `evals.judge.compare` for that fixture
   (`format_source_section(_build_judge_source(...))`).
   - Implementation shape: factor the per-fixture build into a function
     `build_judge_sources(fixtures, exceptions_by_fixture)` returning
     `{fixture_name: source_text}`. `judge_all` gains an optional keyword argument
     `source_texts=None`; when `None` it calls `build_judge_sources` itself (existing
     callers and tests keep working); when given it uses those texts. `main()` builds the
     texts once, passes them to `judge_all`, and writes `judge_sources` from the same
     dict, so the recorded text is byte-identical to what the judge received.
   - The key is additive; readers of older results files must not break (check
     `evals/spotcheck.py` and any other reader of results files).
4. Update docstrings that describe the source section's contents:
   `_build_judge_source`, `format_source_section` in `evals/judge.py`, and the comment on
   `Fixture.templates` in `evals/fixtures.py`, so they mention the named setting.

### Out

- Passing RAG-on's retrieved chunks to the judge with the RAG-on answer only
  (Rejected in the roadmap: reveals which side is RAG-on and undermines the A/B
  position randomization).
- Sending any whole config file, or any setting other than the one named by
  `expected_fix_location`.
- Changing `expected_fix_location` values or fixture content.
- Running the eval harness against a live provider, or any new baseline run.
- The overlap analysis and retrieval-arms work (separate roadmap items).
- Any change to the `explain_errors` package itself.

## Files touched

- `evals/run.py`
- `evals/judge.py` (docstring only)
- `evals/fixtures.py` (comment only)
- `tests/_eval_judge_source_helper.py`
- `tests/test_eval_run.py`
- `evals/README.md`
- `evals/METHODOLOGY.md`

## Acceptance criteria

1. `DJANGO_SETTINGS_MODULE=test_settings python -m django test tests -v 2` passes, and the
   test count is at least 407 (the count on `origin/main` at branch time) plus the new
   tests.
2. Reading `evals/run.py`: `_build_judge_source` adds the setting named by
   `expected_fix_location` when that location is a `settings.py` file, using
   `_extract_setting`, labelled `<path> (<name>)`.
3. A test drives missing_tag_library through the fixture app (subprocess helper) and
   asserts its judge source contains the label `fixture_app/settings.py (INSTALLED_APPS)`
   whose content starts with `INSTALLED_APPS = [` and contains `"django.contrib.auth"`, and
   that no entry contains `SECRET_KEY`, `DATABASES`, or `MIDDLEWARE`.
4. A test asserts that a fixture whose `expected_fix_location` is under `blog/` gets no
   `settings.py` entry.
5. `grep -n '"judge_sources"' evals/run.py` shows the key written into the results dict in
   `main()`.
6. A test runs the existing mocked `main()` path and asserts the written results JSON has
   `judge_sources` keyed by every run fixture's name, each `sha256` equal to
   `hashlib.sha256(text.encode("utf-8")).hexdigest()`, and each `text` equal to the
   `source_text` argument the mocked `compare` received for that fixture.
7. `git diff origin/main --stat -- explain_errors setup.py` is empty.
8. `git diff origin/main -- docs/roadmap.md` is empty.

## Tests to add

- `_extract_setting` unit tests (pure, in `tests/test_eval_run.py`): returns the exact
  segment for a top-level list assignment; handles an annotated assignment; returns `None`
  for a missing name; ignores an assignment of the same name nested inside a function or
  class; does not return a neighbouring setting's text.
- Subprocess helper: add `missing_tag_library` to the fixture names in
  `tests/_eval_judge_source_helper.py`, then add assertions in `BuildJudgeSourceTest` per
  acceptance criteria 3 and 4 (use an existing blog-located fixture for 4).
- Error path: a fixture-like object whose `expected_fix_location` is a settings file with
  no `:name`, or names a setting that does not exist, raises `RuntimeError`. Test this
  without the fixture app if possible (for example by testing the location-resolving code
  as its own small function), otherwise via the subprocess helper.
- Results JSON: extend `test_mocked_run_writes_results_file_with_expected_shape` or add a
  sibling test per acceptance criterion 6.
- `judge_all` with explicit `source_texts`: the given texts are the ones passed to
  `compare`, and `_build_judge_source` is not called.

All OpenAI and judge calls stay mocked. No network.

## Docs to update

- `evals/README.md`: the Limitations paragraph about `_build_judge_source` mentions that
  for a fixture whose fix location is a setting, the judge also sees that one named
  setting (extracted alone, not the whole file). Where the README lists results JSON
  fields, add `judge_sources` (fixture name to `sha256` and `text`), marked additive so
  older results files lack it.
- `evals/METHODOLOGY.md`: a dated (2026-11-18) entry recording that the judge source
  section changed (named setting for settings-located fixtures), that judge inputs are now
  recorded per fixture, and that results from this commit on are not directly comparable
  with the gpt-6-luna baseline (`20261022T005116Z.json`) or Evals #9; a new baseline run
  is required.
- No `CHANGELOG.md` entry: the eval harness is not part of the published package and the
  package's user-facing behavior does not change.
- No em dashes in any doc text.

## Roadmap item addressed

judge-retrieved-context (`docs/roadmap.md`, Sequenced queue item 1). Do not edit
`docs/roadmap.md`.

## Maintainer follow-up

- Run a new baseline on `main` after merge (generator `gpt-6-luna`, judge
  `anthropic/claude-sonnet-5`, runs=3, seed=42, all 15 fixtures), changing no other
  variable. This satisfies the roadmap's second Verify marker (results JSON records the
  judge source section per fixture) and becomes the comparison point for later items.
- Re-check judge-calibration-set agreement, since the judge prompt inputs changed.
- Do not overwrite or delete the Evals #9 or gpt-6-luna baseline results files.
