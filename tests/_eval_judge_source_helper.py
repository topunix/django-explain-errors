"""Subprocess helper for test_eval_run.BuildJudgeSourceTest.

evals.run._build_judge_source calls django.urls.resolve against the
fixture app's URLconf, which -- like test_eval_fixtures.py's URL
resolution test and tests/_eval_harness_e2e_runner.py -- needs the fixture
app's own settings loaded (blog in INSTALLED_APPS), not this suite's
test_settings. It also needs a real captured exception (with its original
__traceback__) for the frames-based path, which means actually driving a
request through the fixture app. Both requirements mean this has to run in
its own process, not inside the main test suite.
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch


def main():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fixture_app_dir = os.path.join(repo_root, "evals", "fixture_app")
    sys.path.insert(0, repo_root)
    sys.path.insert(0, fixture_app_dir)
    os.environ["DJANGO_SETTINGS_MODULE"] = "settings"
    os.environ["OPENAI_API_KEY"] = "test-key"

    import django

    django.setup()

    from django.test import Client
    from django.test.utils import override_settings

    from evals.fixtures import FIXTURES_BY_NAME
    from evals.run import _build_judge_source, _capture_call, _hit_fixture, _reset_database

    _reset_database()

    fixture_names = ["none_attribute", "str_recursion", "unexpected_kwarg", "url_name_typo"]

    results = {}
    with override_settings(DEBUG=True, EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE=False):
        with patch("openai.OpenAI") as mock_cls:
            client = MagicMock()
            client.chat.completions.create.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(content="x"), finish_reason="stop")]
            )
            mock_cls.return_value = client
            test_client = Client(raise_request_exception=False)
            for name in fixture_names:
                fixture = FIXTURES_BY_NAME[name]
                _response, captured = _capture_call(lambda: _hit_fixture(test_client, fixture))
                sources = _build_judge_source(fixture, captured["exception"])
                results[name] = [{"label": label, "content": content} for label, content in sources]

    print(json.dumps(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
