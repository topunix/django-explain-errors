"""The eval harness's judge: a second, deliberately different model that
compares a RAG-off and a RAG-on explanation of the same fixture failure
against the fixture's known-good facts.

Client config is read from the environment and is never shared with the
generator's OPENAI_* settings (see evals/README.md's Environment variables
section):

    EVAL_JUDGE_BASE_URL   e.g. an OpenRouter-compatible endpoint
    EVAL_JUDGE_API_KEY
    EVAL_JUDGE_MODEL

Which of RAG-on and RAG-off is shown to the judge as "A" is randomized per
comparison (see `compare`), and the mapping is recorded in the result, so
position bias can't leak into the win counts.
"""
import json
import random
import re

QUESTION_KEYS = (
    "identifies_cause",
    "points_to_fix_location",
    "fix_would_work",
    "written_for_learner",
    "no_fabrication",
)

# Booleans the judge answers directly. no_fabrication is deliberately not
# here: it's derived from the "claims" list (see parse_judge_response),
# never asked for as a yes/no, because a direct yes/no question let the
# judge answer "unverified" without ever checking the source it was given.
JUDGE_ASKED_KEYS = (
    "identifies_cause",
    "points_to_fix_location",
    "fix_would_work",
    "written_for_learner",
)

CLAIM_STATUSES = ("verified", "contradicted", "absent")

# Cheap signals that an explanation asserts something specific enough that
# an empty claims list means the judge skipped the lookup step, not that
# there was nothing to check. Deliberately permissive (a few false
# positives on prose that merely mentions a word matching one of these are
# fine) -- the cost of a false positive is one avoidable judge_failure; the
# cost of a false negative is exactly the silent "empty claims = clean
# pass" bug this guards against.
_CODE_SPECIFIC_PATTERNS = (
    re.compile(r"`[^`]+`"),  # a backtick-quoted code span
    re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*\("),  # a function_call(
    re.compile(r"\b\w+\.(?:py|html|txt|js|css)\b"),  # a named file
)


def _explanation_states_specific_details(explanation_text):
    if not explanation_text:
        return False
    return any(pattern.search(explanation_text) for pattern in _CODE_SPECIFIC_PATTERNS)


# Module-level and easy to edit: the maintainer will iterate on this once
# real judge output is available. Keep the JSON shape in sync with
# parse_judge_response below if you change it.
JUDGE_PROMPT_TEMPLATE = """\
You are evaluating two AI-generated explanations of the same Django error, \
written for a developer who is still learning Django. Decide which \
explanation is more useful. You do not know which explanation (if either) \
had access to the project's source code when it was written -- both are \
checked against the same source, shown below, so its presence can't tell \
you which is which.

## Traceback

{traceback}

## Known-good facts about this error

Cause: {expected_cause}
Where the fix belongs: {expected_fix_location}

## Relevant project source

{source}

## Explanation A

{explanation_a}

## Explanation B

{explanation_b}

## Your task

For EACH explanation (A and B), first list its specific claims -- every \
function name, parameter, file, template, or other code-level detail it \
states as fact. For each claim, look it up against the traceback, the \
known facts, and the source above, and mark it:
- "verified": the traceback, known facts, or source above confirms it.
- "contradicted": the traceback, known facts, or source above shows it's \
wrong.
- "absent": none of them address it either way. The source section is a \
small excerpt of the project, not the whole thing, so "absent" is not \
evidence the claim is made up -- only "contradicted" is.
An explanation with no specific claims gets an empty claims list.

Then, still for EACH explanation, answer yes or no to:
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
  "a": {{
    "claims": [
      {{"claim": "the fix belongs in post_preview's view function", "status": "verified"}},
      {{"claim": "revision is an unused parameter", "status": "contradicted"}}
    ],
    "identifies_cause": true,
    "points_to_fix_location": false,
    "fix_would_work": true,
    "written_for_learner": true
  }},
  "b": {{
    "claims": [],
    "identifies_cause": true,
    "points_to_fix_location": true,
    "fix_would_work": true,
    "written_for_learner": false
  }},
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


def format_source_section(sources):
    """Render (label, content) pairs -- e.g. [("blog/views.py", "..."), ...]
    -- into the judge prompt's "Relevant project source" block.

    Callers (see evals/run.py) always pass the same fixed set of modules
    plus whatever a fixture's own `templates` field names, for every
    comparison, regardless of which side had RAG -- so the shape of this
    section itself can't tip the judge off.
    """
    if not sources:
        return "(no project source available)"
    return "\n\n".join(
        f"### {label}\n```\n{content.rstrip()}\n```" for label, content in sources
    )


def build_judge_prompt(traceback_text, fixture, source_text, explanation_a, explanation_b):
    return JUDGE_PROMPT_TEMPLATE.format(
        traceback=traceback_text,
        expected_cause=fixture.expected_cause,
        expected_fix_location=fixture.expected_fix_location,
        source=source_text,
        explanation_a=explanation_a or "(no explanation was produced)",
        explanation_b=explanation_b or "(no explanation was produced)",
    )


def _parse_claims(side, side_value):
    """Validate side_value["claims"] and return it unchanged. Strict on
    purpose: a response missing the claim lists skipped the lookup step
    entirely, and that's a judge failure, not a silent pass with
    no_fabrication defaulting to true.
    """
    claims = side_value.get("claims")
    if not isinstance(claims, list):
        raise JudgeParseError(f"{side}.claims is missing or not a list")

    for i, claim in enumerate(claims):
        if not isinstance(claim, dict):
            raise JudgeParseError(f"{side}.claims[{i}] is not an object")
        if not isinstance(claim.get("claim"), str) or not claim["claim"].strip():
            raise JudgeParseError(f"{side}.claims[{i}].claim is missing or not a non-empty string")
        if claim.get("status") not in CLAIM_STATUSES:
            raise JudgeParseError(
                f"{side}.claims[{i}].status must be one of {CLAIM_STATUSES}, "
                f"got {claim.get('status')!r}"
            )

    return claims


def parse_judge_response(raw_text):
    """Strictly parse the judge's raw response text into the expected dict
    shape. Raises JudgeParseError on anything that doesn't match -- a
    malformed response is a judge failure, never silently dropped.

    no_fabrication is not read from the judge's answer -- it's derived here
    from the per-claim "claims" list: false only if some claim is
    "contradicted". A claim marked "absent" is not a fabrication, since the
    source section is a subset of the project, not the whole thing.
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
        for key in JUDGE_ASKED_KEYS:
            if not isinstance(side_value.get(key), bool):
                raise JudgeParseError(f"{side}.{key} is missing or not a boolean")

        claims = _parse_claims(side, side_value)
        side_value["no_fabrication"] = not any(claim["status"] == "contradicted" for claim in claims)

    if parsed.get("winner") not in ("A", "B", "tie"):
        raise JudgeParseError(
            f"winner must be 'A', 'B', or 'tie', got {parsed.get('winner')!r}"
        )

    if not isinstance(parsed.get("reasoning"), str) or not parsed["reasoning"].strip():
        raise JudgeParseError("reasoning is missing or not a non-empty string")

    return parsed


SIDE_RESULT_KEYS = ("claims",) + QUESTION_KEYS


def _side_result(side_value):
    """Build the per-side result dict explicitly, field by field, rather
    than passing `side_value` through as-is: a future change to what
    parse_judge_response returns should have to touch this list to change
    what lands in the results file, instead of "claims" (or anything else)
    silently riding along or silently dropping out.
    """
    return {key: side_value[key] for key in SIDE_RESULT_KEYS}


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
    source_text,
    rag_off_explanation,
    rag_on_explanation,
    rng=None,
):
    """Judge rag_off vs rag_on for one fixture call.

    `source_text` (see format_source_section) is the same for both sides of
    this comparison -- it exists so the judge can verify a specific claim
    against real source instead of treating anything outside the traceback
    and known facts as unverifiable.

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

    prompt = build_judge_prompt(traceback_text, fixture, source_text, explanation_a, explanation_b)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = response.choices[0].message.content
        parsed = parse_judge_response(raw_text)

        # Guard against the "empty claims list" version of a skipped lookup:
        # no_fabrication derives to true when claims is [], which is
        # indistinguishable from "checked and found nothing wrong" unless we
        # also check whether there was obviously something to check.
        for side, explanation in (("a", explanation_a), ("b", explanation_b)):
            if not parsed[side]["claims"] and _explanation_states_specific_details(explanation):
                raise JudgeParseError(
                    f"{side}'s explanation states specific, checkable details "
                    "but the judge returned an empty claims list for it -- "
                    "treating this as a skipped lookup, not a clean pass"
                )
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
        "a": _side_result(parsed["a"]),
        "b": _side_result(parsed["b"]),
        "reasoning": parsed["reasoning"],
    }
