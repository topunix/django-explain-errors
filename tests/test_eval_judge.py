"""Plumbing tests for the eval harness's judge (evals/judge.py).

Fully mocked, same as the rest of the eval harness's plumbing tests: no
real API calls.
"""
import json
import unittest
from unittest.mock import MagicMock

from evals.fixtures import Fixture
from evals.judge import (
    SIDE_RESULT_KEYS,
    JudgeParseError,
    _explanation_states_specific_details,
    build_judge_prompt,
    compare,
    format_source_section,
    parse_judge_response,
)


def _make_fixture(**overrides):
    defaults = dict(
        name="sample",
        url="/sample/",
        group="A",
        expected_exception="ValueError",
        expected_cause="a cause",
        expected_fix_location="app/views.py:sample",
    )
    defaults.update(overrides)
    return Fixture(**defaults)


VALID_RESPONSE = json.dumps(
    {
        "a": {
            "claims": [{"claim": "the fix is in sample()", "status": "verified"}],
            "identifies_cause": True,
            "points_to_fix_location": True,
            "fix_would_work": True,
            "written_for_learner": True,
        },
        "b": {
            "claims": [{"claim": "the fix is in some_other_func()", "status": "contradicted"}],
            "identifies_cause": False,
            "points_to_fix_location": False,
            "fix_would_work": False,
            "written_for_learner": False,
        },
        "winner": "A",
        "reasoning": "A is better.",
    }
)


def _client_returning(content):
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=content))]
    )
    return client


class _FixedRng:
    """Stands in for random.Random: .random() always returns a fixed value,
    so tests can force which side lands in position A.
    """

    def __init__(self, value):
        self._value = value

    def random(self):
        return self._value


class ParseJudgeResponseTest(unittest.TestCase):

    def test_valid_json_parses(self):
        parsed = parse_judge_response(VALID_RESPONSE)
        self.assertEqual(parsed["winner"], "A")
        self.assertTrue(parsed["a"]["identifies_cause"])
        self.assertFalse(parsed["b"]["identifies_cause"])

    def test_malformed_json_raises_judge_parse_error(self):
        with self.assertRaises(JudgeParseError):
            parse_judge_response("this is not json")

    def test_json_that_is_not_an_object_raises(self):
        with self.assertRaises(JudgeParseError):
            parse_judge_response("[1, 2, 3]")

    def test_missing_question_key_raises(self):
        payload = json.loads(VALID_RESPONSE)
        del payload["a"]["fix_would_work"]
        with self.assertRaises(JudgeParseError):
            parse_judge_response(json.dumps(payload))

    def test_non_boolean_question_value_raises(self):
        payload = json.loads(VALID_RESPONSE)
        payload["a"]["fix_would_work"] = "yes"
        with self.assertRaises(JudgeParseError):
            parse_judge_response(json.dumps(payload))

    def test_missing_claims_key_is_a_judge_failure(self):
        payload = json.loads(VALID_RESPONSE)
        del payload["b"]["claims"]
        with self.assertRaises(JudgeParseError):
            parse_judge_response(json.dumps(payload))

    def test_claims_not_a_list_raises(self):
        payload = json.loads(VALID_RESPONSE)
        payload["a"]["claims"] = "none"
        with self.assertRaises(JudgeParseError):
            parse_judge_response(json.dumps(payload))

    def test_claim_missing_claim_text_raises(self):
        payload = json.loads(VALID_RESPONSE)
        payload["a"]["claims"] = [{"status": "verified"}]
        with self.assertRaises(JudgeParseError):
            parse_judge_response(json.dumps(payload))

    def test_claim_with_invalid_status_raises(self):
        payload = json.loads(VALID_RESPONSE)
        payload["a"]["claims"] = [{"claim": "x", "status": "unverified"}]
        with self.assertRaises(JudgeParseError):
            parse_judge_response(json.dumps(payload))

    def test_no_fabrication_is_derived_not_read_from_response(self):
        # A raw no_fabrication key in the response is ignored -- it's always
        # computed from the claims list.
        payload = json.loads(VALID_RESPONSE)
        payload["a"]["no_fabrication"] = False  # contradicts a's all-verified claims
        parsed = parse_judge_response(json.dumps(payload))
        self.assertTrue(parsed["a"]["no_fabrication"])

    def test_contradicted_claim_yields_no_fabrication_false(self):
        parsed = parse_judge_response(VALID_RESPONSE)
        # b's one claim is "contradicted" in VALID_RESPONSE.
        self.assertFalse(parsed["b"]["no_fabrication"])

    def test_verified_and_absent_claims_yield_no_fabrication_true(self):
        payload = json.loads(VALID_RESPONSE)
        payload["a"]["claims"] = [
            {"claim": "x", "status": "verified"},
            {"claim": "y", "status": "absent"},
        ]
        parsed = parse_judge_response(json.dumps(payload))
        self.assertTrue(parsed["a"]["no_fabrication"])

    def test_empty_claims_list_yields_no_fabrication_true(self):
        payload = json.loads(VALID_RESPONSE)
        payload["a"]["claims"] = []
        parsed = parse_judge_response(json.dumps(payload))
        self.assertTrue(parsed["a"]["no_fabrication"])

    def test_invalid_winner_raises(self):
        payload = json.loads(VALID_RESPONSE)
        payload["winner"] = "C"
        with self.assertRaises(JudgeParseError):
            parse_judge_response(json.dumps(payload))

    def test_missing_reasoning_raises(self):
        payload = json.loads(VALID_RESPONSE)
        del payload["reasoning"]
        with self.assertRaises(JudgeParseError):
            parse_judge_response(json.dumps(payload))


class FormatSourceSectionTest(unittest.TestCase):

    def test_renders_each_label_and_content(self):
        rendered = format_source_section(
            [("blog/views.py", "def f():\n    pass\n"), ("blog/models.py", "x = 1")]
        )
        self.assertIn("### blog/views.py", rendered)
        self.assertIn("def f():", rendered)
        self.assertIn("### blog/models.py", rendered)
        self.assertIn("x = 1", rendered)
        # views.py section comes first, in the order given.
        self.assertLess(rendered.index("views.py"), rendered.index("models.py"))

    def test_empty_sources_yields_placeholder_not_empty_string(self):
        rendered = format_source_section([])
        self.assertTrue(rendered)
        self.assertNotIn("###", rendered)


class BuildJudgePromptTest(unittest.TestCase):

    def test_source_section_is_included(self):
        fixture = _make_fixture()
        prompt = build_judge_prompt("tb", fixture, "### blog/views.py\nreal source here", "a", "b")
        self.assertIn("real source here", prompt)

    def test_prompt_asks_for_a_claims_list_checked_against_the_source(self):
        fixture = _make_fixture()
        prompt = build_judge_prompt("tb", fixture, "src", "a", "b")
        self.assertIn("first list its specific claims", prompt)
        self.assertIn('"verified"', prompt)
        self.assertIn('"contradicted"', prompt)
        self.assertIn('"absent"', prompt)
        self.assertNotIn("no_fabrication: Does it avoid", prompt)


class CompareRandomizationTest(unittest.TestCase):

    def test_rag_on_lands_in_position_a_roughly_half_the_time(self):
        import random

        fixture = _make_fixture()
        client = _client_returning(VALID_RESPONSE)
        rng = random.Random(1234)

        n = 500
        a_count = sum(
            1
            for _ in range(n)
            if compare(client, "judge-model", "tb", fixture, "src", "off", "on", rng=rng)[
                "rag_on_is_a"
            ]
        )

        fraction = a_count / n
        self.assertGreater(fraction, 0.4)
        self.assertLess(fraction, 0.6)

    def test_winner_a_maps_to_rag_on_when_rag_on_is_a(self):
        fixture = _make_fixture()
        client = _client_returning(VALID_RESPONSE)  # winner "A"

        result = compare(client, "m", "tb", fixture, "src", "off", "on", rng=_FixedRng(0.1))

        self.assertTrue(result["rag_on_is_a"])
        self.assertEqual(result["winner_side"], "rag_on")

    def test_winner_a_maps_to_rag_off_when_rag_off_is_a(self):
        fixture = _make_fixture()
        client = _client_returning(VALID_RESPONSE)  # winner "A"

        result = compare(client, "m", "tb", fixture, "src", "off", "on", rng=_FixedRng(0.9))

        self.assertFalse(result["rag_on_is_a"])
        self.assertEqual(result["winner_side"], "rag_off")

    def test_winner_b_inverts_relative_to_winner_a(self):
        payload = json.loads(VALID_RESPONSE)
        payload["winner"] = "B"
        fixture = _make_fixture()
        client = _client_returning(json.dumps(payload))

        result = compare(client, "m", "tb", fixture, "src", "off", "on", rng=_FixedRng(0.1))

        self.assertTrue(result["rag_on_is_a"])
        self.assertEqual(result["winner_side"], "rag_off")

    def test_tie_maps_to_tie_regardless_of_position(self):
        payload = json.loads(VALID_RESPONSE)
        payload["winner"] = "tie"
        fixture = _make_fixture()
        client = _client_returning(json.dumps(payload))

        result = compare(client, "m", "tb", fixture, "src", "off", "on", rng=_FixedRng(0.1))

        self.assertEqual(result["winner_side"], "tie")


class CompareJudgeFailureTest(unittest.TestCase):

    def test_malformed_response_recorded_as_judge_failure_not_dropped(self):
        client = _client_returning("not json")
        fixture = _make_fixture()

        result = compare(client, "m", "tb", fixture, "src", "off", "on", rng=_FixedRng(0.1))

        self.assertTrue(result["judge_failure"])
        self.assertIsNotNone(result["error"])
        self.assertIn("rag_on_is_a", result)
        self.assertIsNone(result["winner_side"])

    def test_client_exception_recorded_as_judge_failure(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("judge endpoint down")
        fixture = _make_fixture()

        result = compare(client, "m", "tb", fixture, "src", "off", "on", rng=_FixedRng(0.1))

        self.assertTrue(result["judge_failure"])
        self.assertIn("judge endpoint down", result["error"])


class CompareIncludesClaimsInResultTest(unittest.TestCase):
    """Regression coverage for claims not reaching the results file: assert
    the actual dict compare() returns, not just that parsing didn't raise.
    """

    def test_claims_are_present_in_both_sides_of_the_result(self):
        fixture = _make_fixture()
        client = _client_returning(VALID_RESPONSE)

        result = compare(client, "m", "tb", fixture, "src", "off", "on", rng=_FixedRng(0.1))

        self.assertEqual(
            result["a"]["claims"], [{"claim": "the fix is in sample()", "status": "verified"}]
        )
        self.assertEqual(
            result["b"]["claims"],
            [{"claim": "the fix is in some_other_func()", "status": "contradicted"}],
        )

    def test_result_sides_have_exactly_the_expected_keys(self):
        fixture = _make_fixture()
        client = _client_returning(VALID_RESPONSE)

        result = compare(client, "m", "tb", fixture, "src", "off", "on", rng=_FixedRng(0.1))

        self.assertEqual(set(result["a"].keys()), set(SIDE_RESULT_KEYS))
        self.assertEqual(set(result["b"].keys()), set(SIDE_RESULT_KEYS))


class ExplanationStatesSpecificDetailsTest(unittest.TestCase):

    def test_none_or_empty_is_false(self):
        self.assertFalse(_explanation_states_specific_details(None))
        self.assertFalse(_explanation_states_specific_details(""))

    def test_plain_prose_is_false(self):
        self.assertFalse(
            _explanation_states_specific_details(
                "The post could not be found, so accessing its title failed."
            )
        )

    def test_backtick_code_span_is_true(self):
        self.assertTrue(
            _explanation_states_specific_details("The fix belongs in `post_preview`.")
        )

    def test_function_call_is_true(self):
        self.assertTrue(
            _explanation_states_specific_details("Call get_object_or_404(Post, slug=slug) instead.")
        )

    def test_named_file_is_true(self):
        self.assertTrue(
            _explanation_states_specific_details("The bug is in blog/views.py near the top.")
        )


class CompareClaimsGuardTest(unittest.TestCase):

    def _response_with_empty_claims(self, winner="A"):
        payload = json.loads(VALID_RESPONSE)
        payload["a"]["claims"] = []
        payload["b"]["claims"] = []
        payload["winner"] = winner
        return json.dumps(payload)

    def test_empty_claims_with_code_specific_explanation_is_a_judge_failure(self):
        fixture = _make_fixture()
        client = _client_returning(self._response_with_empty_claims())

        result = compare(
            client, "m", "tb", fixture, "src",
            "Call `post_preview` with the right kwargs.", "on", rng=_FixedRng(0.1),
        )

        self.assertTrue(result["judge_failure"])
        self.assertIn("empty claims list", result["error"])

    def test_empty_claims_with_plain_explanation_is_not_a_failure(self):
        fixture = _make_fixture()
        client = _client_returning(self._response_with_empty_claims())

        result = compare(
            client, "m", "tb", fixture, "src",
            "The post was missing, so the page couldn't be shown.",
            "It also didn't work.",
            rng=_FixedRng(0.1),
        )

        self.assertFalse(result["judge_failure"])

    def test_non_empty_claims_with_code_specific_explanation_is_not_a_failure(self):
        fixture = _make_fixture()
        client = _client_returning(VALID_RESPONSE)  # both sides have claims

        result = compare(
            client, "m", "tb", fixture, "src",
            "Call `post_preview` with the right kwargs.", "on", rng=_FixedRng(0.1),
        )

        self.assertFalse(result["judge_failure"])
