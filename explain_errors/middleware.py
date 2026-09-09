import asyncio
import logging
import traceback

from django.conf import settings
from django.http import JsonResponse
from asgiref.sync import sync_to_async

from .client import get_openai_client
from .sanitize import sanitize_traceback
from .throttle import SlidingWindowThrottle
from .rag.retriever import format_chunks_for_prompt, retrieve_chunks

logger = logging.getLogger(__name__)

EXPLANATION_WORD_BUDGET = 200
# Generous ceiling, not a target. Billing follows tokens generated, so unused
# headroom is free. The prompt controls length; this only stops runaway
# generation, which matters most for local models behind OPENAI_BASE_URL.
DEFAULT_MAX_TOKENS = 1000

SYSTEM_PROMPT = (
    "You are a Django expert helping a developer understand an error. "
    f"Answer in under {EXPLANATION_WORD_BUDGET} words: what went wrong, "
    "why, and how to fix it. Be concrete and skip preamble."
)


class ExplainErrorsMiddleware:
    """
    Captures unhandled exceptions, asks OpenAI to explain them, and prints the
    explanation to stdout when DEBUG is True. Supports both sync (WSGI) and
    async (ASGI) views.
    """

    async_capable = True
    sync_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        self._is_async = asyncio.iscoroutinefunction(get_response)
        self.openai_client = None
        self.throttle = None

        if settings.DEBUG:
            max_calls = getattr(settings, "EXPLAIN_ERRORS_MAX_CALLS", 5)
            window_seconds = getattr(settings, "EXPLAIN_ERRORS_WINDOW_SECONDS", 60)
            self.throttle = SlidingWindowThrottle(max_calls, window_seconds)

            # Configurable via settings, with sensible defaults.
            self.model = getattr(settings, "OPENAI_MODEL", "gpt-4o-mini")
            self.max_tokens = getattr(settings, "OPENAI_MAX_TOKENS", DEFAULT_MAX_TOKENS)
            timeout = getattr(settings, "OPENAI_TIMEOUT", 10)

            self.openai_client = get_openai_client(timeout=timeout)

    def __call__(self, request):
        # Delegate to the async path when wrapped around an async view chain.
        if self._is_async:
            return self.__acall__(request)
        return self._sync_handler(request)

    # --------- Sync path ----------
    def _sync_handler(self, request):
        try:
            response = self.get_response(request)
        except Exception as exception:
            response = self.process_exception(request, exception)
            if response is None:
                raise
            return response
        return response

    # --------- Async path ----------
    async def __acall__(self, request):
        try:
            response = await self.get_response(request)
        except Exception as exception:
            # process_exception performs blocking OpenAI I/O, so run it in a
            # thread to keep the event loop free.
            response = await sync_to_async(self.process_exception)(request, exception)
            if response is None:
                raise
            return response
        return response

    def process_exception(self, request, exception):
        if not settings.DEBUG:
            return None

        explanation = None
        if self.throttle.allow():
            # Get the exception traceback, trimmed to the most recent frames to
            # cap token usage and stay within the model's context window.
            tb = traceback.format_exc()
            max_tb_chars = getattr(settings, "OPENAI_MAX_TRACEBACK_CHARS", 3000)
            if len(tb) > max_tb_chars:
                tb = "...(truncated)...\n" + tb[-max_tb_chars:]
            # Sanitize the exact payload that ships, after truncation so we
            # don't waste work redacting frames that get discarded.
            tb = sanitize_traceback(tb)

            # Construct the prompt
            prompt = f"Explain the following Django error in simple terms:\n\n{tb}"

            # RAG: ground the explanation in the user's own project source.
            # Opt-in and must never break the traceback-only path, so any
            # failure here is logged and swallowed.
            if getattr(settings, "EXPLAIN_ERRORS_RAG_ENABLED", False):
                try:
                    chunks = retrieve_chunks(exception)
                    max_prompt_chars = getattr(
                        settings, "EXPLAIN_ERRORS_RAG_MAX_PROMPT_CHARS", 6000
                    )
                    remaining_chars = max(max_prompt_chars - len(tb), 0)
                    rag_section = format_chunks_for_prompt(chunks, remaining_chars)
                    if rag_section:
                        prompt += f"\n\n{rag_section}"
                except Exception as exc:
                    logger.warning(
                        "explain_errors: RAG retrieval failed, falling back to "
                        "traceback-only prompt: %s",
                        exc,
                    )

            try:
                # Call OpenAI API
                response = self.openai_client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=self.max_tokens,
                )
                explanation = response.choices[0].message.content
                if response.choices[0].finish_reason == "length":
                    logger.warning(
                        "explain_errors: explanation truncated by OPENAI_MAX_TOKENS=%s",
                        self.max_tokens,
                    )

                # Print the explanation to stdout
                print("Error Explanation by OpenAI:\n", explanation)
            except Exception as e:
                # If the OpenAI call fails, surface the failure but still return
                # a 500 so the request lifecycle completes cleanly.
                print("Failed to get an explanation from OpenAI:", e)

        if getattr(settings, "EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE", True):
            return None

        return JsonResponse(
            {"error": "An error occurred.", "message": explanation}, status=500
        )
