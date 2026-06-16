import os
import asyncio
import traceback

from openai import OpenAI
from dotenv import load_dotenv
from django.conf import settings
from django.http import JsonResponse
from asgiref.sync import sync_to_async


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
        self.api_called = False
        self.openai_client = None

        if settings.DEBUG:
            # Load environment variables from .env file
            load_dotenv()
            # Get the OpenAI API key from environment variable (or settings)
            openai_api_key = os.getenv(
                "OPENAI_API_KEY", getattr(settings, "OPENAI_API_KEY", None)
            )
            if not openai_api_key:
                raise ValueError(
                    "OpenAI API key not found. Please set the OPENAI_API_KEY "
                    "environment variable."
                )

            # Configurable via settings, with sensible defaults.
            self.model = getattr(settings, "OPENAI_MODEL", "gpt-4o-mini")
            self.max_tokens = getattr(settings, "OPENAI_MAX_TOKENS", 150)
            timeout = getattr(settings, "OPENAI_TIMEOUT", 10)

            self.openai_client = OpenAI(api_key=openai_api_key, timeout=timeout)

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
            return self.process_exception(request, exception)
        return response

    # --------- Async path ----------
    async def __acall__(self, request):
        try:
            response = await self.get_response(request)
        except Exception as exception:
            # process_exception performs blocking OpenAI I/O, so run it in a
            # thread to keep the event loop free.
            return await sync_to_async(self.process_exception)(request, exception)
        return response

    def process_exception(self, request, exception):
        if not settings.DEBUG:
            return None

        explanation = None
        if not self.api_called:
            # Get the exception traceback, trimmed to the most recent frames to
            # cap token usage and stay within the model's context window.
            tb = traceback.format_exc()
            max_tb_chars = getattr(settings, "OPENAI_MAX_TRACEBACK_CHARS", 3000)
            if len(tb) > max_tb_chars:
                tb = "...(truncated)...\n" + tb[-max_tb_chars:]
            # Construct the prompt
            prompt = f"Explain the following Django error in simple terms:\n\n{tb}"

            try:
                # Call OpenAI API
                response = self.openai_client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=self.max_tokens,
                )
                explanation = response.choices[0].message.content

                # Print the explanation to stdout
                print("Error Explanation by OpenAI:\n", explanation)
                self.api_called = True  # Set flag after the call
            except Exception as e:
                # If the OpenAI call fails, surface the failure but still return
                # a 500 so the request lifecycle completes cleanly.
                print("Failed to get an explanation from OpenAI:", e)

        return JsonResponse(
            {"error": "An error occurred.", "message": explanation}, status=500
        )
