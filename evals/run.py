#!/usr/bin/env python
"""Eval harness entry point. See evals/README.md for how to run this and how
to read its output.

Not part of the test suite. This costs money, hits real APIs, and gives
slightly different answers every run -- the maintainer runs it by hand when
a change might affect explanation quality. `tests/test_eval_*.py` cover the
plumbing here with everything mocked; they do not run this script for real.
"""
import argparse
import datetime
import json
import logging
import os
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

EVALS_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVALS_DIR.parent
FIXTURE_APP_DIR = EVALS_DIR / "fixture_app"
BLOG_DIR = FIXTURE_APP_DIR / "blog"
RESULTS_DIR = EVALS_DIR / "results"
DB_PATH = FIXTURE_APP_DIR / "eval_fixture_app.sqlite3"
INDEX_PATH = FIXTURE_APP_DIR / ".eval_rag_index.db"

# Included in the judge's source section for every fixture (see
# _build_judge_source), so the judge can check a claimed function name,
# parameter, or file against the real thing instead of treating anything
# outside the traceback and known facts as unverifiable.
JUDGE_SOURCE_MODULES = (
    ("blog/views.py", BLOG_DIR / "views.py"),
    ("blog/models.py", BLOG_DIR / "models.py"),
    ("blog/urls.py", BLOG_DIR / "urls.py"),
)


def _build_judge_source(fixture):
    """(label, content) pairs for evals.judge.format_source_section: the
    three modules above, present for every fixture, plus any templates this
    fixture names via its `templates` field. Sending the same fixed modules
    regardless of fixture or RAG side means their presence can't tip the
    judge off to which explanation had RAG.
    """
    sources = [(label, path.read_text()) for label, path in JUDGE_SOURCE_MODULES]
    for template_name in fixture.templates:
        template_path = BLOG_DIR / "templates" / template_name
        sources.append((template_name, template_path.read_text()))
    return sources


# USD per 1K tokens. Best-effort, current as of this writing -- update when
# it drifts enough to matter, or drop a model's entry to skip its estimate.
PRICING_PER_1K_TOKENS = {
    "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
    "gpt-4o": {"prompt": 0.0025, "completion": 0.01},
}


def _setup_django():
    """Point Django at the fixture app's settings module and load the app
    registry. The fixture app is a standalone Django project (its own
    manage.py, settings.py, urls.py), not an installed package -- it is put
    on sys.path exactly like manage.py would, plus the repo root so
    `explain_errors` and `evals` import normally.
    """
    for path in (str(REPO_ROOT), str(FIXTURE_APP_DIR)):
        if path not in sys.path:
            sys.path.insert(0, path)
    # Force, not setdefault: the fixture app owns its own settings module
    # unconditionally. A caller's ambient DJANGO_SETTINGS_MODULE (e.g. the
    # test suite's test_settings, if this somehow ran in that process) would
    # otherwise point Django at an app registry that has never heard of
    # `blog` and does not know INSTALLED_APPS needs it.
    os.environ["DJANGO_SETTINGS_MODULE"] = "settings"

    import django

    django.setup()


def _reset_database():
    """Fresh schema and seed data for every run, so fixture URLs that
    assume specific row ids (see evals/fixture_app/seed.py) are reliable.
    """
    from django.core.management import call_command

    if DB_PATH.exists():
        DB_PATH.unlink()
    call_command("migrate", run_syncdb=True, verbosity=0)

    from seed import seed

    seed()


def _build_rag_index():
    from django.test import override_settings

    from explain_errors.rag.indexer import build_index

    if INDEX_PATH.exists():
        INDEX_PATH.unlink()

    with override_settings(
        EXPLAIN_ERRORS_RAG_INCLUDE=[str(FIXTURE_APP_DIR)],
        EXPLAIN_ERRORS_RAG_INDEX_PATH=str(INDEX_PATH),
    ):
        return build_index()


def _git_sha():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT), text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


class _UsageCaptureHandler(logging.Handler):
    """Picks the token-usage debug line (middleware.py) out of the
    `explain_errors` logger for the duration of one request.
    """

    PREFIX = "explain_errors: token usage"

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.usage = None

    def emit(self, record):
        if isinstance(record.msg, str) and record.msg.startswith(self.PREFIX):
            prompt_tokens, completion_tokens = record.args
            self.usage = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            }


def _capture_call(fn):
    """Run fn() (a single request through the fixture app), returning
    (result, captured) where captured holds the exception type the
    middleware actually handled, the sanitized traceback text sent to the
    generator, and token usage if the generator reported any.

    Exception type can't be read off got_request_exception: that signal
    only fires when the exception is left to propagate (preserve mode), and
    this harness always runs with EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE=False
    (section 3) so it can read the explanation out of the JSON 500 body.
    Instead this spies on sanitize_traceback, which middleware.py always
    calls, synchronously, from inside the `except Exception:` block in
    process_exception -- so sys.exc_info() is still live when it runs.
    """
    import explain_errors.middleware as mw_mod
    from explain_errors.sanitize import sanitize_traceback as real_sanitize_traceback

    captured = {
        "exc_type": None,
        "traceback": None,
        "prompt_tokens": None,
        "completion_tokens": None,
    }

    def _spy(tb_text):
        exc_type, _exc_value, _tb = sys.exc_info()
        captured["exc_type"] = exc_type
        sanitized = real_sanitize_traceback(tb_text)
        captured["traceback"] = sanitized
        return sanitized

    logger = logging.getLogger("explain_errors")
    handler = _UsageCaptureHandler()
    previous_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        with patch.object(mw_mod, "sanitize_traceback", side_effect=_spy):
            result = fn()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    if handler.usage is not None:
        captured["prompt_tokens"] = handler.usage["prompt_tokens"]
        captured["completion_tokens"] = handler.usage["completion_tokens"]

    return result, captured


def _hit_fixture(client, fixture):
    if fixture.method == "POST":
        return client.post(fixture.url, data=fixture.data)
    return client.get(fixture.url)


def run_fixture(client, fixture, rag_enabled, run_index):
    """Run one fixture once and return its call record. Raises
    AssertionError if the fixture didn't raise what it's supposed to --
    that means the fixture is broken, not that the middleware misbehaved.
    """
    start = time.perf_counter()
    response, captured = _capture_call(lambda: _hit_fixture(client, fixture))
    latency_seconds = time.perf_counter() - start

    exc_type = captured["exc_type"]
    exc_name = exc_type.__name__ if exc_type is not None else None
    exc_qualname = exc_type.__qualname__ if exc_type is not None else None
    if fixture.expected_exception not in (exc_name, exc_qualname):
        raise AssertionError(
            f"fixture {fixture.name!r} (rag_enabled={rag_enabled}, run {run_index}) "
            f"raised {exc_qualname or exc_name!r}, expected "
            f"{fixture.expected_exception!r} -- the fixture is broken, fix "
            "evals/fixture_app before trusting these results"
        )

    explanation = None
    if response.status_code == 500:
        try:
            explanation = response.json().get("message")
        except ValueError:
            explanation = None

    return {
        "fixture": fixture.name,
        "group": fixture.group,
        "rag_enabled": rag_enabled,
        "run_index": run_index,
        "explanation": explanation,
        "traceback": captured["traceback"],
        "latency_seconds": latency_seconds,
        "prompt_tokens": captured["prompt_tokens"],
        "completion_tokens": captured["completion_tokens"],
        "status_code": response.status_code,
    }


def run_all(fixtures, runs):
    """Two passes (RAG off, then RAG on) over every fixture, `runs` times
    each. Returns the flat list of call records.
    """
    from django.test import Client, override_settings

    calls = []
    for rag_enabled in (False, True):
        with override_settings(
            EXPLAIN_ERRORS_RAG_ENABLED=rag_enabled,
            EXPLAIN_ERRORS_RAG_INDEX_PATH=str(INDEX_PATH),
        ):
            client = Client(raise_request_exception=False)
            for fixture in fixtures:
                for run_index in range(runs):
                    calls.append(run_fixture(client, fixture, rag_enabled, run_index))
    return calls


def judge_all(fixtures, calls):
    """Pair up each fixture+run's RAG-off and RAG-on calls and judge them.
    Returns the list of judgment records.
    """
    from evals.judge import compare, format_source_section, get_judge_client, get_judge_model

    client = get_judge_client()
    model = get_judge_model()
    rng = random.Random()

    calls_by_key = {(c["fixture"], c["rag_enabled"], c["run_index"]): c for c in calls}

    judgments = []
    for fixture in fixtures:
        source_text = format_source_section(_build_judge_source(fixture))
        run_indices = sorted(
            {c["run_index"] for c in calls if c["fixture"] == fixture.name}
        )
        for run_index in run_indices:
            off_call = calls_by_key[(fixture.name, False, run_index)]
            on_call = calls_by_key[(fixture.name, True, run_index)]
            judgment = compare(
                client,
                model,
                off_call["traceback"] or "",
                fixture,
                source_text,
                off_call["explanation"],
                on_call["explanation"],
                rng=rng,
            )
            judgment["run_index"] = run_index
            judgments.append(judgment)
    return judgments


def tally_judgments(judgments):
    """Given a (possibly hand-built) list of judgment records, return win
    counts and per-question yes counts, each split by fixture group.
    """
    from evals.judge import QUESTION_KEYS

    wins_by_group = {
        group: {"rag_on": 0, "rag_off": 0, "tie": 0} for group in ("A", "B")
    }
    questions_by_group = {
        group: {
            "rag_on": {key: 0 for key in QUESTION_KEYS},
            "rag_off": {key: 0 for key in QUESTION_KEYS},
        }
        for group in ("A", "B")
    }
    judge_failures = 0

    for judgment in judgments:
        if judgment.get("judge_failure"):
            judge_failures += 1
            continue

        group = judgment["group"]
        wins_by_group[group][judgment["winner_side"]] += 1

        rag_on_is_a = judgment["rag_on_is_a"]
        rag_on_answers = judgment["a"] if rag_on_is_a else judgment["b"]
        rag_off_answers = judgment["b"] if rag_on_is_a else judgment["a"]
        for key in QUESTION_KEYS:
            if rag_on_answers[key]:
                questions_by_group[group]["rag_on"][key] += 1
            if rag_off_answers[key]:
                questions_by_group[group]["rag_off"][key] += 1

    return {
        "wins_by_group": wins_by_group,
        "question_yes_counts_by_group": questions_by_group,
        "judge_failures": judge_failures,
    }


def tally_latency(calls):
    result = {}
    for label, rag_enabled in (("rag_off", False), ("rag_on", True)):
        latencies = [c["latency_seconds"] for c in calls if c["rag_enabled"] == rag_enabled]
        if latencies:
            result[label] = {
                "mean_seconds": statistics.mean(latencies),
                "p50_seconds": statistics.median(latencies),
                "n": len(latencies),
            }
        else:
            result[label] = {"mean_seconds": None, "p50_seconds": None, "n": 0}
    return result


def tally_usage(calls):
    result = {}
    for label, rag_enabled in (("rag_off", False), ("rag_on", True)):
        subset = [c for c in calls if c["rag_enabled"] == rag_enabled]
        have_usage = any(c["prompt_tokens"] is not None for c in subset)
        if have_usage:
            prompt_tokens = sum(c["prompt_tokens"] or 0 for c in subset)
            completion_tokens = sum(c["completion_tokens"] or 0 for c in subset)
        else:
            prompt_tokens = completion_tokens = None
        result[label] = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
    return result


def estimate_cost_usd(model, prompt_tokens, completion_tokens):
    pricing = PRICING_PER_1K_TOKENS.get(model)
    if pricing is None or prompt_tokens is None or completion_tokens is None:
        return None
    return (prompt_tokens / 1000) * pricing["prompt"] + (
        completion_tokens / 1000
    ) * pricing["completion"]


def print_summary(fixtures, calls, judgments, generator_model, judge_model):
    tallied = tally_judgments(judgments)
    latency = tally_latency(calls)
    usage = tally_usage(calls)

    print()
    print(f"generator: {generator_model}   judge: {judge_model}")
    print(f"fixtures: {len(fixtures)}   calls: {len(calls)}   judgments: {len(judgments)}")
    if tallied["judge_failures"]:
        print(f"judge failures (malformed response): {tallied['judge_failures']}")

    print()
    print("Wins by group (this is the headline, not the aggregate):")
    for group, label in (("A", "A -- RAG predicted to help"), ("B", "B -- RAG predicted neutral")):
        w = tallied["wins_by_group"][group]
        total = w["rag_on"] + w["rag_off"] + w["tie"]
        print(
            f"  Group {label}: RAG-on {w['rag_on']}, RAG-off {w['rag_off']}, "
            f"tie {w['tie']}  (of {total})"
        )

    print()
    print("Per-question yes counts by group:")
    for group in ("A", "B"):
        print(f"  Group {group}:")
        q = tallied["question_yes_counts_by_group"][group]
        for key in q["rag_on"]:
            print(f"    {key:24} rag_on={q['rag_on'][key]:<4} rag_off={q['rag_off'][key]}")

    print()
    print("Latency:")
    for label in ("rag_off", "rag_on"):
        stats = latency[label]
        if stats["n"]:
            print(
                f"  {label}: mean {stats['mean_seconds']:.2f}s, "
                f"p50 {stats['p50_seconds']:.2f}s (n={stats['n']})"
            )
        else:
            print(f"  {label}: no calls")

    print()
    print("Token usage:")
    total_cost = 0.0
    have_cost = False
    for label in ("rag_off", "rag_on"):
        u = usage[label]
        if u["prompt_tokens"] is None:
            print(f"  {label}: not captured")
            continue
        cost = estimate_cost_usd(generator_model, u["prompt_tokens"], u["completion_tokens"])
        cost_str = f"(~${cost:.4f})" if cost is not None else "(no pricing entry for this model)"
        print(
            f"  {label}: prompt={u['prompt_tokens']} completion={u['completion_tokens']} "
            f"{cost_str}"
        )
        if cost is not None:
            total_cost += cost
            have_cost = True
    if have_cost:
        print(f"  estimated total: ~${total_cost:.4f}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs", type=int, default=1, help="repeat every fixture N times (default 1)"
    )
    parser.add_argument(
        "--fixture", default=None, help="run only this fixture, by name, for debugging"
    )
    args = parser.parse_args(argv)

    _setup_django()

    from evals.fixtures import FIXTURES, FIXTURES_BY_NAME

    if args.fixture is not None:
        if args.fixture not in FIXTURES_BY_NAME:
            parser.error(f"no fixture named {args.fixture!r}")
        fixtures = [FIXTURES_BY_NAME[args.fixture]]
    else:
        fixtures = FIXTURES

    from django.conf import settings

    generator_model = getattr(settings, "OPENAI_MODEL", "gpt-4o-mini")

    print("Resetting fixture app database...")
    _reset_database()

    print(f"Building RAG index against {FIXTURE_APP_DIR} ...")
    index_result = _build_rag_index()
    print(
        f"Indexed {index_result['chunks_embedded']} chunks from "
        f"{index_result['files_scanned']} files."
    )

    print(f"Running {len(fixtures)} fixture(s) x {args.runs} run(s) x 2 (RAG off/on)...")
    calls = run_all(fixtures, args.runs)

    print("Judging...")
    judgments = judge_all(fixtures, calls)

    from evals.judge import get_judge_model

    judge_model = get_judge_model()

    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results = {
        "timestamp": timestamp,
        "git_sha": _git_sha(),
        "generator_model": generator_model,
        "judge_model": judge_model,
        "runs": args.runs,
        "calls": calls,
        "judgments": judgments,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{timestamp}.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nWrote {out_path}")

    print_summary(fixtures, calls, judgments, generator_model, judge_model)

    return out_path


if __name__ == "__main__":
    main()
