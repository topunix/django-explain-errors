import logging
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, SimpleTestCase, override_settings

from explain_errors.middleware import (
    DEFAULT_MAX_TOKENS,
    EXPLANATION_WORD_BUDGET,
    SYSTEM_PROMPT,
    ExplainErrorsMiddleware,
)


def _mock_openai(finish_reason="stop"):
    """Patch the chat-completion OpenAI client used by the middleware."""
    patcher = patch("openai.OpenAI")
    mock_cls = patcher.start()
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[
            MagicMock(
                message=MagicMock(content="Mocked explanation."),
                finish_reason=finish_reason,
            )
        ]
    )
    mock_cls.return_value = client
    return patcher, client


class TruncationConstantsTest(SimpleTestCase):
    """Case 1 & 2: only the constants can catch drift between the token
    ceiling and the word budget the prompt states; a mocked API response
    cannot, since the mock ignores max_tokens entirely.
    """

    def test_default_max_tokens_has_generous_headroom_over_word_budget(self):
        self.assertGreaterEqual(DEFAULT_MAX_TOKENS, EXPLANATION_WORD_BUDGET * 4)

    def test_system_prompt_states_the_word_budget(self):
        self.assertIn(str(EXPLANATION_WORD_BUDGET), SYSTEM_PROMPT)


@override_settings(DEBUG=True, OPENAI_API_KEY="test-key")
class MaxTokensConfigTest(SimpleTestCase):

    def setUp(self):
        self.factory = RequestFactory()
        self.patcher, self.client = _mock_openai()
        self.addCleanup(self.patcher.stop)

    def test_default_max_tokens_exceeds_old_150_cap(self):
        mw = ExplainErrorsMiddleware(lambda r: None)
        self.assertEqual(mw.max_tokens, DEFAULT_MAX_TOKENS)
        self.assertGreater(mw.max_tokens, 150)

    @override_settings(OPENAI_MAX_TOKENS=42)
    def test_configured_max_tokens_reaches_openai_call(self):
        mw = ExplainErrorsMiddleware(lambda r: None)
        mw.process_exception(self.factory.get("/"), Exception("x"))
        kwargs = self.client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["max_tokens"], 42)


@override_settings(DEBUG=True, OPENAI_API_KEY="test-key")
class TruncationWarningTest(SimpleTestCase):

    def setUp(self):
        self.factory = RequestFactory()

    def test_finish_reason_length_logs_truncation_warning(self):
        patcher, client = _mock_openai(finish_reason="length")
        self.addCleanup(patcher.stop)
        mw = ExplainErrorsMiddleware(lambda r: None)

        with self.assertLogs("explain_errors", level="WARNING") as cm:
            mw.process_exception(self.factory.get("/"), Exception("x"))

        self.assertTrue(any("truncated" in record.getMessage() for record in cm.records))

    def test_finish_reason_stop_logs_no_warning(self):
        # assertNoLogs needs Python 3.10+; this package supports 3.9 (CLAUDE.md
        # invariant 6), so a sentinel INFO record keeps assertLogs from raising
        # on an empty capture and we assert no WARNING-or-above record joined it.
        patcher, client = _mock_openai(finish_reason="stop")
        self.addCleanup(patcher.stop)
        mw = ExplainErrorsMiddleware(lambda r: None)
        logger = logging.getLogger("explain_errors")

        with self.assertLogs("explain_errors", level="INFO") as cm:
            logger.info("sentinel")
            mw.process_exception(self.factory.get("/"), Exception("x"))

        warnings = [r for r in cm.records if r.levelno >= logging.WARNING]
        self.assertEqual(warnings, [])
