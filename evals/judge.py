"""The eval harness's judge: a second, deliberately different model that
compares a RAG-off and a RAG-on explanation of the same fixture failure
against the fixture's known-good facts.

Client config is read from the environment and is never shared with the
generator's OPENAI_* settings (see docs/tasks/eval-harness.md section 4):

    EVAL_JUDGE_BASE_URL   e.g. an OpenRouter-compatible endpoint
    EVAL_JUDGE_API_KEY
    EVAL_JUDGE_MODEL

Which of RAG-on and RAG-off is shown to the judge as "A" is randomized per
comparison (see `compare`), and the mapping is recorded in the result, so
position bias can't leak into the win counts.
"""
import json
import random

QUESTION_KEYS = (
    "identifies_cause",
    "points_to_fix_location",
    "fix_would_work",
    "written_for_learner",
)

# Module-level and easy to edit: the maintainer will iterate on this once
# real judge output is available. Keep the JSON shape in sync with
# parse_judge_response below if you change it.
JUDGE_PROMPT_TEMPLATE = """\
You are evaluating two AI-generated explanations of the same Django error, \
written for a developer who is still learning Django. Decide which \
explanation is more useful. You do not know which explanation (if either) \
had access to the project's source code when it was written -- judge only \
what is written.

## Traceback

{traceback}

## Known-good facts about this error

Cause: {expected_cause}
Where the fix belongs: {expected_fix_location}

## Explanation A

{explanation_a}

## Explanation B

{explanation_b}

## Your task

For EACH explanation (A and B), answer yes or no to:
1. identifies_cause: Does it correctly identify the cause described above?
2. points_to_fix_location: Does it point the developer to the fix location \
above, by file and function?
3. fix_would_work: Does it propose a fix that would actually resolve the \
error?
4. written_for_learner: Is it written for someone learning Django, rather \
than assuming they already know the framework?

Then pick an overall winner: "A", "B", or "tie" if they are equally good. \
Do not favor the longer or more confident-sounding explanation -- favor the \
one that is more correct and more useful to a learner.

Respond with ONLY a JSON object, no other text, in exactly this shape:

{{
  "a": {{"identifies_cause": true, "points_to_fix_location": false, "fix_would_work": true, "written_for_learner": true}},
  "b": {{"identifies_cause": true, "points_to_fix_location": true, "fix_would_work": true, "written_for_learner": false}},
  "winner": "B",
  "reasoning": "One sentence explaining the winner."
}}
"""


class JudgeParseError(Exception):
    """Raised when the judge's response cannot be parsed into the expected
    shape. Callers record this as a judge failure, never silently drop it.
    """


def get_judge_client():
    import os

    from openai import OpenAI

    api_key = os.environ.get("EVAL_JUDGE_API_KEY")
    if not api_key:
        raise ValueError(
            "EVAL_JUDGE_API_KEY is not set. The judge intentionally never "
            "reuses the generator's OPENAI_API_KEY."
        )
    kwargs = {"api_key": api_key}
    base_url = os.environ.get("EVAL_JUDGE_BASE_URL")
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def get_judge_model():
    import os

    model = os.environ.get("EVAL_JUDGE_MODEL")
    if not model:
        raise ValueError("EVAL_JUDGE_MODEL is not set.")
    return model


def build_judge_prompt(traceback_text, fixture, explanation_a, explanation_b):
    return JUDGE_PROMPT_TEMPLATE.format(
        traceback=traceback_text,
        expected_cause=fixture.expected_cause,
        expected_fix_location=fixture.expected_fix_location,
        explanation_a=explanation_a or "(no explanation was produced)",
        explanation_b=explanation_b or "(no explanation was produced)",
    )


def parse_judge_response(raw_text):
    """Strictly parse the judge's raw response text into the expected dict
    shape. Raises JudgeParseError on anything that doesn't match -- a
    malformed response is a judge failure, never silently dropped.
    """
    try:
        parsed = json.loads(raw_text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise JudgeParseError(f"response is not valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise JudgeParseError("response is not a JSON object")

    for side in ("a", "b"):
        side_value = parsed.get(side)
        if not isinstance(side_value, dict):
            raise JudgeParseError(f"{side!r} section is missing or not an object")
        for key in QUESTION_KEYS:
            if not isinstance(side_value.get(key), bool):
                raise JudgeParseError(f"{side}.{key} is missing or not a boolean")

    if parsed.get("winner") not in ("A", "B", "tie"):
        raise JudgeParseError(
            f"winner must be 'A', 'B', or 'tie', got {parsed.get('winner')!r}"
        )

    if not isinstance(parsed.get("reasoning"), str) or not parsed["reasoning"].strip():
        raise JudgeParseError("reasoning is missing or not a non-empty string")

    return parsed


def _winner_side(winner, rag_on_is_a):
    if winner == "tie":
        return "tie"
    is_a = winner == "A"
    return "rag_on" if (is_a == rag_on_is_a) else "rag_off"


def compare(
    client,
    model,
    traceback_text,
    fixture,
    rag_off_explanation,
    rag_on_explanation,
    rng=None,
):
    """Judge rag_off vs rag_on for one fixture call.

    Returns a dict recording which side won ("rag_on", "rag_off", or "tie"),
    the randomized A/B mapping (`rag_on_is_a`), and the raw per-question
    answers -- or, on any failure to get a well-formed response, a judge
    failure record with the mapping still included.
    """
    rng = rng if rng is not None else random
    rag_on_is_a = rng.random() < 0.5
    if rag_on_is_a:
        explanation_a, explanation_b = rag_on_explanation, rag_off_explanation
    else:
        explanation_a, explanation_b = rag_off_explanation, rag_on_explanation

    prompt = build_judge_prompt(traceback_text, fixture, explanation_a, explanation_b)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = response.choices[0].message.content
        parsed = parse_judge_response(raw_text)
    except Exception as exc:
        return {
            "fixture": fixture.name,
            "group": fixture.group,
            "judge_failure": True,
            "error": str(exc),
            "rag_on_is_a": rag_on_is_a,
            "winner": None,
            "winner_side": None,
            "a": None,
            "b": None,
            "reasoning": None,
        }

    winner = parsed["winner"]
    return {
        "fixture": fixture.name,
        "group": fixture.group,
        "judge_failure": False,
        "error": None,
        "rag_on_is_a": rag_on_is_a,
        "winner": winner,
        "winner_side": _winner_side(winner, rag_on_is_a),
        "a": parsed["a"],
        "b": parsed["b"],
        "reasoning": parsed["reasoning"],
    }
