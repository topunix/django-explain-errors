# Eval harness

A tool the maintainer runs by hand when a change might affect explanation
quality: a scored RAG-on vs RAG-off comparison across a fixed set of
15 Django failures (`evals/fixtures.py`), run against a small fixture blog
app (`evals/fixture_app/`).

This is **not** part of the test suite. Nothing here runs under
`python -m django test tests`, and nothing here is imported by
`explain_errors/`. It costs money, hits two real API endpoints, and gives
slightly different answers every run.

## What it costs

Every run makes:

- One embeddings call per chunk of `evals/fixture_app/` (roughly two dozen
  chunks, once per run, to build the RAG index) plus one embeddings call per
  RAG-on fixture call, using `EXPLAIN_ERRORS_RAG_EMBED_MODEL` (default
  `text-embedding-3-small`, effectively free at these volumes: well under
  $0.001 per run).
- 30 generator chat completions per `--runs` value of 1 (15 fixtures x
  RAG-off and RAG-on), using `OPENAI_MODEL` (default `gpt-4o-mini`).
- 15 judge chat completions per `--runs` value of 1, using `EVAL_JUDGE_MODEL`.

At `gpt-4o-mini` pricing, the generator side alone costs on the order of a
few cents per `--runs 1` pass. The judge side is not cheaper -- it sends
source excerpts plus both explanations on every comparison, and is the
larger share of the real cost, not a rounding error on top of the
generator. Measured against a live OpenRouter judge model, five `--runs 1`
passes consumed roughly $4.55 of credit combined: call it about eighty
cents a pass, not the two cents an earlier version of this estimate
implied by only counting generator tokens. The harness prints separate
generator and judge token-usage and cost lines at the end of every run so
you don't have to guess at either -- read both before running `--runs` any
higher than 1. **`--runs 20` is real money, not a typo-proof default;
there isn't one.**

## Environment variables

The harness needs three sets of credentials. It never reuses the
generator's settings for the judge -- this is deliberate: the maintainer's
plan is to point the judge at a different provider (OpenRouter) than the
generator, and mixing them would defeat the point of an independent judge.

**Generator** (used by the fixture app's middleware, same as production):

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | required |
| `OPENAI_BASE_URL` | optional, for an OpenAI-compatible endpoint |
| `OPENAI_MODEL` | optional, defaults to `gpt-4o-mini` (set in `evals/fixture_app/settings.py`) |

**Judge** (independent client, see `evals/judge.py`):

| Variable | Purpose |
|---|---|
| `EVAL_JUDGE_API_KEY` | required |
| `EVAL_JUDGE_BASE_URL` | e.g. an OpenRouter URL |
| `EVAL_JUDGE_MODEL` | required, e.g. an OpenRouter model slug |

**RAG embeddings** reuse the generator's `OPENAI_API_KEY` /
`OPENAI_BASE_URL`, same as production (`explain_errors.rag`).

## Running it

From the repo root, with the package's own dependencies installed plus the
`[rag]` extra (`pip install -e ".[rag]"`):

```bash
export OPENAI_API_KEY=...
export EVAL_JUDGE_API_KEY=...
export EVAL_JUDGE_BASE_URL=https://openrouter.ai/api/v1
export EVAL_JUDGE_MODEL=anthropic/claude-...   # any model, just not the generator's

python evals/run.py
```

Each run:

1. Deletes and recreates the fixture app's SQLite database, then seeds it
   (`evals/fixture_app/seed.py`) -- so fixture URLs that assume specific
   row ids stay reliable.
2. Rebuilds the RAG index against `evals/fixture_app/` (via the existing
   `build_error_index` management command's underlying `build_index()`).
3. Hits every fixture URL twice: once with `EXPLAIN_ERRORS_RAG_ENABLED=False`,
   once with it `True`.
4. Asks the judge to compare the two explanations for each fixture, with
   which one it sees as "A" randomized per comparison.
5. Writes `evals/results/<timestamp>.json` (git-ignored) with every call,
   every judgment, the generator and judge model names, and the git SHA.
6. Prints a summary.

### `--runs N`

Repeats every fixture `N` times (default 1), since explanations and judge
verdicts are nondeterministic. Costs scale linearly -- `--runs 5` is five
times the cost above. Higher `--runs` narrows the win-count noise; it does
not change what a single call costs.

### `--fixture NAME`

Runs only the named fixture (see `evals/fixtures.py` for the list), for
debugging one failure without paying for the other 14.

## Reading the summary

Read the win counts **per group**, not just the aggregate -- the aggregate
hides which class of error RAG actually helps with. Fixtures split into two
groups by where the cause lives:

- **Group A** (10 fixtures): the cause lives in the app's own source -- a
  bad queryset lookup, a missing null check, a broken model method --
  something the RAG index can retrieve and RAG-off cannot see at all.
- **Group B** (5 fixtures): the traceback itself already names the cause --
  a typo'd template name, a missing tag library, a bad import -- with no
  extra source context needed to identify it.

See the Results section below for what three runs actually show: RAG-on won
convincingly in both groups, and by a wider margin in group B than in group
A. Group B was designed as a RAG-neutral control; the data says it isn't
one.

Below the win counts:

- **Per-question yes counts**, split by group: how often each side
  correctly identified the cause, pointed at the fix location, proposed a
  working fix, and was written for a learner. Useful for diagnosing *why*
  a side won or lost, not just that it did.
- **Latency**: mean and p50, RAG-off vs RAG-on. This is not a side note --
  `process_exception` blocks the request path today, so p50 here is what
  every real 500 already waits on. It's also load-bearing for the
  `debug page injection` roadmap item's decision rule
  (see `docs/roadmap.md`).
- **Token usage and estimated cost**, printed as separate Generator and
  Judge lines (plus a combined total) so the split between them is
  visible -- the judge is usually the larger of the two, see "What it
  costs" above. Each side is only reported when the corresponding API
  response carried usage data (it always does against the real OpenAI and
  OpenRouter APIs; a local OpenAI-compatible server via `OPENAI_BASE_URL`
  or `EVAL_JUDGE_BASE_URL` may not return `usage`, in which case that line
  prints "not captured"). The cost estimate uses a small hardcoded price
  table in `evals/run.py` (`PRICING_PER_1K_TOKENS`) that will drift out of
  date -- treat it as a rough order of magnitude, not a bill. A model
  (generator or judge) with no entry in that table prints its token counts
  with "no pricing entry for this model" instead of a dollar figure.

A run where more than a third of judge comparisons failed to parse doesn't
print win tallies, per-question yes counts, or claim counts at all -- those
numbers on a mostly-failed sample look like a finding when they're really
noise. Instead the summary prints the failure count, the most common judge
error, and a line stating the run is incomplete. Latency and token usage
still print either way, since those reflect calls actually made, not
judgments actually reached.

A judge response that doesn't parse (see `evals/judge.py:parse_judge_response`)
is recorded as a judge failure, counted separately, and excluded from the
win counts and per-question tallies -- it is never silently dropped and
never counted as a tie.

## Results

First real evidence from this harness: 3 runs against `gpt-4o-mini`
(`evals/results/20260916T235351Z.json`, git SHA
`843da72b74778644c9c1596184e590798b14f8f4`; see also the earlier 1-run
pass, `evals/results/20260916T233441Z.json`, same SHA).

**RAG-on won 39 of 45 judged comparisons** (4 rag-off, 2 tie):

| Group | RAG-on | RAG-off | Tie |
|---|---|---|---|
| A (10 fixtures x 3 runs) | 25 | 3 | 2 |
| B (5 fixtures x 3 runs) | 14 | 1 | 0 |

`points_to_fix_location` is the discriminator, not `identifies_cause` or
`written_for_learner` (both sides answer those correctly almost every
time). RAG-on names the actual file and function; RAG-off, with only the
traceback, usually can't:

| Group | RAG-on says yes | RAG-off says yes |
|---|---|---|
| A | 21 / 30 | 6 / 30 |
| B | 12 / 15 | 8 / 15 |

Per-fixture win counts across the 3 runs:

| Fixture | Group | RAG-on | RAG-off | Tie |
|---|---|---|---|---|
| none_attribute | A | 3 | 0 | 0 |
| str_recursion | A | 3 | 0 | 0 |
| bad_lookup | A | 2 | 0 | 1 |
| missing_profile | A | 3 | 0 | 0 |
| non_unique_get | A | 3 | 0 | 0 |
| unexpected_kwarg | A | 1 | 1 | 1 |
| unvalidated_int | A | 2 | 1 | 0 |
| missing_fk | A | 3 | 0 | 0 |
| missing_post_key | A | 2 | 1 | 0 |
| get_not_404 | A | 3 | 0 | 0 |
| template_typo | B | 2 | 1 | 0 |
| url_name_typo | B | 3 | 0 | 0 |
| missing_tag_library | B | 3 | 0 | 0 |
| import_typo | B | 3 | 0 | 0 |
| unclosed_tag | B | 3 | 0 | 0 |

`unexpected_kwarg` is the only fixture RAG-on didn't win outright -- one
win, one loss, one tie, not a consistent loss. The loss is instructive for
a different reason than it first looks: RAG-on's answer named `post_id`,
which is in fact the correct kwarg for the failing route (confirmed
against `evals/fixture_app/blog/urls.py` and `blog/views.py`), but the
judge only sees the traceback and the known-good facts, neither of which
mentions `post_id` -- so a correct detail RAG-on read from the source
looked, to the judge, indistinguishable from an invented one. See the
judge limitation noted below.

Latency: p50 ~2s on both sides (rag-off 1.82s, rag-on 2.11s across all 90
calls) -- RAG adds no meaningful overhead on top of the generator call
itself. Cost: about $0.02 in generator tokens alone for a full `--runs 3`
pass (54k prompt + 19k completion tokens at `gpt-4o-mini` pricing) --
judge tokens are not included in that figure; see "What it costs" above
for the real combined number.

The plain finding: without access to the project's source, `gpt-4o-mini`
tends to invent plausible-sounding function names, parameters, and fix
steps rather than say it doesn't know. With source access via RAG, it
mostly does not -- in both groups, not just the one RAG was built for.

`missing_post_key` shows RAG-on anchoring on an adjacent retrieved chunk
instead of the one that actually matters: the judge's reasoning states
directly that it redirected the fix to a retrieved template instead of the
view. Retrieval reduces fabrication; it does not eliminate it, and it can
introduce a new kind of error when the wrong chunk ranks high.

**Known limitation of the current judge**: it sees only the traceback and
`expected_cause` / `expected_fix_location`, never the retrieved source. A
correct detail RAG-on pulled from source but that isn't restated in those
known facts -- like `post_id` above -- reads as unverified to the judge in
exactly the same way a genuinely invented detail would. The judge cannot
currently tell "true but not in the known facts" apart from "fabricated."
That's a harness limitation, not a generator finding; see the roadmap item
to give the judge the retrieved chunks.

## Spot-checking claim statuses

`evals/spotcheck.py` is not a verifier -- claims are English, and nothing
short of a human reading them settles whether one is right. What it does
is cheap and mechanical: it pulls every identifier, dotted filename, and
function-call-shaped token out of each claim's text and checks whether
that token literally appears in `evals/fixture_app/blog/`'s source. A
claim the judge marked "contradicted" that nonetheless names something
real in the source is exactly the failure mode worth a second look --
either the judge is wrong, or it's right for a reason the naive token
match can't see. Run it against the latest results file with
`python evals/spotcheck.py`.

On the run of 2026-09-17, it flagged 14 of 363 claims, and all 14 were
correct judgments on manual inspection -- the judge's "contradicted" calls
held up, the spot-check just surfaced them for a human to confirm rather
than proving them wrong. Two examples worth naming: RAG-off asserted
`def post_preview(request):` when the real signature at `views.py:40` is
`def post_preview(request, post_id)`, and RAG-off said
`post_timeago.html` needs `{% load humanize %}` added when line 1 already
has it. Zero for zero on false positives in this run isn't a guarantee for
the next one -- it's a script that flags candidates, not a correctness
proof.

## Files

- `fixture_app/` -- a small, deliberately breakable Django blog. A
  standalone Django project (its own `settings.py`, `urls.py`, `manage.py`),
  not an installed package; `evals/run.py` points `DJANGO_SETTINGS_MODULE`
  at it directly.
- `fixtures.py` -- the fixture registry: 15 records, each one URL that
  triggers one specific, realistic failure.
- `run.py` -- the harness entry point described above.
- `judge.py` -- the judge prompt (`JUDGE_PROMPT_TEMPLATE`, easy to edit)
  and client.
- `spotcheck.py` -- see "Spot-checking claim statuses" above.
- `results/` -- git-ignored. One JSON file per run.
