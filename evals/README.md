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
few cents -- about $0.02 for a full `--runs 3` pass. The judge side is not
cheaper; it is the real cost. A `--runs 3` pass measured against a live
OpenRouter judge (`anthropic/claude-sonnet-5`) recorded 107,805 judge
prompt tokens and 114,120 judge completion tokens, and OpenRouter billed
$1.36 for it. Combined with the ~$0.02 of generator tokens for that same
pass, **one `--runs 3` pass costs about $1.37 total** -- roughly two cents
generator, roughly $1.35 judge -- not the two cents an earlier version of
this estimate implied by counting generator tokens alone.

Notice the judge's completion tokens (114k) outweigh its own prompt tokens
(108k), even though the judge receives far more input per call (source
excerpts plus two full explanations) than it produces. The reason is what
it's asked to produce: a claim list per explanation, not a short verdict,
and output tokens are priced several times higher than input ($10.00 vs
$2.00 per 1M tokens for the OpenRouter judge model measured here). That
completion-token volume, not the larger prompt side, is what actually
drives the bill -- and it scales with `--runs` the same way the win counts
do. The harness prints separate generator and judge token-usage and cost
lines at the end of every run so you don't have to guess at either --
anyone considering `--runs` above 3 should read the $1.37 figure above
first. **`--runs 20` is real money, not a typo-proof default; there isn't
one.**

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

Final numbers, from the 2026-09-20 run (`evals/results/20260920T204005Z.json`):
3 runs across all 15 fixtures, 45 comparisons, 2 judge failures, 43 scored.

**RAG-on won 35 of 43 scored comparisons** (5 rag-off, 3 tie).

`identifies_cause` and `written_for_learner` aren't shown below -- both
sides answer those correctly almost every time, so they don't
discriminate. `points_to_fix_location`, `no_fabrication`, and
`fix_would_work` do:

| Question | Group | RAG-on yes | RAG-off yes |
|---|---|---|---|
| `points_to_fix_location` | A | 26 | 13 |
| `points_to_fix_location` | B | 11 | 7 |
| `no_fabrication` | A | 26 | 17 |
| `no_fabrication` | B | 14 | 5 |
| `fix_would_work` | A | 28 | 22 |
| `fix_would_work` | B | 14 | 12 |

Claims checked per side: 109 (RAG-on) vs 104 (RAG-off) in group A, 56 vs
57 in group B -- both sides state plenty of checkable specifics; the
judge isn't defaulting to an empty claims list on either side.

Latency: p50 2.12s RAG-off, 2.22s RAG-on -- RAG adds no meaningful
overhead on top of the generator call itself.

**The truncation caveat, resolved.** Earlier versions of this section
carried an unresolved caveat, twice, instead of settling it: before the
`app-frame-preserving truncation` fix, `OPENAI_MAX_TRACEBACK_CHARS` kept
only the tail of the raw traceback, which for a Django ORM stack
routinely dropped the application frames and left only library
internals. Part of RAG-off's `points_to_fix_location` disadvantage was
never about missing source access -- RAG-off was never shown the frame
naming the failing function at all. After the fix, RAG-off's
`points_to_fix_location` in group A rose from 6 of 30 to the 13 above,
while RAG-on held roughly steady, around 21 to 26. Roughly half of the
old location gap was the trim bug; the rest is real.

**The no_fabrication arc.** `no_fabrication` wasn't part of the judge
prompt from the start. It was added because the judge kept deciding
fabrication implicitly -- by default, whenever the other four questions
tied -- which measured nothing. It then failed twice before it worked: a
judge given only the traceback and the known-good facts defaulted to
calling any specific-but-unfamiliar detail invented, since it had nothing
to check it against; a judge later given whole source modules ignored
them anyway, because answering "unverified" is cheaper than reading 300
mostly-irrelevant lines. Neither version was actually measuring
fabrication. Only forcing a per-claim verdict -- verified / contradicted /
absent, checked against a small, relevant source excerpt, with
`no_fabrication` *derived* from those verdicts in the parser rather than
asked as a direct yes/no -- made the question measure what its name
claimed. The numbers inverted once that landed: RAG-on went from
*appearing* to fabricate more, an artifact of the judge having nothing to
check its source-grounded details against, to measurably fabricating
less, as the table above shows.

**Two limitations remain.** The judge is shown the failing function's own
source (`_build_judge_source` in `evals/run.py`) -- the same source
RAG-on's retriever draws from -- so part of RAG-on's `no_fabrication`
advantage is judge and generator overlapping on material RAG-off never
sees, not necessarily RAG-on being more careful. And claim statuses are
spot-checked, not exhaustively audited: `evals/spotcheck.py` (see below)
flagged 14 of 363 claims on the 2026-09-17 run and all 14 held up on
manual inspection -- a sample that didn't turn up a false positive, not a
proof that none exists.

From the earlier `evals/results/20260916T235351Z.json` run: the judge's
own reasoning showed RAG-on anchoring on an adjacent retrieved chunk for
`missing_post_key`, redirecting the fix to a retrieved template instead
of the view. Retrieval reduces fabrication; it does not eliminate it, and
a wrong chunk ranking high introduces its own kind of error. Whether that
recurs across more fixtures is exactly what the roadmap's "Retrieval
anchoring" item would test (see `docs/roadmap.md`).

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
