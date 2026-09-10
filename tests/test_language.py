from unittest.mock import MagicMock, patch

from django.test import RequestFactory, SimpleTestCase, override_settings

from explain_errors.middleware import (
    DEFAULT_MAX_TOKENS,
    EXPLANATION_WORD_BUDGET,
    LANGUAGE_MAX_TOKENS_MULTIPLIER,
    SYSTEM_PROMPT,
    ExplainErrorsMiddleware,
)


def _mock_openai():
    patcher = patch("openai.OpenAI")
    mock_cls = patcher.start()
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[
            MagicMock(message=MagicMock(content="Mocked explanation."), finish_reason="stop")
        ]
    )
    mock_cls.return_value = client
    return patcher, client


@override_settings(DEBUG=True, OPENAI_API_KEY="test-key")
class LanguageUnsetTest(SimpleTestCase):
    """Case 1: no EXPLAIN_ERRORS_LANGUAGE must not change existing output."""

    def setUp(self):
        self.factory = RequestFactory()
        self.patcher, self.client = _mock_openai()
        self.addCleanup(self.patcher.stop)

    def test_system_prompt_byte_identical_when_language_unset(self):
        mw = ExplainErrorsMiddleware(lambda r: None)
        mw.process_exception(self.factory.get("/"), Exception("x"))

        messages = self.client.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(messages[0]["content"], SYSTEM_PROMPT)

    def test_user_prompt_byte_identical_when_language_unset(self):
        # Invariant 10: the language clause belongs in the system message
        # only. Confirm the user-message prompt is unaffected.
        mw = ExplainErrorsMiddleware(lambda r: None)
        request = self.factory.get("/")

        try:
            raise ValueError("boom")
        except ValueError as exc:
            mw.process_exception(request, exc)

        messages = self.client.chat.completions.create.call_args.kwargs["messages"]
        # Re-derive what the traceback-only prompt should look like: same
        # approach as tests/test_rag.py's byte-identical check.
        expected_prefix = "Explain the following Django error in simple terms:\n\n"
        self.assertTrue(messages[1]["content"].startswith(expected_prefix))
        self.assertIn("ValueError", messages[1]["content"])
        self.assertIn("boom", messages[1]["content"])


@override_settings(
    DEBUG=True, OPENAI_API_KEY="test-key", EXPLAIN_ERRORS_LANGUAGE="Spanish"
)
class LanguageConfiguredTest(SimpleTestCase):
    """Cases 2 & 3: the language clause and identifier-preservation
    instruction appear in the system prompt when configured."""

    def setUp(self):
        self.factory = RequestFactory()
        self.patcher, self.client = _mock_openai()
        self.addCleanup(self.patcher.stop)

    def test_language_clause_appears_in_system_prompt(self):
        mw = ExplainErrorsMiddleware(lambda r: None)
        mw.process_exception(self.factory.get("/"), Exception("x"))

        messages = self.client.chat.completions.create.call_args.kwargs["messages"]
        system_prompt = messages[0]["content"]
        self.assertIn("Spanish", system_prompt)
        self.assertTrue(system_prompt.startswith(SYSTEM_PROMPT))

    def test_identifier_preservation_instruction_appears(self):
        mw = ExplainErrorsMiddleware(lambda r: None)
        mw.process_exception(self.factory.get("/"), Exception("x"))

        system_prompt = self.client.chat.completions.create.call_args.kwargs["messages"][
            0
        ]["content"]
        self.assertIn("Django and Python", system_prompt)
        self.assertIn("English", system_prompt)

    def test_user_prompt_still_byte_identical_when_language_configured(self):
        # Invariant 10: configuring a language must not touch the
        # traceback-only user-message prompt.
        mw = ExplainErrorsMiddleware(lambda r: None)
        request = self.factory.get("/")

        with patch(
            "explain_errors.middleware.sanitize_traceback",
            side_effect=lambda tb: tb,
        ):
            try:
                raise ValueError("boom")
            except ValueError as exc:
                mw.process_exception(request, exc)

        messages = self.client.chat.completions.create.call_args.kwargs["messages"]
        self.assertTrue(
            messages[1]["content"].startswith(
                "Explain the following Django error in simple terms:\n\n"
            )
        )
        self.assertNotIn("Spanish", messages[1]["content"])


@override_settings(DEBUG=True, OPENAI_API_KEY="test-key")
class MaxTokensLanguageScalingTest(SimpleTestCase):
    """Case 4: the scaled ceiling covers the word budget, and applies
    uniformly to any configured language rather than only ones anticipated
    in advance — that's the no-cliff property a per-language table would
    have broken."""

    def setUp(self):
        self.patcher, self.client = _mock_openai()
        self.addCleanup(self.patcher.stop)

    def test_default_ceiling_used_when_language_unset(self):
        mw = ExplainErrorsMiddleware(lambda r: None)
        self.assertEqual(mw.max_tokens, DEFAULT_MAX_TOKENS)

    def test_scaled_ceiling_covers_word_budget(self):
        scaled = int(DEFAULT_MAX_TOKENS * LANGUAGE_MAX_TOKENS_MULTIPLIER)
        self.assertGreaterEqual(scaled, EXPLANATION_WORD_BUDGET * 4)

    def test_scaled_ceiling_applies_for_an_estimated_language(self):
        with override_settings(EXPLAIN_ERRORS_LANGUAGE="Japanese"):
            mw = ExplainErrorsMiddleware(lambda r: None)
            self.assertEqual(
                mw.max_tokens, int(DEFAULT_MAX_TOKENS * LANGUAGE_MAX_TOKENS_MULTIPLIER)
            )

    def test_scaled_ceiling_applies_for_a_language_not_in_the_estimate(self):
        # No per-language table to fall through: Chinese was never
        # estimated, and still gets the same scaled ceiling as Japanese.
        with override_settings(EXPLAIN_ERRORS_LANGUAGE="Chinese"):
            mw = ExplainErrorsMiddleware(lambda r: None)
            self.assertEqual(
                mw.max_tokens, int(DEFAULT_MAX_TOKENS * LANGUAGE_MAX_TOKENS_MULTIPLIER)
            )

    def test_explicit_openai_max_tokens_overrides_language_scaling(self):
        with override_settings(
            EXPLAIN_ERRORS_LANGUAGE="Hindi", OPENAI_MAX_TOKENS=42
        ):
            mw = ExplainErrorsMiddleware(lambda r: None)
            self.assertEqual(mw.max_tokens, 42)
