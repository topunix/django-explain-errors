# Eval harness

Does grounding an error explanation in your project's own source code make it
better? This harness measures that.

django-explain-errors can explain an exception in two modes:

- **RAG-off:** the model sees the Django traceback (sanitized and
  truncated) plus a fixed instruction prompt, and no project source. It has
  to guess what the surrounding code looks like.
- **RAG-on:** the model also sees excerpts of your project's source, found
  by meaning (vector similarity, not keyword matching) in a local index.
  The search query is the exception type, its message, and the code around
  the failing line.

The harness triggers 15 known failures in a small fixture blog app, collects
both explanations for each, and asks an independent judge model to pick the
better one and answer specific questions: did it find the cause, point to
the fix location, propose a working fix, avoid fabricated details, and
explain it for a learner. The goal is to show whether RAG is worth enabling,
and for which kinds of errors.

**TL;DR**

- **Result:** RAG-on won 35 of 43 scored comparisons (5 RAG-off, 3 ties, 3 runs).
- **Biggest gains:** `points_to_fix_location` and `no_fabrication`.
- **Latency:** p50 2.22s RAG-on vs 2.12s RAG-off.
- **Cost:** about $1.37 per `--runs 3` pass, mostly the judge.

This is **not** part of the test suite. Nothing here runs under
`python -m django test tests`, and nothing here is imported by
`explain_errors/`. It costs money, hits two real APIs, and is
nondeterministic.

## How RAG-on retrieves source

- **Chunking:** Python files are split into top-level functions and
  classes. Templates and text files (.html, .txt), and any Python file
  that cannot be parsed, are split into 80-line windows with 20 lines of
  overlap.
- **Index:** each chunk is sanitized, embedded, and stored in sqlite-vec.
- **Query:** the exception type, message, and 5 lines of source on each
  side of the innermost project frame, sanitized and embedded the same way.
- **Selection:** the 4 nearest chunks (configurable via
  `EXPLAIN_ERRORS_RAG_TOP_K`) by vector distance, keeping at most one chunk
  per file.
- **Prompt:** the chunks are appended after the traceback under a
  "Relevant project source:" heading.
- **Budget:** traceback plus source is capped at 6000 characters by
  default; source beyond that is truncated.

## What it costs

Measured on one `--runs 3` pass: `gpt-4o-mini` generator,
`anthropic/claude-sonnet-5` judge via OpenRouter.

| Component | Calls | Cost |
|---|---|---|
| Embeddings | ~24 chunks per run, plus 1 per RAG-on call | under $0.001 |
| Generator | 90 | ~$0.02 |
| Judge | 45 (108k in / 114k out tokens) | $1.36 |
| **Total** | | **~$1.37** |

The judge is the real cost. It writes a claim list per explanation, not a
short verdict, and output tokens are priced 5x input ($10.00 vs $2.00 per
1M tokens for this judge model). Cost scales linearly with `--runs` and
there is no cap, so `--runs 20` is real money. The harness prints the
generator/judge split at the end of every run.

## Environment variables

The harness needs three sets of credentials. It never reuses the
generator's settings for the judge, deliberately. The maintainer's plan is
to point the judge at a different provider (OpenRouter) than the
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
   (`evals/fixture_app/seed.py`), so fixture URLs that assume specific
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
verdicts are nondeterministic. Costs scale linearly (`--runs 5` is five
times the cost above). Higher `--runs` narrows the win-count noise; it does
not change what a single call costs.

### `--fixture NAME`

Runs only the named fixture (see `evals/fixtures.py` for the list), for
debugging one failure without paying for the other 14.

## Reading the summary

Read the win counts **per group**, not just the aggregate. The aggregate
hides which class of error RAG actually helps with. Fixtures split into two
groups by where the cause lives:

- **Group A** (10 fixtures): the cause lives in the app's own source (a
  bad queryset lookup, a missing null check, a broken model method),
  something the RAG index can retrieve and RAG-off cannot see at all.
- **Group B** (5 fixtures): the traceback itself already names the cause
  (a typo'd template name, a missing tag library, a bad import), with no
  extra source context needed to identify it.

See the Results section below for what three runs actually show: RAG-on won
convincingly in both groups. Group B was designed as a RAG-neutral control; the data says it isn't
one.

Below the win counts:

- **Per-question yes counts**, split by group: how often each side
  correctly identified the cause, pointed at the fix location, proposed a
  working fix, avoided fabrication, and was written for a learner. Useful for diagnosing *why*
  a side won or lost, not just that it did.
- **Latency**: mean and p50, RAG-off vs RAG-on. This is not a side note.
  `process_exception` blocks the request path today, so p50 here is what
  every real 500 already waits on. It's also load-bearing for the
  `debug page injection` roadmap item's decision rule
  (see `docs/roadmap.md`).
- **Token usage and estimated cost**, printed as separate Generator and
  Judge lines (plus a combined total) so the split between them is
  visible (the judge is usually the larger of the two, see "What it
  costs" above). Each side is only reported when the corresponding API
  response carried usage data (it always does against the real OpenAI and
  OpenRouter APIs; a local OpenAI-compatible server via `OPENAI_BASE_URL`
  or `EVAL_JUDGE_BASE_URL` may not return `usage`, in which case that line
  prints "not captured"). The cost estimate uses a small hardcoded price
  table in `evals/run.py` (`PRICING_PER_1K_TOKENS`) that will drift out of
  date, so treat it as a rough order of magnitude, not a bill. A model
  (generator or judge) with no entry in that table prints its token counts
  with "no pricing entry for this model" instead of a dollar figure.

A run where more than a third of judge comparisons failed to parse doesn't
print win tallies, per-question yes counts, or claim counts at all. Those
numbers on a mostly-failed sample look like a finding when they're really
noise. Instead the summary prints the failure count, the most common judge
error, and a line stating the run is incomplete. Latency and token usage
still print either way, since those reflect calls actually made, not
judgments actually reached.

A judge response that doesn't parse (see `evals/judge.py:parse_judge_response`)
is recorded as a judge failure, counted separately, and excluded from the
win counts and per-question tallies. It is never silently dropped and
never counted as a tie.

## Results

Final numbers, from the 2026-09-20 run (`evals/results/20260920T204005Z.json`):
3 runs across all 15 fixtures, 45 comparisons, 2 judge failures, 43 scored.
These numbers predate the `str_recursion` source-extraction fix and were
not re-run.

**RAG-on won 35 of 43 scored comparisons** (5 rag-off, 3 tie).

`identifies_cause` and `written_for_learner` aren't shown below, since both
sides answer those correctly almost every time and they don't
discriminate. `points_to_fix_location`, `no_fabrication`, and
`fix_would_work` do.

Group A: 30 comparisons, Group B: 15. 2 judge failures excluded; the per-group split was not recorded.

| Question | Group | RAG-on yes | RAG-off yes |
|---|---|---|---|
| `points_to_fix_location` | A | 26 | 13 |
| `points_to_fix_location` | B | 11 | 7 |
| `no_fabrication` | A | 26 | 17 |
| `no_fabrication` | B | 14 | 5 |
| `fix_would_work` | A | 28 | 22 |
| `fix_would_work` | B | 14 | 12 |

Claims checked per side: 109 (RAG-on) vs 104 (RAG-off) in group A, 56 vs
57 in group B. Both sides state plenty of checkable specifics, and the
judge isn't defaulting to an empty claims list on either side.

Latency: p50 2.12s RAG-off, 2.22s RAG-on. RAG adds no meaningful overhead
on top of the generator call itself.

Methodology history and fixed harness bugs: see METHODOLOGY.md.

## Limitations

The judge is shown the failing function's own source
(`_build_judge_source` in `evals/run.py`), the same source RAG-on's
retriever draws from. So part of RAG-on's `no_fabrication` advantage is
judge and generator overlapping on material RAG-off never sees, not
necessarily RAG-on being more careful. And claim statuses are
spot-checked, not exhaustively audited: `evals/spotcheck.py` (see below)
flagged 14 of 363 claims on the 2026-09-17 run and all 14 held up on
manual inspection. That is a sample that didn't turn up a false positive,
not a proof that none exists.

From the earlier `evals/results/20260916T235351Z.json` run: the judge's
own reasoning showed RAG-on anchoring on an adjacent retrieved chunk for
`missing_post_key`, redirecting the fix to a retrieved template instead
of the view. Retrieval reduces fabrication; it does not eliminate it, and
a wrong chunk ranking high introduces its own kind of error. Whether that
recurs across more fixtures is exactly what the roadmap's "Retrieval
anchoring" item would test (see `docs/roadmap.md`).

## Spot-checking claim statuses

`evals/spotcheck.py` is not a verifier. Claims are English, and nothing
short of a human reading them settles whether one is right. What it does
is cheap and mechanical: it pulls every identifier, dotted filename, and
function-call-shaped token out of each claim's text and checks whether
that token literally appears in `evals/fixture_app/blog/`'s source. A
claim the judge marked "contradicted" that nonetheless names something
real in the source is exactly the failure mode worth a second look.
Either the judge is wrong, or it's right for a reason the naive token
match can't see. Run it against the latest results file with
`python evals/spotcheck.py`.

On the run of 2026-09-17, it flagged 14 of 363 claims, and all 14 were
correct judgments on manual inspection. The judge's "contradicted" calls
held up, and the spot-check just surfaced them for a human to confirm rather
than proving them wrong. Two examples worth naming: RAG-off asserted
`def post_preview(request):` when the real signature at `views.py:40` is
`def post_preview(request, post_id)`, and RAG-off said
`post_timeago.html` needs `{% load humanize %}` added when line 1 already
has it. Zero for zero on false positives in this run isn't a guarantee for
the next one. It's a script that flags candidates, not a correctness
proof.

## Files

- `fixture_app/`: a small, deliberately breakable Django blog. A
  standalone Django project (its own `settings.py`, `urls.py`, `manage.py`),
  not an installed package. `evals/run.py` points `DJANGO_SETTINGS_MODULE`
  at it directly.
- `fixtures.py`: the fixture registry, 15 records, each one URL that
  triggers one specific, realistic failure.
- `run.py`: the harness entry point described above.
- `judge.py`: the judge prompt (`JUDGE_PROMPT_TEMPLATE`, easy to edit)
  and client.
- `spotcheck.py`: see "Spot-checking claim statuses" above.
- `METHODOLOGY.md`: methodology history and fixed harness bugs.
- `results/`: git-ignored. One JSON file per run.
