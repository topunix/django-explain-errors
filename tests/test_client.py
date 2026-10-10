import os
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from explain_errors.client import (
    PLACEHOLDER_API_KEY,
    EmbeddingsNotSupportedError,
    create_embeddings,
    get_embeddings_client,
    get_openai_client,
)
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
            "API key not found. Set the OPENAI_API_KEY environment "
            "variable or setting, or set OPENAI_BASE_URL for a local "
            "server that needs no key.",
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
    def test_indexer_uses_embeddings_factory(self):
        from explain_errors.client import get_embeddings_client as indexer_get_client

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("OPENAI_BASE_URL", None)
            with patch("openai.OpenAI") as mock_cls:
                indexer_get_client()

        kwargs = mock_cls.call_args.kwargs
        self.assertEqual(kwargs["api_key"], "test-key")
        self.assertEqual(kwargs["base_url"], "http://localhost:11434/v1")


class GetEmbeddingsClientTest(SimpleTestCase):

    def _build(self, **env_overrides):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("OPENAI_BASE_URL", None)
            with patch("openai.OpenAI") as mock_cls:
                get_embeddings_client()
        return mock_cls

    @override_settings(OPENAI_API_KEY="chat-key", OPENAI_BASE_URL="http://chat/v1")
    def test_falls_back_to_chat_settings_when_embed_settings_unset(self):
        kwargs = self._build().call_args.kwargs
        self.assertEqual(kwargs["api_key"], "chat-key")
        self.assertEqual(kwargs["base_url"], "http://chat/v1")

    @override_settings(OPENAI_API_KEY="chat-key")
    def test_openai_only_config_has_no_base_url(self):
        kwargs = self._build().call_args.kwargs
        self.assertEqual(kwargs["api_key"], "chat-key")
        self.assertNotIn("base_url", kwargs)

    @override_settings(
        OPENAI_API_KEY="chat-key",
        OPENAI_BASE_URL="https://api.anthropic.com/v1/",
        EXPLAIN_ERRORS_RAG_EMBED_API_KEY="embed-key",
        EXPLAIN_ERRORS_RAG_EMBED_BASE_URL="https://embed.example/v1",
    )
    def test_embed_settings_build_a_separate_client(self):
        kwargs = self._build().call_args.kwargs
        self.assertEqual(kwargs["api_key"], "embed-key")
        self.assertEqual(kwargs["base_url"], "https://embed.example/v1")

    @override_settings(
        OPENAI_API_KEY="chat-key",
        OPENAI_BASE_URL="https://api.anthropic.com/v1/",
        EXPLAIN_ERRORS_RAG_EMBED_BASE_URL="https://embed.example/v1",
    )
    def test_different_embed_base_url_without_key_raises(self):
        with self.assertRaises(ImproperlyConfigured) as ctx:
            self._build()
        self.assertIn("EXPLAIN_ERRORS_RAG_EMBED_API_KEY", str(ctx.exception))

    @override_settings(
        OPENAI_API_KEY="chat-key",
        OPENAI_BASE_URL="https://embed.example/v1",
        EXPLAIN_ERRORS_RAG_EMBED_BASE_URL="https://embed.example/v1/",
    )
    def test_matching_embed_base_url_keeps_chat_key(self):
        kwargs = self._build().call_args.kwargs
        self.assertEqual(kwargs["api_key"], "chat-key")
        self.assertEqual(kwargs["base_url"], "https://embed.example/v1/")

    @override_settings(OPENAI_API_KEY="chat-key")
    def test_embed_settings_from_env_vars(self):
        env = {
            "EXPLAIN_ERRORS_RAG_EMBED_BASE_URL": "https://env-embed.example/v1",
            "EXPLAIN_ERRORS_RAG_EMBED_API_KEY": "env-embed-key",
        }
        with patch.dict(os.environ, env):
            kwargs = self._build().call_args.kwargs
        self.assertEqual(kwargs["api_key"], "env-embed-key")
        self.assertEqual(kwargs["base_url"], "https://env-embed.example/v1")

    @override_settings(
        OPENAI_API_KEY="chat-key",
        EXPLAIN_ERRORS_RAG_EMBED_BASE_URL="https://settings-embed.example/v1",
        EXPLAIN_ERRORS_RAG_EMBED_API_KEY="settings-embed-key",
    )
    def test_embed_env_vars_win_over_settings(self):
        env = {
            "EXPLAIN_ERRORS_RAG_EMBED_BASE_URL": "https://env-embed.example/v1",
            "EXPLAIN_ERRORS_RAG_EMBED_API_KEY": "env-embed-key",
        }
        with patch.dict(os.environ, env):
            kwargs = self._build().call_args.kwargs
        self.assertEqual(kwargs["api_key"], "env-embed-key")
        self.assertEqual(kwargs["base_url"], "https://env-embed.example/v1")

    @override_settings(OPENAI_API_KEY="chat-key")
    def test_env_embed_base_url_without_key_raises(self):
        with patch.dict(
            os.environ, {"EXPLAIN_ERRORS_RAG_EMBED_BASE_URL": "https://e.example/v1"}
        ):
            with self.assertRaises(ImproperlyConfigured):
                self._build()

    @override_settings(OPENAI_API_KEY="chat-key")
    def test_embeddings_client_does_not_reuse_chat_client(self):
        with patch("explain_errors.client.get_openai_client") as chat_factory:
            self._build()
        chat_factory.assert_not_called()


def _client_answering(status, body, base_url):
    import httpx
    from openai import OpenAI

    transport = httpx.MockTransport(lambda request: httpx.Response(status, json=body))
    return OpenAI(
        api_key="k",
        base_url=base_url,
        http_client=httpx.Client(transport=transport),
        max_retries=0,
    )


class AnthropicEmbeddingsErrorTest(SimpleTestCase):
    NOT_FOUND = {"error": {"message": "Not found", "type": "not_found_error"}}

    @override_settings(OPENAI_BASE_URL="https://api.anthropic.com/v1/")
    def test_404_on_anthropic_base_url_raises_friendly_error(self):
        client = _client_answering(404, self.NOT_FOUND, "https://api.anthropic.com/v1/")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_BASE_URL", None)
            with self.assertRaises(EmbeddingsNotSupportedError) as ctx:
                create_embeddings(client, "text-embedding-3-small", ["x"])
        message = str(ctx.exception)
        self.assertIn("Anthropic's OpenAI-compatible API has no embeddings endpoint", message)
        self.assertIn("EXPLAIN_ERRORS_RAG_EMBED_BASE_URL", message)
        self.assertIn("EXPLAIN_ERRORS_RAG_EMBED_API_KEY", message)
        self.assertIn("EXPLAIN_ERRORS_RAG_EMBED_MODEL", message)

    @override_settings(OPENAI_BASE_URL="https://api.openai.com/v1/")
    def test_404_on_other_base_url_surfaces_sdk_error(self):
        from openai import NotFoundError

        client = _client_answering(404, self.NOT_FOUND, "https://api.openai.com/v1/")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_BASE_URL", None)
            with self.assertRaises(NotFoundError):
                create_embeddings(client, "m", ["x"])

    @override_settings(
        OPENAI_BASE_URL="https://api.anthropic.com/v1/",
        EXPLAIN_ERRORS_RAG_EMBED_BASE_URL="https://embed.example/v1",
    )
    def test_embed_base_url_override_avoids_anthropic_message(self):
        from openai import NotFoundError

        client = _client_answering(404, self.NOT_FOUND, "https://embed.example/v1")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_BASE_URL", None)
            with self.assertRaises(NotFoundError):
                create_embeddings(client, "m", ["x"])


    @override_settings(OPENAI_BASE_URL="https://anthropic.com.evil.example/v1/")
    def test_anthropic_detection_uses_hostname_not_substring(self):
        from openai import NotFoundError

        client = _client_answering(404, self.NOT_FOUND, "https://anthropic.com.evil.example/v1/")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_BASE_URL", None)
            with self.assertRaises(NotFoundError):
                create_embeddings(client, "m", ["x"])

    @override_settings(OPENAI_BASE_URL="https://gateway.anthropic.com/v1/")
    def test_anthropic_subdomain_is_detected(self):
        client = _client_answering(404, self.NOT_FOUND, "https://gateway.anthropic.com/v1/")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_BASE_URL", None)
            with self.assertRaises(EmbeddingsNotSupportedError):
                create_embeddings(client, "m", ["x"])
