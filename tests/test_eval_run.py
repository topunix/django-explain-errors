"""Plumbing tests for the eval harness's entry point (evals/run.py).

Fully mocked / subprocess-isolated, same as the rest of the eval harness's
plumbing tests: no real API calls anywhere in this file.
"""
import glob
import hashlib
import json
import os
import subprocess
import sys
import random
import runpy
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from evals.fixtures import FIXTURES_BY_NAME
from evals.run import (
    _capture_call,
    _extract_setting,
    _settings_source,
    build_judge_sources,
    estimate_cost_usd,
    is_generation_failure,
    judge_all,
    parse_generation_error,
    run_fixture,
    tally_generation,
    tally_judgments,
    tally_latency,
    tally_retries,
    tally_usage,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    import sqlite_vec  # noqa: F401
    HAS_SQLITE_VEC = True
except ImportError:
    HAS_SQLITE_VEC = False

requires_sqlite_vec = unittest.skipUnless(
    HAS_SQLITE_VEC, "sqlite-vec not installed (rag extra)"
)


def _judgment(group, winner_side, rag_on_is_a=True, judge_failure=False, a=None, b=None):
    return {
        "group": group,
        "judge_failure": judge_failure,
        "winner_side": winner_side,
        "rag_on_is_a": rag_on_is_a,
        "a": a
        if a is not None
        else {
            "claims": [],
            "identifies_cause": True,
            "points_to_fix_location": True,
            "fix_would_work": True,
            "written_for_learner": True,
            "no_fabrication": True,
        },
        "b": b
        if b is not None
        else {
            "claims": [],
            "identifies_cause": False,
            "points_to_fix_location": False,
            "fix_would_work": False,
            "written_for_learner": False,
            "no_fabrication": False,
        },
    }


class TallyJudgmentsTest(unittest.TestCase):

    def test_per_group_win_counts(self):
        judgments = [
            _judgment("A", "rag_on"),
            _judgment("A", "rag_on"),
            _judgment("A", "rag_off"),
            _judgment("B", "tie"),
            _judgment("B", "rag_off"),
        ]

        result = tally_judgments(judgments)

        self.assertEqual(result["wins_by_group"]["A"], {"rag_on": 2, "rag_off": 1, "tie": 0})
        self.assertEqual(result["wins_by_group"]["B"], {"rag_on": 0, "rag_off": 1, "tie": 1})

    def test_judge_failures_are_counted_and_excluded_from_wins(self):
        judgments = [
            _judgment("A", "rag_on"),
            {
                "group": "A",
                "judge_failure": True,
                "winner_side": None,
                "rag_on_is_a": True,
                "a": None,
                "b": None,
            },
        ]

        result = tally_judgments(judgments)

        self.assertEqual(result["judge_failures"], 1)
        self.assertEqual(result["wins_by_group"]["A"]["rag_on"], 1)
        self.assertEqual(sum(result["wins_by_group"]["A"].values()), 1)

    def test_question_yes_counts_follow_rag_on_off_not_raw_a_b(self):
        # rag_on_is_a=True: judge's "a" answers describe the RAG-on side.
        result = tally_judgments([_judgment("A", "rag_on", rag_on_is_a=True)])
        q = result["question_yes_counts_by_group"]["A"]
        self.assertEqual(q["rag_on"]["identifies_cause"], 1)
        self.assertEqual(q["rag_off"]["identifies_cause"], 0)
        self.assertEqual(q["rag_on"]["no_fabrication"], 1)
        self.assertEqual(q["rag_off"]["no_fabrication"], 0)

        # rag_on_is_a=False: judge's "b" answers now describe the RAG-on side.
        result = tally_judgments([_judgment("A", "rag_off", rag_on_is_a=False)])
        q = result["question_yes_counts_by_group"]["A"]
        self.assertEqual(q["rag_on"]["identifies_cause"], 0)
        self.assertEqual(q["rag_off"]["identifies_cause"], 1)

    def test_empty_list_yields_zeroed_tally(self):
        result = tally_judgments([])
        self.assertEqual(result["wins_by_group"]["A"], {"rag_on": 0, "rag_off": 0, "tie": 0})
        self.assertEqual(result["judge_failures"], 0)
        self.assertEqual(result["claim_counts_by_group"]["A"], {"rag_on": 0, "rag_off": 0})

    def test_claim_counts_follow_rag_on_off_not_raw_a_b(self):
        a = {
            "claims": [{"claim": "x", "status": "verified"}, {"claim": "y", "status": "absent"}],
            "identifies_cause": True,
            "points_to_fix_location": True,
            "fix_would_work": True,
            "written_for_learner": True,
            "no_fabrication": True,
        }
        b = {
            "claims": [{"claim": "z", "status": "verified"}],
            "identifies_cause": False,
            "points_to_fix_location": False,
            "fix_would_work": False,
            "written_for_learner": False,
            "no_fabrication": False,
        }

        # rag_on_is_a=True: a's two claims describe the RAG-on side.
        result = tally_judgments([_judgment("A", "rag_on", rag_on_is_a=True, a=a, b=b)])
        self.assertEqual(result["claim_counts_by_group"]["A"], {"rag_on": 2, "rag_off": 1})

        # rag_on_is_a=False: b's one claim now describes the RAG-on side.
        result = tally_judgments([_judgment("A", "rag_off", rag_on_is_a=False, a=a, b=b)])
        self.assertEqual(result["claim_counts_by_group"]["A"], {"rag_on": 1, "rag_off": 2})

    def test_judge_failures_do_not_count_toward_claims(self):
        judgments = [
            {
                "group": "A",
                "judge_failure": True,
                "winner_side": None,
                "rag_on_is_a": True,
                "a": None,
                "b": None,
            }
        ]
        result = tally_judgments(judgments)
        self.assertEqual(result["claim_counts_by_group"]["A"], {"rag_on": 0, "rag_off": 0})


class TallyLatencyAndUsageTest(unittest.TestCase):

    def test_mean_and_p50_split_by_rag_enabled(self):
        calls = [
            {"rag_enabled": False, "latency_seconds": 1.0},
            {"rag_enabled": False, "latency_seconds": 3.0},
            {"rag_enabled": True, "latency_seconds": 2.0},
        ]

        result = tally_latency(calls)

        self.assertEqual(result["rag_off"]["mean_seconds"], 2.0)
        self.assertEqual(result["rag_off"]["p50_seconds"], 2.0)
        self.assertEqual(result["rag_off"]["n"], 2)
        self.assertEqual(result["rag_on"]["n"], 1)

    def test_no_calls_for_a_side_yields_none_stats(self):
        result = tally_latency([{"rag_enabled": True, "latency_seconds": 1.0}])
        self.assertIsNone(result["rag_off"]["mean_seconds"])
        self.assertEqual(result["rag_off"]["n"], 0)

    def test_usage_none_when_not_captured(self):
        calls = [{"rag_enabled": False, "prompt_tokens": None, "completion_tokens": None}]
        result = tally_usage(calls)
        self.assertIsNone(result["rag_off"]["prompt_tokens"])
        self.assertIsNone(result["rag_off"]["completion_tokens"])

    def test_usage_summed_when_captured(self):
        calls = [
            {"rag_enabled": True, "prompt_tokens": 100, "completion_tokens": 20},
            {"rag_enabled": True, "prompt_tokens": 50, "completion_tokens": 10},
        ]
        result = tally_usage(calls)
        self.assertEqual(result["rag_on"]["prompt_tokens"], 150)
        self.assertEqual(result["rag_on"]["completion_tokens"], 30)

    def test_estimate_cost_returns_none_for_unknown_model(self):
        self.assertIsNone(estimate_cost_usd("some-unreleased-model", 100, 100))

    def test_estimate_cost_returns_none_without_usage(self):
        self.assertIsNone(estimate_cost_usd("gpt-4o-mini", None, None))

    def test_estimate_cost_for_known_model(self):
        cost = estimate_cost_usd("gpt-4o-mini", 1000, 1000)
        self.assertAlmostEqual(cost, 0.00015 + 0.0006)

    def test_estimate_cost_for_gpt_6_luna(self):
        cost = estimate_cost_usd("gpt-6-luna", 1000, 1000)
        self.assertIsNotNone(cost)
        self.assertAlmostEqual(cost, 0.0001 + 0.0005)


class BuildJudgeSourceTest(unittest.TestCase):
    """Runs tests/_eval_judge_source_helper.py in a subprocess -- see its
    docstring for why: _build_judge_source needs django.urls.resolve
    against the fixture app's own URLconf, and a real captured exception
    from an actual request, neither of which this suite's process can give
    it (see test_eval_fixtures.EvalFixtureUrlResolutionTest for the same
    constraint on a different function).
    """

    @classmethod
    def setUpClass(cls):
        proc = subprocess.run(
            [sys.executable, "-m", "tests._eval_judge_source_helper"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cls.results = json.loads(proc.stdout.strip().splitlines()[-1])

    def _labels(self, fixture_name):
        return [entry["label"] for entry in self.results[fixture_name]]

    def _contents(self, fixture_name):
        return {entry["label"]: entry["content"] for entry in self.results[fixture_name]}

    def test_does_not_send_whole_modules(self):
        # none_attribute's failing function is latest_post; some other
        # view's body (e.g. prolific_authors, defined nowhere near it)
        # should not be along for the ride.
        contents = self._contents("none_attribute")
        views_content = "\n".join(
            content for label, content in contents.items() if "views.py" in label
        )
        self.assertIn("latest_post", views_content)
        self.assertNotIn("def prolific_authors", views_content)

    def test_includes_failing_function_and_url_pattern(self):
        contents = self._contents("none_attribute")
        labels = self._labels("none_attribute")
        self.assertTrue(any("views.py" in label for label in labels))
        self.assertTrue(any("urls.py" in label for label in labels))
        views_content = next(c for l, c in contents.items() if "views.py" in l)
        self.assertIn("def latest_post", views_content)
        urls_content = next(c for l, c in contents.items() if "urls.py" in l)
        self.assertIn("latest-post", urls_content)

    def test_recursion_fixture_points_at_the_models_method_not_views(self):
        # str_recursion's exception originates in models.py (Comment.summary),
        # not in the post_comments view -- the innermost project frame should
        # win over "wherever the URL routes".
        labels = self._labels("str_recursion")
        contents = self._contents("str_recursion")
        self.assertTrue(any("models.py" in label for label in labels))
        models_content = next(c for l, c in contents.items() if "models.py" in l)
        self.assertIn("def summary", models_content)

    def test_unexpected_kwarg_falls_back_to_routed_view_and_names_post_id(self):
        # The clearest regression case: the TypeError is raised in Django's
        # own dispatch code (no project frame exists for it at all), so this
        # must fall back to the routed view -- and the URL pattern must show
        # post_id is real, which is exactly what the judge was missing.
        contents = self._contents("unexpected_kwarg")
        views_content = next(c for l, c in contents.items() if "views.py" in l)
        self.assertIn("def post_preview", views_content)
        self.assertIn("post_id", views_content)
        urls_content = next(c for l, c in contents.items() if "urls.py" in l)
        self.assertIn("post_id", urls_content)

    def test_fixture_with_templates_field_gets_extra_source(self):
        labels = self._labels("url_name_typo")
        contents = self._contents("url_name_typo")
        self.assertIn("blog/related_posts.html", labels)
        self.assertIn("post-detial", contents["blog/related_posts.html"])

    def test_fixture_without_templates_field_gets_no_template_source(self):
        labels = self._labels("none_attribute")
        self.assertFalse(any(label.endswith(".html") for label in labels))


    def test_settings_fixture_gets_only_the_named_setting(self):
        labels = self._labels("missing_tag_library")
        label = "fixture_app/settings.py (INSTALLED_APPS)"
        self.assertIn(label, labels)
        content = self._contents("missing_tag_library")[label]
        self.assertTrue(content.startswith("INSTALLED_APPS = ["), content)
        self.assertIn('"django.contrib.auth"', content)
        # Appended last, so existing entries keep their order.
        self.assertEqual(labels[-1], label)
        for entry in self.results["missing_tag_library"]:
            for forbidden in ("SECRET_KEY", "DATABASES", "MIDDLEWARE"):
                self.assertNotIn(forbidden, entry["content"], entry["label"])

    def test_blog_located_fixture_gets_no_settings_entry(self):
        for name in ("none_attribute", "str_recursion"):
            self.assertFalse(
                any("settings.py" in label for label in self._labels(name)), name
            )


class ExtractSettingTest(unittest.TestCase):
    TEXT = (
        "DEBUG = True\n"
        "INSTALLED_APPS = [\n"
        '    "a",\n'
        '    "b",\n'
        "]\n"
        "MIDDLEWARE = []\n"
        "TYPED: list = [1]\n"
        "\n"
        "def f():\n"
        "    NESTED = 1\n"
        "\n"
        "class C:\n"
        "    CLS = 2\n"
    )

    def test_returns_exact_segment_for_list_assignment(self):
        self.assertEqual(
            _extract_setting(self.TEXT, "INSTALLED_APPS"),
            'INSTALLED_APPS = [\n    "a",\n    "b",\n]',
        )

    def test_handles_annotated_assignment(self):
        self.assertEqual(_extract_setting(self.TEXT, "TYPED"), "TYPED: list = [1]")

    def test_missing_name_returns_none(self):
        self.assertIsNone(_extract_setting(self.TEXT, "SECRET_KEY"))

    def test_ignores_nested_assignments(self):
        self.assertIsNone(_extract_setting(self.TEXT, "NESTED"))
        self.assertIsNone(_extract_setting(self.TEXT, "CLS"))

    def test_does_not_return_neighbouring_setting(self):
        segment = _extract_setting(self.TEXT, "INSTALLED_APPS")
        self.assertNotIn("MIDDLEWARE", segment)
        self.assertNotIn("DEBUG", segment)


class _FakeFixture:
    name = "fake"

    def __init__(self, location):
        self.expected_fix_location = location


class SettingsSourceTest(unittest.TestCase):
    def test_non_settings_location_adds_nothing(self):
        for location in (
            "blog/views.py:latest_post",
            "blog/models.py:Comment",
            "blog/templates/blog/post_archive.html",
        ):
            self.assertIsNone(_settings_source(_FakeFixture(location)), location)

    def test_settings_location_without_name_raises(self):
        with self.assertRaises(RuntimeError):
            _settings_source(_FakeFixture("fixture_app/settings.py"))
        with self.assertRaises(RuntimeError):
            _settings_source(_FakeFixture("fixture_app/settings.py:"))

    def test_unknown_setting_raises(self):
        with self.assertRaises(RuntimeError):
            _settings_source(_FakeFixture("fixture_app/settings.py:NOPE_NOT_A_SETTING"))

    def test_missing_file_raises(self):
        with self.assertRaises(RuntimeError):
            _settings_source(_FakeFixture("fixture_app/nope/settings.py:INSTALLED_APPS"))

    def test_known_setting_is_labelled_path_and_name(self):
        label, content = _settings_source(
            _FakeFixture("fixture_app/settings.py:INSTALLED_APPS")
        )
        self.assertEqual(label, "fixture_app/settings.py (INSTALLED_APPS)")
        self.assertTrue(content.startswith("INSTALLED_APPS = ["))


class JudgeAllSourceTextsTest(unittest.TestCase):
    def _calls(self, name):
        return [
            {
                "fixture": name,
                "rag_enabled": rag,
                "run_index": 0,
                "traceback": "tb",
                "explanation": f"{name}/{rag}",
            }
            for rag in (False, True)
        ]

    def test_given_texts_reach_compare_and_builder_is_not_called(self):
        fixture = FIXTURES_BY_NAME["none_attribute"]
        with patch("evals.judge.get_judge_client", return_value=object()), patch(
            "evals.judge.get_judge_model", return_value="m"
        ), patch("evals.judge.compare", return_value={"fixture": fixture.name}) as compare, patch(
            "evals.run._build_judge_source"
        ) as build:
            judge_all(
                [fixture],
                self._calls(fixture.name),
                {},
                seed=1,
                source_texts={fixture.name: "GIVEN TEXT"},
            )
        build.assert_not_called()
        self.assertEqual(compare.call_args[0][4], "GIVEN TEXT")

    def test_build_judge_sources_formats_per_fixture(self):
        fixtures = [FIXTURES_BY_NAME["none_attribute"], FIXTURES_BY_NAME["str_recursion"]]
        with patch("evals.run._build_judge_source", return_value=[("lbl", "body")]) as build:
            texts = build_judge_sources(fixtures, {})
        self.assertEqual(set(texts), {f.name for f in fixtures})
        self.assertEqual(build.call_count, 2)
        self.assertEqual(texts["none_attribute"], "### lbl\n```\nbody\n```")


class JudgeAllConcurrencyTest(unittest.TestCase):
    """judge_all with a fake judge: position draws happen up front, in
    order, independent of concurrency, and results keep job order.
    """

    FIXTURE_NAMES = ("none_attribute", "str_recursion", "bad_lookup")
    RUNS = 3
    SEED = 20260711

    def _calls(self):
        return [
            {
                "fixture": name,
                "rag_enabled": rag,
                "run_index": run,
                "traceback": "tb",
                "explanation": f"{name}/{rag}/{run}",
            }
            for name in self.FIXTURE_NAMES
            for rag in (False, True)
            for run in range(self.RUNS)
        ]

    def _run(self, concurrency, scramble=True):
        """Returns (judgments, draws_before_first_call, call_order)."""
        fixtures = [FIXTURES_BY_NAME[n] for n in self.FIXTURE_NAMES]
        draws = []
        lock = threading.Lock()
        state = {"draws_at_first_call": None}
        call_order = []

        class CountingRandom(random.Random):
            def random(self):
                value = super().random()
                draws.append(value)
                return value

        def fake_compare(client, model, tb, fixture, source, off, on, rag_on_is_a=None, **kw):
            with lock:
                if state["draws_at_first_call"] is None:
                    state["draws_at_first_call"] = len(draws)
                call_order.append((fixture.name, off))
            if scramble:
                # Finish out of submission order: later jobs return sooner.
                time.sleep(0.02 if off.endswith("/0") else 0.0)
            return {
                "fixture": fixture.name,
                "run_index": None,
                "rag_on_is_a": rag_on_is_a,
                "off": off,
                "on": on,
                "retries": 0,
            }

        with patch("evals.judge.get_judge_client", return_value=object()), patch(
            "evals.judge.get_judge_model", return_value="m"
        ), patch("evals.judge.compare", side_effect=fake_compare), patch(
            "evals.run._build_judge_source", return_value=[]
        ), patch("evals.run.random.Random", CountingRandom):
            judgments = judge_all(
                fixtures, self._calls(), {}, concurrency=concurrency, seed=self.SEED
            )
        return judgments, state["draws_at_first_call"], call_order

    def test_all_draws_happen_before_the_first_judge_call(self):
        total = len(self.FIXTURE_NAMES) * self.RUNS
        for n in (1, 4, 8):
            _, draws_at_first_call, _ = self._run(n)
            self.assertEqual(draws_at_first_call, total, f"N={n}")

    def test_positions_match_the_seeded_rng_in_fixture_then_run_order(self):
        reference = random.Random(self.SEED)
        expected = [
            reference.random() < 0.5
            for _ in range(len(self.FIXTURE_NAMES) * self.RUNS)
        ]
        for n in (1, 2, 8, 32):
            judgments, _, _ = self._run(n)
            self.assertEqual([j["rag_on_is_a"] for j in judgments], expected, f"N={n}")

    def test_results_keep_comparison_order_even_when_completion_order_differs(self):
        expected_order = [
            (name, run) for name in self.FIXTURE_NAMES for run in range(self.RUNS)
        ]
        for n in (1, 8):
            judgments, _, _ = self._run(n)
            self.assertEqual(
                [(j["fixture"], j["run_index"]) for j in judgments], expected_order, f"N={n}"
            )
            self.assertEqual(
                [j["off"] for j in judgments],
                [f"{name}/False/{run}" for name, run in expected_order],
            )

    def test_concurrency_one_calls_in_order(self):
        _, _, call_order = self._run(1)
        self.assertEqual(
            [name for name, _ in call_order],
            [name for name in self.FIXTURE_NAMES for _ in range(self.RUNS)],
        )

    def test_concurrency_actually_overlaps_calls(self):
        active = {"now": 0, "peak": 0}
        lock = threading.Lock()

        def fake_compare(*args, **kwargs):
            with lock:
                active["now"] += 1
                active["peak"] = max(active["peak"], active["now"])
            time.sleep(0.05)
            with lock:
                active["now"] -= 1
            return {"fixture": "x", "retries": 0}

        fixtures = [FIXTURES_BY_NAME[n] for n in self.FIXTURE_NAMES]
        with patch("evals.judge.get_judge_client", return_value=object()), patch(
            "evals.judge.get_judge_model", return_value="m"
        ), patch("evals.judge.compare", side_effect=fake_compare), patch(
            "evals.run._build_judge_source", return_value=[]
        ):
            judge_all(fixtures, self._calls(), {}, concurrency=4, seed=1)
        self.assertGreater(active["peak"], 1)
        self.assertLessEqual(active["peak"], 4)


def _skipped(group, sides):
    return {
        "group": group,
        "judge_failure": False,
        "judgment_skipped": True,
        "generation_failed_sides": sides,
        "rag_on_is_a": True,
        "retries": 0,
    }


class _FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        if isinstance(self._body, ValueError):
            raise self._body
        return self._body


class GenerationFailureRecordingTest(unittest.TestCase):
    """run_fixture flags calls with no usable explanation."""

    def _run(self, response, generation_error=None):
        fixture = FIXTURES_BY_NAME["none_attribute"]
        captured = {
            "exc_type": AttributeError,
            "exception": None,
            "traceback": "tb",
            "prompt_tokens": None,
            "completion_tokens": None,
            "generation_error": generation_error,
        }
        with patch("evals.run._capture_call", return_value=(response, captured)):
            call, _ = run_fixture(object(), fixture, False, 0)
        return call

    def test_null_explanation_is_a_failure(self):
        call = self._run(
            _FakeResponse(500, {"message": None}), generation_error="the model returned an empty response"
        )
        self.assertTrue(call["generation_failed"])
        self.assertEqual(call["generation_error"], "the model returned an empty response")
        self.assertIsNone(call["explanation"])

    def test_whitespace_only_explanation_is_a_failure(self):
        call = self._run(_FakeResponse(500, {"message": "  \n\t "}))
        self.assertTrue(call["generation_failed"])
        self.assertIsNone(call["generation_error"])

    def test_empty_string_and_non_json_bodies_are_failures(self):
        self.assertTrue(self._run(_FakeResponse(500, {"message": ""}))["generation_failed"])
        self.assertTrue(self._run(_FakeResponse(500, ValueError("bad")))["generation_failed"])

    def test_real_explanation_is_not_a_failure(self):
        call = self._run(_FakeResponse(500, {"message": "Because None has no attribute."}))
        self.assertFalse(call["generation_failed"])
        self.assertIsNone(call["generation_error"])


class GenerationErrorCaptureTest(unittest.TestCase):
    def test_parses_reason_from_middleware_line(self):
        text = "noise\nFailed to get an explanation from the model API: Error code: 404 - model not found\n"
        self.assertEqual(
            parse_generation_error(text), "Error code: 404 - model not found"
        )

    def test_no_failure_line_is_none(self):
        self.assertIsNone(parse_generation_error("Error explanation (m):\n fine\n"))
        self.assertIsNone(parse_generation_error(""))

    def test_capture_call_records_reason_and_still_prints(self):
        import contextlib
        import io

        outer = io.StringIO()

        def fn():
            print("Failed to get an explanation from the model API:", ValueError("boom"))
            return "ok"

        with contextlib.redirect_stdout(outer):
            result, captured = _capture_call(fn)
        self.assertEqual(result, "ok")
        self.assertEqual(captured["generation_error"], "boom")
        self.assertIn("boom", outer.getvalue())

    def test_capture_call_without_failure_leaves_error_null(self):
        _, captured = _capture_call(lambda: None)
        self.assertIsNone(captured["generation_error"])


class IsGenerationFailureTest(unittest.TestCase):
    def test_flag_wins_when_present(self):
        self.assertTrue(is_generation_failure({"generation_failed": True, "explanation": "x"}))
        self.assertFalse(is_generation_failure({"generation_failed": False, "explanation": None}))

    def test_older_records_without_the_flag_fall_back_to_the_explanation(self):
        self.assertTrue(is_generation_failure({"explanation": None}))
        self.assertTrue(is_generation_failure({"explanation": "   "}))
        self.assertFalse(is_generation_failure({"explanation": "real"}))


class JudgeSkipTest(unittest.TestCase):
    """judge_all never calls the judge for a pair with a failed side."""

    def _judge(self, off_explanation, on_explanation, **extra):
        fixture = FIXTURES_BY_NAME["none_attribute"]

        def call(rag, explanation):
            record = {
                "fixture": fixture.name,
                "rag_enabled": rag,
                "run_index": 0,
                "traceback": "tb",
                "explanation": explanation,
            }
            record.update(extra.get("on" if rag else "off", {}))
            return record

        calls = [call(False, off_explanation), call(True, on_explanation)]
        with patch("evals.judge.get_judge_client", return_value=object()), patch(
            "evals.judge.get_judge_model", return_value="m"
        ), patch("evals.judge.compare", return_value={"fixture": fixture.name}) as compare, patch(
            "evals.run._build_judge_source", return_value=[]
        ):
            judgments = judge_all([fixture], calls, {}, seed=1)
        return judgments, compare

    def test_neither_fails_judges_as_before(self):
        judgments, compare = self._judge("off text", "on text")
        compare.assert_called_once()
        self.assertEqual(len(judgments), 1)
        self.assertNotIn("judgment_skipped", judgments[0])

    def test_rag_on_failure_skips_the_judge(self):
        judgments, compare = self._judge("off text", None)
        compare.assert_not_called()
        self.assertTrue(judgments[0]["judgment_skipped"])
        self.assertEqual(judgments[0]["generation_failed_sides"], ["rag_on"])
        self.assertFalse(judgments[0]["judge_failure"])

    def test_rag_off_failure_skips_the_judge(self):
        judgments, compare = self._judge("", "on text")
        compare.assert_not_called()
        self.assertEqual(judgments[0]["generation_failed_sides"], ["rag_off"])

    def test_both_failing_skips_and_names_both_sides(self):
        judgments, compare = self._judge(None, None)
        compare.assert_not_called()
        self.assertEqual(
            sorted(judgments[0]["generation_failed_sides"]), ["rag_off", "rag_on"]
        )

    def test_whitespace_only_explanation_skips_the_judge(self):
        judgments, compare = self._judge("  \n ", "on text")
        compare.assert_not_called()
        self.assertEqual(judgments[0]["generation_failed_sides"], ["rag_off"])

    def test_recorded_flag_skips_even_with_text_present(self):
        judgments, compare = self._judge("off", "on", on={"generation_failed": True})
        compare.assert_not_called()
        self.assertEqual(judgments[0]["generation_failed_sides"], ["rag_on"])

    def test_skipped_record_keeps_identifying_fields(self):
        judgments, _ = self._judge(None, "on text")
        j = judgments[0]
        self.assertEqual((j["fixture"], j["group"], j["run_index"]), ("none_attribute", FIXTURES_BY_NAME["none_attribute"].group, 0))


class SkippedPairTallyTest(unittest.TestCase):
    def test_skipped_pairs_count_toward_no_group(self):
        judgments = [
            _judgment("A", "rag_on", rag_on_is_a=True),
            _skipped("A", ["rag_on"]),
            _skipped("B", ["rag_on", "rag_off"]),
        ]
        result = tally_judgments(judgments)
        self.assertEqual(result["skipped_pairs"], 2)
        self.assertEqual(result["judge_failures"], 0)
        self.assertEqual(result["wins_by_group"]["A"], {"rag_on": 1, "rag_off": 0, "tie": 0})
        self.assertEqual(result["wins_by_group"]["B"], {"rag_on": 0, "rag_off": 0, "tie": 0})
        self.assertEqual(
            sum(result["question_yes_counts_by_group"]["B"]["rag_on"].values()), 0
        )

    def test_no_skips_reports_zero(self):
        self.assertEqual(tally_judgments([_judgment("A", "tie")])["skipped_pairs"], 0)

    def test_tally_generation_counts_failures(self):
        calls = [
            {"explanation": "ok", "generation_failed": False},
            {"explanation": None, "generation_failed": True},
            {"explanation": "  "},  # older record, no flag
        ]
        self.assertEqual(tally_generation(calls), {"failures": 2, "calls": 3})

    def test_summary_prints_generation_lines_and_excludes_skips_from_incomplete(self):
        import contextlib
        import io

        from evals.run import print_summary

        calls = [
            {"explanation": None, "generation_failed": True, "rag_enabled": False,
             "latency_seconds": 1.0, "prompt_tokens": None, "completion_tokens": None},
            {"explanation": None, "generation_failed": True, "rag_enabled": True,
             "latency_seconds": 1.0, "prompt_tokens": None, "completion_tokens": None},
        ]
        # Skipped pairs stay out of the judge-failure ratio: 1 judged, 0 failed.
        judgments = [_judgment("A", "tie"), _skipped("A", ["rag_on", "rag_off"])]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            print_summary([], calls, judgments, "g", "j")
        text = out.getvalue()
        self.assertIn("Generation failures: 2 of 2 calls", text)
        self.assertIn("Skipped pairs (generation failure, not judged): 1", text)
        self.assertNotIn("RUN INCOMPLETE", text)

    def test_all_pairs_skipped_prints_run_incomplete_without_dividing_by_zero(self):
        import contextlib
        import io

        from evals.run import print_summary

        calls = [
            {"explanation": None, "generation_failed": True, "rag_enabled": flag,
             "latency_seconds": 1.0, "prompt_tokens": None, "completion_tokens": None}
            for flag in (False, True)
        ]
        judgments = [_skipped("A", ["rag_on", "rag_off"]), _skipped("B", ["rag_on"])]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            print_summary([], calls, judgments, "g", "j")
        text = out.getvalue()
        self.assertIn("RUN INCOMPLETE: all 2 pair(s) were skipped", text)
        self.assertNotIn("Wins by group", text)

    def test_no_judgments_at_all_is_not_flagged_incomplete(self):
        import contextlib
        import io

        from evals.run import print_summary

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            print_summary([], [], [], "g", "j")
        self.assertNotIn("RUN INCOMPLETE", out.getvalue())


class TallyRetriesTest(unittest.TestCase):

    def test_sums_per_judgment_retries_and_tolerates_missing(self):
        self.assertEqual(
            tally_retries([{"retries": 2}, {"retries": 0}, {}, {"retries": None}, {"retries": 1}]),
            3,
        )


class FixtureSettingsModelTest(unittest.TestCase):
    """The fixture app's settings resolve OPENAI_MODEL from the environment."""

    SETTINGS_PATH = os.path.join(REPO_ROOT, "evals", "fixture_app", "settings.py")

    def _model(self, **env):
        with patch.dict(os.environ, env):
            if "OPENAI_MODEL" not in env:
                os.environ.pop("OPENAI_MODEL", None)
            return runpy.run_path(self.SETTINGS_PATH)["OPENAI_MODEL"]

    def test_reads_env_var(self):
        self.assertEqual(self._model(OPENAI_MODEL="some-other-model"), "some-other-model")

    def test_unset_falls_back_to_default(self):
        self.assertEqual(self._model(), "gpt-6-luna")

    def test_empty_string_falls_back_to_default(self):
        self.assertEqual(self._model(OPENAI_MODEL=""), "gpt-6-luna")


class EvalHarnessEndToEndTest(unittest.TestCase):
    """Runs evals.run.main() in a subprocess with the generator (chat +
    embeddings) and the judge fully mocked
    (tests/_eval_harness_e2e_runner.py), and asserts the results file it
    writes has the shape evals/run.py's main() actually produces.

    This is the harness's proof of runnability per section 6: the real
    OpenAI and judge APIs are unavailable in this environment, so this is
    the closest thing to actually running it end to end.

    Subprocess, not in-process, because evals.run.main() calls
    django.setup() against the fixture app's settings (blog in
    INSTALLED_APPS), which this test suite process's already-populated app
    registry (test_settings) cannot accommodate -- see
    tests/_eval_harness_e2e_runner.py's docstring.
    """

    @requires_sqlite_vec
    def test_mocked_run_writes_results_file_with_expected_shape(self):
        results_dir = os.path.join(REPO_ROOT, "evals", "results")
        os.makedirs(results_dir, exist_ok=True)
        before = set(glob.glob(os.path.join(results_dir, "*.json")))

        log_fd, compare_log = tempfile.mkstemp(suffix=".json")
        os.close(log_fd)
        self.addCleanup(lambda: os.path.exists(compare_log) and os.remove(compare_log))
        env = dict(os.environ, EVAL_E2E_COMPARE_LOG=compare_log)
        proc = subprocess.run(
            [sys.executable, "-m", "tests._eval_harness_e2e_runner", "--runs", "1"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

        after = set(glob.glob(os.path.join(results_dir, "*.json")))
        new_files = list(after - before)
        self.assertEqual(len(new_files), 1, new_files)
        result_path = new_files[0]
        self.addCleanup(lambda: os.path.exists(result_path) and os.remove(result_path))

        with open(result_path) as f:
            data = json.load(f)

        for key in (
            "timestamp",
            "git_sha",
            "generator_model",
            "judge_model",
            "runs",
            "seed",
            "timing",
            "judge_retries_total",
            "judge_sources",
            "calls",
            "judgments",
        ):
            self.assertIn(key, data)

        with open(compare_log) as f:
            received = json.load(f)
        run_fixtures = {call["fixture"] for call in data["calls"]}
        self.assertEqual(set(data["judge_sources"]), run_fixtures)
        for name, entry in data["judge_sources"].items():
            self.assertEqual(
                entry["sha256"], hashlib.sha256(entry["text"].encode("utf-8")).hexdigest()
            )
            self.assertEqual(received[name], [entry["text"]], name)
        self.assertEqual(data["judge_retries_total"], 0)
        self.assertEqual(data["timing"]["judge_concurrency"], 8)
        self.assertGreaterEqual(data["timing"]["generator_seconds"], 0)
        self.assertGreaterEqual(data["timing"]["judge_seconds"], 0)

        self.assertEqual(data["generator_model"], "gpt-6-luna")
        self.assertEqual(data["judge_model"], "judge-test-model")
        self.assertEqual(len(data["calls"]), 30)  # 15 fixtures x 2 RAG states x 1 run
        self.assertEqual(len(data["judgments"]), 15)

        rag_states = {call["rag_enabled"] for call in data["calls"]}
        self.assertEqual(rag_states, {True, False})

        call_keys = {
            "fixture",
            "group",
            "rag_enabled",
            "run_index",
            "explanation",
            "traceback",
            "latency_seconds",
            "prompt_tokens",
            "completion_tokens",
            "status_code",
            "generation_failed",
            "generation_error",
        }
        for call in data["calls"]:
            self.assertTrue(call_keys.issubset(call.keys()), call)
            self.assertEqual(call["status_code"], 500)
            self.assertFalse(call["generation_failed"])
            self.assertIsNone(call["generation_error"])
            self.assertEqual(call["explanation"], "Mocked generator explanation.")
            self.assertEqual(call["prompt_tokens"], 10)
            self.assertEqual(call["completion_tokens"], 5)

        judgment_keys = {
            "fixture",
            "group",
            "judge_failure",
            "rag_on_is_a",
            "winner",
            "winner_side",
            "a",
            "b",
            "reasoning",
            "run_index",
            "retries",
        }
        for judgment in data["judgments"]:
            self.assertTrue(judgment_keys.issubset(judgment.keys()), judgment)
            self.assertFalse(judgment["judge_failure"])
            self.assertIn(judgment["winner_side"], ("rag_on", "rag_off", "tie"))
            # The core regression this guards against: claims (see
            # _eval_harness_e2e_runner.py's JUDGE_RESPONSE_TEXT) surviving
            # compare() -> the results dict -> json.dumps -> disk -> back.
            self.assertEqual(
                judgment["a"]["claims"],
                [{"claim": "the fix is in the view function", "status": "verified"}],
            )
            self.assertEqual(
                judgment["b"]["claims"],
                [{"claim": "the fix is elsewhere", "status": "contradicted"}],
            )
            self.assertTrue(judgment["a"]["no_fabrication"])
            self.assertFalse(judgment["b"]["no_fabrication"])
