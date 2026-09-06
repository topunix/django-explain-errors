import os

from django.conf import settings
from dotenv import load_dotenv, find_dotenv

PLACEHOLDER_API_KEY = "sk-no-key-required"


def get_openai_client(timeout=None):
    """Build the OpenAI client used by the middleware and the
    RAG indexer. Honors OPENAI_BASE_URL for OpenAI-compatible
    servers (Ollama, LM Studio, gateways)."""
    load_dotenv(find_dotenv(usecwd=True))
    from openai import OpenAI

    base_url = os.getenv(
        "OPENAI_BASE_URL", getattr(settings, "OPENAI_BASE_URL", None)
    )
    api_key = os.getenv(
        "OPENAI_API_KEY", getattr(settings, "OPENAI_API_KEY", None)
    )
    if not api_key:
        if base_url:
            # Local OpenAI-compatible servers ignore the key but
            # the SDK requires one.
            api_key = PLACEHOLDER_API_KEY
        else:
            raise ValueError(
                "OpenAI API key not found. Please set the "
                "OPENAI_API_KEY environment variable."
            )
    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    if timeout is not None:
        kwargs["timeout"] = timeout
    return OpenAI(**kwargs)
