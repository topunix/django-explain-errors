"""Subprocess helper for test_eval_run.EvalHarnessEndToEndTest.

Runs `evals.run.main()` with both the generator (chat + embeddings) and the
judge fully mocked, in a separate process from the main test suite.

That separation is required, not just convenient: `evals.run.main()` calls
`django.setup()` against the fixture app's settings (INSTALLED_APPS includes
`blog`), but the test suite process has already populated Django's app
registry from `test_settings` (INSTALLED_APPS = ["explain_errors"]). Django
populates its app registry once per process and ignores later attempts to
redo it with different settings, so `blog.models` would never become a
valid app in-process -- this has to run somewhere Django hasn't been set up
yet.
"""
import os
import sys
from unittest.mock import MagicMock, patch

JUDGE_RESPONSE_TEXT = (
    '{"a": {"identifies_cause": true, "points_to_fix_location": true, '
    '"fix_would_work": true, "written_for_learner": true, "no_fabrication": true}, '
    '"b": {"identifies_cause": true, "points_to_fix_location": false, '
    '"fix_would_work": true, "written_for_learner": false, "no_fabrication": true}, '
    '"winner": "A", "reasoning": "A names the cause and the fix location; B does not."}'
)


def _fake_generator_client(**kwargs):
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[
            MagicMock(
                message=MagicMock(content="Mocked generator explanation."),
                finish_reason="stop",
            )
        ],
        usage=MagicMock(prompt_tokens=10, completion_tokens=5),
    )
    client.embeddings.create.side_effect = lambda model, input: MagicMock(
        data=[MagicMock(embedding=[0.1, 0.2, 0.3]) for _ in input]
    )
    return client


def _fake_judge_client():
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=JUDGE_RESPONSE_TEXT))]
    )
    return client


def main():
    os.environ["OPENAI_API_KEY"] = "generator-test-key"
    os.environ["EVAL_JUDGE_API_KEY"] = "judge-test-key"
    os.environ["EVAL_JUDGE_MODEL"] = "judge-test-model"
    os.environ.pop("EVAL_JUDGE_BASE_URL", None)

    with patch("openai.OpenAI", side_effect=_fake_generator_client):
        with patch("evals.judge.get_judge_client", return_value=_fake_judge_client()):
            from evals.run import main as run_main

            run_main(sys.argv[1:])


if __name__ == "__main__":
    main()
