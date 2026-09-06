import os
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from explain_errors.client import PLACEHOLDER_API_KEY, get_openai_client
from explain_errors.middleware import ExplainErrorsMiddleware


class GetOpenAIClientTest(SimpleTestCase):

    def _clear_env(self):
        """Pop the env vars the factory reads; patch.dict restores the
        original environment on exit regardless of these mutations."""
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("OPENAI_BASE_URL", None)

    @override_settings(OPENAI_API_KEY="test-key")
    def test_default_no_base_url_kwarg(self):
        with patch.dict(os.environ, {}, clear=False):
            self._clear_env()
            with patch("openai.OpenAI") as mock_cls:
                get_openai_client()

        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args.kwargs
        self.assertEqual(kwargs["api_key"], "test-key")
        self.assertNotIn("base_url", kwargs)

    @override_settings(
        OPENAI_API_KEY="test-key",
        OPENAI_BASE_URL="http://localhost:11434/v1",
    )
    def test_base_url_from_settings_passed_to_client(self):
        with patch.dict(os.environ, {}, clear=False):
            self._clear_env()
            with patch("openai.OpenAI") as mock_cls:
                get_openai_client()

        kwargs = mock_cls.call_args.kwargs
        self.assertEqual(kwargs["base_url"], "http://localhost:11434/v1")

    @override_settings(
        OPENAI_API_KEY="test-key",
        OPENAI_BASE_URL="http://settings-host/v1",
    )
    def test_base_url_env_var_takes_precedence_over_settings(self):
        with patch.dict(os.environ, {"OPENAI_BASE_URL": "http://env-host/v1"}):
            with patch("openai.OpenAI") as mock_cls:
                get_openai_client()

        kwargs = mock_cls.call_args.kwargs
        self.assertEqual(kwargs["base_url"], "http://env-host/v1")

    @override_settings(OPENAI_BASE_URL="http://localhost:11434/v1")
    def test_missing_key_with_base_url_uses_placeholder(self):
        with patch.dict(os.environ, {}, clear=False):
            self._clear_env()
            with patch("openai.OpenAI") as mock_cls:
                get_openai_client()

        kwargs = mock_cls.call_args.kwargs
        self.assertEqual(kwargs["api_key"], PLACEHOLDER_API_KEY)

    def test_missing_key_without_base_url_raises(self):
        with patch.dict(os.environ, {}, clear=False):
            self._clear_env()
            with self.assertRaises(ValueError) as ctx:
                get_openai_client()

        self.assertEqual(
            str(ctx.exception),
            "OpenAI API key not found. Please set the "
            "OPENAI_API_KEY environment variable.",
        )

    @override_settings(OPENAI_API_KEY="test-key")
    def test_timeout_forwarded_when_provided(self):
        with patch.dict(os.environ, {}, clear=False):
            self._clear_env()
            with patch("openai.OpenAI") as mock_cls:
                get_openai_client(timeout=15)

        self.assertEqual(mock_cls.call_args.kwargs["timeout"], 15)


class MiddlewareUsesFactoryTest(SimpleTestCase):

    @override_settings(DEBUG=True, OPENAI_API_KEY="test-key", OPENAI_TIMEOUT=7)
    def test_middleware_uses_factory(self):
        with patch("explain_errors.middleware.get_openai_client") as mock_factory:
            ExplainErrorsMiddleware(lambda r: None)

        mock_factory.assert_called_once_with(timeout=7)


class IndexerUsesFactoryTest(SimpleTestCase):

    @override_settings(
        OPENAI_API_KEY="test-key",
        OPENAI_BASE_URL="http://localhost:11434/v1",
    )
    def test_indexer_uses_factory(self):
        from explain_errors.rag.indexer import get_openai_client as indexer_get_client

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("OPENAI_BASE_URL", None)
            with patch("openai.OpenAI") as mock_cls:
                indexer_get_client()

        kwargs = mock_cls.call_args.kwargs
        self.assertEqual(kwargs["api_key"], "test-key")
        self.assertEqual(kwargs["base_url"], "http://localhost:11434/v1")
