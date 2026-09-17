"""Plumbing tests for the eval harness's entry point (evals/run.py).

Fully mocked / subprocess-isolated, same as the rest of the eval harness's
plumbing tests: no real API calls anywhere in this file.
"""
import glob
import json
import os
import subprocess
import sys
import unittest

from evals.run import estimate_cost_usd, tally_judgments, tally_latency, tally_usage

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _judgment(group, winner_side, rag_on_is_a=True, judge_failure=False, a=None, b=None):
    return {
        "group": group,
        "judge_failure": judge_failure,
        "winner_side": winner_side,
        "rag_on_is_a": rag_on_is_a,
        "a": a
        if a is not None
        else {
            "identifies_cause": True,
            "points_to_fix_location": True,
            "fix_would_work": True,
            "written_for_learner": True,
            "no_fabrication": True,
        },
        "b": b
        if b is not None
        else {
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

    def test_mocked_run_writes_results_file_with_expected_shape(self):
        results_dir = os.path.join(REPO_ROOT, "evals", "results")
        os.makedirs(results_dir, exist_ok=True)
        before = set(glob.glob(os.path.join(results_dir, "*.json")))

        proc = subprocess.run(
            [sys.executable, "-m", "tests._eval_harness_e2e_runner", "--runs", "1"],
            cwd=REPO_ROOT,
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
            "calls",
            "judgments",
        ):
            self.assertIn(key, data)

        self.assertEqual(data["generator_model"], "gpt-4o-mini")
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
        }
        for call in data["calls"]:
            self.assertTrue(call_keys.issubset(call.keys()), call)
            self.assertEqual(call["status_code"], 500)
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
        }
        for judgment in data["judgments"]:
            self.assertTrue(judgment_keys.issubset(judgment.keys()), judgment)
            self.assertFalse(judgment["judge_failure"])
            self.assertIn(judgment["winner_side"], ("rag_on", "rag_off", "tie"))
