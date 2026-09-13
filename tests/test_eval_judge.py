"""Plumbing tests for the eval harness's judge (evals/judge.py).

Fully mocked per docs/tasks/eval-harness.md section 6: no real API calls.
"""
import json
import unittest
from unittest.mock import MagicMock

from evals.fixtures import Fixture
from evals.judge import JudgeParseError, compare, parse_judge_response


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
            "identifies_cause": True,
            "points_to_fix_location": True,
            "fix_would_work": True,
            "written_for_learner": True,
        },
        "b": {
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
            if compare(client, "judge-model", "tb", fixture, "off", "on", rng=rng)[
                "rag_on_is_a"
            ]
        )

        fraction = a_count / n
        self.assertGreater(fraction, 0.4)
        self.assertLess(fraction, 0.6)

    def test_winner_a_maps_to_rag_on_when_rag_on_is_a(self):
        fixture = _make_fixture()
        client = _client_returning(VALID_RESPONSE)  # winner "A"

        result = compare(client, "m", "tb", fixture, "off", "on", rng=_FixedRng(0.1))

        self.assertTrue(result["rag_on_is_a"])
        self.assertEqual(result["winner_side"], "rag_on")

    def test_winner_a_maps_to_rag_off_when_rag_off_is_a(self):
        fixture = _make_fixture()
        client = _client_returning(VALID_RESPONSE)  # winner "A"

        result = compare(client, "m", "tb", fixture, "off", "on", rng=_FixedRng(0.9))

        self.assertFalse(result["rag_on_is_a"])
        self.assertEqual(result["winner_side"], "rag_off")

    def test_winner_b_inverts_relative_to_winner_a(self):
        payload = json.loads(VALID_RESPONSE)
        payload["winner"] = "B"
        fixture = _make_fixture()
        client = _client_returning(json.dumps(payload))

        result = compare(client, "m", "tb", fixture, "off", "on", rng=_FixedRng(0.1))

        self.assertTrue(result["rag_on_is_a"])
        self.assertEqual(result["winner_side"], "rag_off")

    def test_tie_maps_to_tie_regardless_of_position(self):
        payload = json.loads(VALID_RESPONSE)
        payload["winner"] = "tie"
        fixture = _make_fixture()
        client = _client_returning(json.dumps(payload))

        result = compare(client, "m", "tb", fixture, "off", "on", rng=_FixedRng(0.1))

        self.assertEqual(result["winner_side"], "tie")


class CompareJudgeFailureTest(unittest.TestCase):

    def test_malformed_response_recorded_as_judge_failure_not_dropped(self):
        client = _client_returning("not json")
        fixture = _make_fixture()

        result = compare(client, "m", "tb", fixture, "off", "on", rng=_FixedRng(0.1))

        self.assertTrue(result["judge_failure"])
        self.assertIsNotNone(result["error"])
        self.assertIn("rag_on_is_a", result)
        self.assertIsNone(result["winner_side"])

    def test_client_exception_recorded_as_judge_failure(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("judge endpoint down")
        fixture = _make_fixture()

        result = compare(client, "m", "tb", fixture, "off", "on", rng=_FixedRng(0.1))

        self.assertTrue(result["judge_failure"])
        self.assertIn("judge endpoint down", result["error"])
