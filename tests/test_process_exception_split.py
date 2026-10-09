from io import StringIO
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from explain_errors.middleware import ExplainErrorsMiddleware


def _exception():
    try:
        raise ValueError("split boom")
    except ValueError as exc:
        return exc


def _middleware():
    with patch("openai.OpenAI"):
        return ExplainErrorsMiddleware(lambda r: None)


def _response(content="Mocked explanation.", finish_reason="stop"):
    choice = MagicMock(finish_reason=finish_reason)
    choice.message.content = content
    return MagicMock(choices=[choice])


@override_settings(DEBUG=True, OPENAI_API_KEY="test-key")
class BuildPromptTest(SimpleTestCase):

    @override_settings(EXPLAIN_ERRORS_RAG_ENABLED=False)
    def test_rag_off_is_traceback_only_and_skips_retrieval(self):
        mw = _middleware()
        with patch("explain_errors.middleware.retrieve_chunks") as retrieve:
            prompt = mw._build_prompt(_exception())
        retrieve.assert_not_called()
        self.assertTrue(
            prompt.startswith("Explain the following Django error in simple terms:\n\n")
        )
        self.assertIn("split boom", prompt)

    @override_settings(EXPLAIN_ERRORS_RAG_ENABLED=False)
    def test_prompt_uses_module_level_sanitize_traceback(self):
        mw = _middleware()
        with patch(
            "explain_errors.middleware.sanitize_traceback", return_value="SANITIZED"
        ) as sanitize:
            prompt = mw._build_prompt(_exception())
        sanitize.assert_called_once()
        self.assertTrue(prompt.endswith("\n\nSANITIZED"))

    @override_settings(EXPLAIN_ERRORS_RAG_ENABLED=True)
    def test_rag_on_appends_section(self):
        mw = _middleware()
        with patch(
            "explain_errors.middleware.retrieve_chunks", return_value=["chunk"]
        ), patch(
            "explain_errors.middleware.format_chunks_for_prompt",
            return_value="RAG SECTION",
        ):
            prompt = mw._build_prompt(_exception())
        self.assertIn("split boom", prompt)
        self.assertTrue(prompt.endswith("\n\nRAG SECTION"))

    @override_settings(EXPLAIN_ERRORS_RAG_ENABLED=True)
    def test_rag_empty_section_leaves_prompt_unchanged(self):
        mw = _middleware()
        exc = _exception()
        with patch("explain_errors.middleware.retrieve_chunks", return_value=[]), patch(
            "explain_errors.middleware.format_chunks_for_prompt", return_value=""
        ):
            with_rag = mw._build_prompt(exc)
        with override_settings(EXPLAIN_ERRORS_RAG_ENABLED=False):
            without_rag = mw._build_prompt(exc)
        self.assertEqual(with_rag, without_rag)

    @override_settings(EXPLAIN_ERRORS_RAG_ENABLED=True)
    def test_rag_failure_degrades_to_traceback_only(self):
        mw = _middleware()
        exc = _exception()
        with override_settings(EXPLAIN_ERRORS_RAG_ENABLED=False):
            expected = mw._build_prompt(exc)
        with patch(
            "explain_errors.middleware.retrieve_chunks", side_effect=RuntimeError("no index")
        ), patch("explain_errors.middleware.logger.warning") as warning:
            prompt = mw._build_prompt(exc)
        self.assertEqual(prompt, expected)
        warning.assert_called_once()
        self.assertIn("RAG retrieval failed", warning.call_args[0][0])


@override_settings(DEBUG=True, OPENAI_API_KEY="test-key")
class GenerateExplanationTest(SimpleTestCase):

    def _generate(self, mw, prompt="p"):
        with patch("sys.stdout", new_callable=StringIO) as out:
            result = mw._generate_explanation(prompt)
        return result, out.getvalue()

    def test_success_returns_text_and_prints(self):
        mw = _middleware()
        mw.openai_client.chat.completions.create.return_value = _response("Fix it.")
        result, out = self._generate(mw, "my prompt")
        self.assertEqual(result, "Fix it.")
        self.assertIn("Fix it.", out)
        kwargs = mw.openai_client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["messages"][1], {"role": "user", "content": "my prompt"})
        self.assertEqual(kwargs["messages"][0]["content"], mw.system_prompt)

    @override_settings(EXPLAIN_ERRORS_PRINT_STDOUT=False)
    def test_stdout_suppressed_when_print_disabled(self):
        mw = _middleware()
        mw.openai_client.chat.completions.create.return_value = _response("Fix it.")
        result, out = self._generate(mw)
        self.assertEqual(result, "Fix it.")
        self.assertEqual(out, "")

    def test_api_error_returns_none_and_prints_failure(self):
        mw = _middleware()
        mw.openai_client.chat.completions.create.side_effect = RuntimeError("api down")
        result, out = self._generate(mw)
        self.assertIsNone(result)
        self.assertIn("Failed to get an explanation from the model API:", out)
        self.assertIn("api down", out)

    @override_settings(EXPLAIN_ERRORS_PRINT_STDOUT=False)
    def test_api_error_message_prints_even_with_stdout_disabled(self):
        mw = _middleware()
        mw.openai_client.chat.completions.create.side_effect = RuntimeError("api down")
        result, out = self._generate(mw)
        self.assertIsNone(result)
        self.assertIn("api down", out)

    def test_truncation_warns_and_still_returns_text(self):
        mw = _middleware()
        mw.openai_client.chat.completions.create.return_value = _response(
            "Cut off", finish_reason="length"
        )
        with patch("explain_errors.middleware.logger.warning") as warning:
            result, _ = self._generate(mw)
        self.assertEqual(result, "Cut off")
        warning.assert_called_once()
        self.assertIn("truncated", warning.call_args[0][0])

    def test_non_length_finish_reason_does_not_warn(self):
        mw = _middleware()
        mw.openai_client.chat.completions.create.return_value = _response("ok")
        with patch("explain_errors.middleware.logger.warning") as warning:
            self._generate(mw)
        warning.assert_not_called()

    def test_empty_choices_fails_open(self):
        mw = _middleware()
        mw.openai_client.chat.completions.create.return_value = MagicMock(choices=[])
        result, out = self._generate(mw)
        self.assertIsNone(result)
        self.assertIn("Failed to get an explanation from the model API:", out)
