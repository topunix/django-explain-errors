import os

from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv, find_dotenv

PLACEHOLDER_API_KEY = "sk-no-key-required"


def _chat_base_url():
    return os.getenv("OPENAI_BASE_URL", getattr(settings, "OPENAI_BASE_URL", None))


def _chat_api_key():
    return os.getenv("OPENAI_API_KEY", getattr(settings, "OPENAI_API_KEY", None))


def _build_client(base_url, api_key, timeout=None):
    from openai import OpenAI

    if not api_key:
        if base_url:
            # Local OpenAI-compatible servers ignore the key but
            # the SDK requires one.
            api_key = PLACEHOLDER_API_KEY
        else:
            raise ValueError(
                "API key not found. Set the OPENAI_API_KEY environment "
                "variable or setting, or set OPENAI_BASE_URL for a local "
                "server that needs no key."
            )
    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    if timeout is not None:
        kwargs["timeout"] = timeout
    return OpenAI(**kwargs)


def get_openai_client(timeout=None):
    """Build the OpenAI client used by the middleware. Honors
    OPENAI_BASE_URL for OpenAI-compatible servers (Ollama, LM Studio,
    gateways)."""
    load_dotenv(find_dotenv(usecwd=True))
    return _build_client(_chat_base_url(), _chat_api_key(), timeout)


def _embed_setting(name):
    """Same resolution order as the chat settings: env var, then Django
    settings. An empty value counts as unset."""
    return os.getenv(name) or getattr(settings, name, None) or None


def _same_url(a, b):
    return (a or "").rstrip("/") == (b or "").rstrip("/")


def get_embed_base_url():
    """Embeddings base URL: EXPLAIN_ERRORS_RAG_EMBED_BASE_URL, else the chat
    base URL."""
    load_dotenv(find_dotenv(usecwd=True))
    return _embed_setting("EXPLAIN_ERRORS_RAG_EMBED_BASE_URL") or _chat_base_url()


def get_embeddings_client(timeout=None):
    """Build the client used by the RAG indexer and retriever.

    EXPLAIN_ERRORS_RAG_EMBED_BASE_URL and EXPLAIN_ERRORS_RAG_EMBED_API_KEY
    (env var, then Django settings) fall back to the chat settings when
    unset. The chat API key is only reused when the embeddings base URL is
    unset or equals the chat base URL. If the embeddings base URL points
    elsewhere, EXPLAIN_ERRORS_RAG_EMBED_API_KEY is required, so the chat key
    is never sent to a different provider.
    """
    load_dotenv(find_dotenv(usecwd=True))
    embed_url = _embed_setting("EXPLAIN_ERRORS_RAG_EMBED_BASE_URL")
    embed_key = _embed_setting("EXPLAIN_ERRORS_RAG_EMBED_API_KEY")
    if embed_url and not _same_url(embed_url, _chat_base_url()) and not embed_key:
        raise ImproperlyConfigured(
            "EXPLAIN_ERRORS_RAG_EMBED_BASE_URL differs from the chat base "
            "URL, so EXPLAIN_ERRORS_RAG_EMBED_API_KEY is required. The chat "
            "API key is not sent to a different provider."
        )
    return _build_client(
        embed_url or _chat_base_url(), embed_key or _chat_api_key(), timeout
    )


class EmbeddingsNotSupportedError(Exception):
    """The configured embeddings endpoint does not exist."""


def create_embeddings(client, model, texts):
    """client.embeddings.create(...) with a friendly error when an Anthropic
    base URL answers 404 / "not found", instead of the raw SDK error."""
    from openai import APIStatusError

    try:
        return client.embeddings.create(model=model, input=texts)
    except APIStatusError as exc:
        base_url = get_embed_base_url() or ""
        hostname = (urlparse(base_url).hostname or "").lower()
        is_anthropic = hostname == "api.anthropic.com" or hostname.endswith(".anthropic.com")
        not_found = exc.status_code == 404 or "not found" in str(exc).lower()
        if not_found and is_anthropic:
            raise EmbeddingsNotSupportedError(
                f"The embeddings endpoint at {base_url} was not found: "
                "Anthropic's OpenAI-compatible API has no embeddings "
                "endpoint. Set "
                "EXPLAIN_ERRORS_RAG_EMBED_BASE_URL, "
                "EXPLAIN_ERRORS_RAG_EMBED_API_KEY and "
                "EXPLAIN_ERRORS_RAG_EMBED_MODEL to an OpenAI-compatible "
                "embeddings provider. For local embeddings see the "
                "local-embeddings item in docs/roadmap.md."
            ) from exc
        raise
