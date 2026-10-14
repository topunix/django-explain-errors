# Eval methodology notes

History of how the eval questions evolved and harness bugs that affected past results. Current results and limitations are in README.md.

**The truncation caveat, resolved.** Before the
`app-frame-preserving truncation` fix, `OPENAI_MAX_TRACEBACK_CHARS` kept
only the tail of the raw traceback, which for a Django ORM stack
routinely dropped the application frames and left only library
internals. Part of RAG-off's `points_to_fix_location` disadvantage was
never about missing source access. RAG-off was never shown the frame
naming the failing function at all. After the fix, RAG-off's
`points_to_fix_location` in group A rose from 6 of 30 to the 13 in the README.md Results table,
while RAG-on held roughly steady, around 21 to 26. Roughly half of the
old location gap was the trim bug; the rest is real.

**The no_fabrication arc.** `no_fabrication` wasn't part of the judge
prompt from the start. It was added because the judge kept deciding
fabrication implicitly (by default, whenever the other four questions
tied), which measured nothing. It then failed twice before it worked: a
judge given only the traceback and the known-good facts defaulted to
calling any specific-but-unfamiliar detail invented, since it had nothing
to check it against; a judge later given whole source modules ignored
them anyway, because answering "unverified" is cheaper than reading 300
mostly-irrelevant lines. Neither version was actually measuring
fabrication. Only forcing a per-claim verdict (verified / contradicted /
absent, checked against a small, relevant source excerpt), with
`no_fabrication` *derived* from those verdicts in the parser rather than
asked as a direct yes/no, made the question measure what its name
claimed. The numbers inverted once that landed: RAG-on went from
*appearing* to fabricate more, an artifact of the judge having nothing to
check its source-grounded details against, to measurably fabricating
less, as the Results table in README.md shows.

**A source-extraction bug affecting `str_recursion`, now fixed.**
`_extract_function_source` used to pick a function's source by name alone
(the first `def <name>` it found walking the module, full stop). For
`str_recursion`, whose `RecursionError` alternates between
`Comment.__str__` and `Comment.summary` on every frame, which one lands
as the innermost project frame depends on how deep the call stack already
was when the recursion started (Django's own dispatch and template
rendering depth), not on anything about the fixture itself. Whenever it
resolved to `__str__`, the name-only lookup returned `Author.__str__`
(the first `__str__` in `models.py`, textually, and an unrelated one-line
method) instead of `Comment.__str__`, which is where the actual
recursion lives. `_extract_function_source` now also takes the frame's
line number and matches the definition whose body contains it, so it
returns the method that actually ran regardless of which side of the
recursion the traceback happened to end on. Any past run where this fired
would have shown the judge a materially smaller, wrong-class excerpt for
`str_recursion` (one line instead of the four-line property plus
`__str__` that actually recurse) on both RAG-on and RAG-off sides
alike (this path is shared, not RAG-specific), on whatever fraction of
comparisons hit the unlucky parity. The Results numbers in README.md predate
this fix and were not re-run to quantify the effect; treat any
`str_recursion`-specific finding in them with that in mind. The fix
applies to every fixture, not just this one. Any two classes in
`blog/models.py` or `blog/views.py` that happen to share a method name
were equally exposed.

**Same fixture, a second gap: only half the loop.** Disambiguating by
class fixed which method came back, but not how much of the cycle. When
the frame resolves to `Comment.__str__`, the extracted source used to
stop at `return self.summary`. The judge was asked to verify claims
about a recursion loop while looking at only one side of it.
`_extract_function_source` now also pulls in, one level deep, any other
method on the same class that the matched method's body reaches through
an explicit `self.<name>` attribute access, so a `__str__`-anchored
extraction now includes `summary` too. This is not fully symmetric:
`Comment.summary` reaches back into `__str__` through `str(self)`, a
call, not a `self.__str__` attribute access, so the reverse case (frame
resolves to `summary`) still shows only `summary` alone. Whether that
residual gap is worth closing depends on how often that parity actually
occurs in practice (unmeasured, same as above).

**Fixture authoring rules.** Everything under `evals/fixture_app/` is indexed
for RAG-on retrieval (`.py`, `.html` and `.txt` files, minus `migrations`
and the other default exclusions). Anything written there can be returned to
the model under test, so a fixture that states its own fix lets RAG-on win by
reading the answer key. An audit of the first published runs found exactly
that: a commented-out correct queryset on `bad_lookup`, a comment naming the
recursion and its fix on `str_recursion`, and seed-data comments describing
the data condition behind three other fixtures. When adding or editing a
fixture:

- No comment, docstring, or dead code inside `evals/fixture_app/` may state
  or hint at the fix. Docstrings that describe what a view is for are fine.
- No bug-describing comments in seed data. Seed records should read as
  ordinary data.
- No harness meta-text inside `evals/fixture_app/`: nothing that mentions the
  harness, RAG, RAG-on or RAG-off, evals, or the fixtures registry. Put that
  explanation in this file instead.
- Ground truth (`expected_cause`, `expected_fix_location`) stays in
  `evals/fixtures.py`, outside the indexed directory.
- Correct patterns that appear naturally elsewhere in the app (for example
  `get_object_or_404` in sibling views) are realistic grounding and may stay.

**Fixture app configuration notes.** These explain choices in
`evals/fixture_app/` that used to live as comments and docstrings inside it.
They were moved here because everything in that directory is indexed for
RAG-on retrieval, so harness-describing text there is visible to the model
under test (see "Fixture authoring rules" below).

- The fixture app is a small, deliberately breakable Django blog. It is not
  a real project; it exists so the harness has realistic tracebacks to feed
  to the middleware.
- `seed.py` runs once per harness run against a freshly migrated (empty)
  SQLite database, so the autoincrement ids assumed by the URLs in
  `evals/fixtures.py` (for example author id 2, post id 1) are stable run
  over run.
- `manage.py` is only for poking at the fixture app by hand (for example
  `python manage.py runserver`). The harness itself (`run.py`) does not shell
  out to it. It calls `django.setup()` and Django's management commands
  directly.
- `EXPLAIN_ERRORS_MAX_CALLS = 10_000`: the harness drives many fixtures in
  quick succession, and the sliding-window throttle must never fire mid-run.
- `EXPLAIN_ERRORS_DEDUP = False`: one middleware instance serves every
  fixture and every `--runs` repeat in a pass, so dedup would replay a cached
  explanation instead of generating a fresh one and corrupt the comparison.
- `EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE = False`: the JSON 500 body carries the
  explanation text, which is how the harness reads it back out. `run.py`
  overrides it per pass. The on-disk value is kept so the fixture app is also
  usable interactively.
- `EXPLAIN_ERRORS_RAG_ENABLED = False`: RAG is off by default. `run.py` flips
  it on for the RAG-on pass and points `EXPLAIN_ERRORS_RAG_INCLUDE` at the
  fixture app directory before building the index.
