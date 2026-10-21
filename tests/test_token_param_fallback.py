from unittest.mock import MagicMock, patch

import openai
from django.test import AsyncClient, Client, SimpleTestCase, override_settings


def _bad_request(param, code):
    body = {
        "message": "Unsupported parameter",
        "type": "invalid_request_error",
        "param": param,
        "code": code,
    }
    response = MagicMock(status_code=400)
    return openai.BadRequestError("Unsupported parameter", response=response, body=body)


def _ok_response(text="Mocked explanation.", finish_reason="stop"):
    return MagicMock(
        choices=[
            MagicMock(message=MagicMock(content=text), finish_reason=finish_reason)
        ]
    )


def _rejects_max_tokens(**kwargs):
    if "max_tokens" in kwargs:
        raise _bad_request("max_tokens", "unsupported_parameter")
    return _ok_response()


@override_settings(
    DEBUG=True,
    OPENAI_API_KEY="test-key",
    OPENAI_MAX_TOKENS=321,
    EXPLAIN_ERRORS_DEDUP=False,
    ROOT_URLCONF="tests.urls_debug_page",
    MIDDLEWARE=["explain_errors.middleware.ExplainErrorsMiddleware"],
)
class TokenParamFallbackBase(SimpleTestCase):
    def setUp(self):
        patcher = patch("openai.OpenAI")
        mock_cls = patcher.start()
        self.addCleanup(patcher.stop)
        self.create = MagicMock(return_value=_ok_response())
        mock_cls.return_value.chat.completions.create = self.create

    def sent(self):
        return [call.kwargs for call in self.create.call_args_list]

    def assertTokenParam(self, kwargs, expected):
        other = (
            "max_completion_tokens" if expected == "max_tokens" else "max_tokens"
        )
        self.assertEqual(kwargs[expected], 321)
        self.assertNotIn(other, kwargs)

    def assertEmptyContentHandled(self, mock_print, response):
        firsts = [call.args[0] for call in mock_print.call_args_list if call.args]
        self.assertEqual(
            firsts.count("Failed to get an explanation from the model API:"), 1
        )
        self.assertFalse([f for f in firsts if str(f).startswith("Error explanation")])
        self.assertNotIn('id="explain-errors"', response.content.decode())


class SyncTokenParamFallbackTest(TokenParamFallbackBase):
    def trigger(self):
        client = getattr(self, "_client", None)
        if client is None:
            client = self._client = Client(raise_request_exception=False)
        return client.get("/boom/")

    def test_default_sends_max_tokens_only(self):
        self.trigger()
        calls = self.sent()
        self.assertEqual(len(calls), 1)
        self.assertTokenParam(calls[0], "max_tokens")

    def test_rejection_retries_once_with_max_completion_tokens(self):
        self.create.side_effect = _rejects_max_tokens
        with self.assertLogs("explain_errors.middleware", level="INFO") as logs:
            response = self.trigger()
        calls = self.sent()
        self.assertEqual(len(calls), 2)
        self.assertTokenParam(calls[0], "max_tokens")
        self.assertTokenParam(calls[1], "max_completion_tokens")
        self.assertIn("Mocked explanation.", response.content.decode())
        self.assertEqual(len(logs.records), 1)
        self.assertIn("max_tokens", logs.records[0].getMessage())
        self.assertIn("max_completion_tokens", logs.records[0].getMessage())

    def test_next_call_uses_max_completion_tokens_directly(self):
        self.create.side_effect = _rejects_max_tokens
        self.trigger()
        self.create.reset_mock()
        self.trigger()
        calls = self.sent()
        self.assertEqual(len(calls), 1)
        self.assertTokenParam(calls[0], "max_completion_tokens")

    def test_other_param_does_not_retry(self):
        self.create.side_effect = _bad_request("temperature", "unsupported_parameter")
        self.trigger()
        self.assertEqual(len(self.sent()), 1)

    def test_other_code_does_not_retry(self):
        self.create.side_effect = _bad_request("max_tokens", "invalid_value")
        self.trigger()
        self.assertEqual(len(self.sent()), 1)

    def test_failed_retry_does_not_loop_and_uses_failure_handling(self):
        self.create.side_effect = [
            _bad_request("max_tokens", "unsupported_parameter"),
            RuntimeError("api down"),
        ]
        with patch("builtins.print") as mock_print:
            self.trigger()
        self.assertEqual(len(self.sent()), 2)
        self.assertIn(
            "Failed to get an explanation from the model API:",
            [call.args[0] for call in mock_print.call_args_list],
        )
        # The switch was not remembered after a failed retry.
        self.create.reset_mock()
        self.create.side_effect = None
        self.create.return_value = _ok_response()
        self.trigger()
        self.assertTokenParam(self.sent()[0], "max_tokens")

    def test_length_warning_hints_at_reasoning_tokens_after_fallback(self):
        self.create.side_effect = _rejects_max_tokens
        self.trigger()
        self.create.side_effect = None
        self.create.return_value = _ok_response(finish_reason="length")
        with self.assertLogs("explain_errors.middleware", level="WARNING") as logs:
            self.trigger()
        self.assertEqual(
            logs.records[0].getMessage(),
            "explain_errors: explanation truncated by OPENAI_MAX_TOKENS=321 "
            "(reasoning tokens count against this limit; raise it if "
            "explanations come back short or empty)",
        )

    def test_length_warning_unchanged_without_fallback(self):
        self.create.return_value = _ok_response(finish_reason="length")
        with self.assertLogs("explain_errors.middleware", level="WARNING") as logs:
            self.trigger()
        self.assertEqual(
            logs.records[0].getMessage(),
            "explain_errors: explanation truncated by OPENAI_MAX_TOKENS=321",
        )

    def test_empty_content_uses_failure_message_and_no_banner(self):
        for content in (None, "", "  \n"):
            with self.subTest(content=content):
                self.create.return_value = _ok_response(content)
                with patch("builtins.print") as mock_print:
                    response = self.trigger()
                self.assertEmptyContentHandled(mock_print, response)


class AsyncTokenParamFallbackTest(TokenParamFallbackBase):
    async def trigger(self):
        client = getattr(self, "_client", None)
        if client is None:
            client = self._client = AsyncClient(raise_request_exception=False)
        return await client.get("/boom-async/")

    async def test_default_sends_max_tokens_only(self):
        await self.trigger()
        calls = self.sent()
        self.assertEqual(len(calls), 1)
        self.assertTokenParam(calls[0], "max_tokens")

    async def test_rejection_retries_once_with_max_completion_tokens(self):
        self.create.side_effect = _rejects_max_tokens
        with self.assertLogs("explain_errors.middleware", level="INFO") as logs:
            response = await self.trigger()
        calls = self.sent()
        self.assertEqual(len(calls), 2)
        self.assertTokenParam(calls[0], "max_tokens")
        self.assertTokenParam(calls[1], "max_completion_tokens")
        self.assertIn("Mocked explanation.", response.content.decode())
        self.assertEqual(len(logs.records), 1)

    async def test_next_call_uses_max_completion_tokens_directly(self):
        self.create.side_effect = _rejects_max_tokens
        await self.trigger()
        self.create.reset_mock()
        await self.trigger()
        calls = self.sent()
        self.assertEqual(len(calls), 1)
        self.assertTokenParam(calls[0], "max_completion_tokens")

    async def test_other_param_does_not_retry(self):
        self.create.side_effect = _bad_request("temperature", "unsupported_parameter")
        await self.trigger()
        self.assertEqual(len(self.sent()), 1)

    async def test_other_code_does_not_retry(self):
        self.create.side_effect = _bad_request("max_tokens", "invalid_value")
        await self.trigger()
        self.assertEqual(len(self.sent()), 1)

    async def test_failed_retry_does_not_loop_and_uses_failure_handling(self):
        self.create.side_effect = [
            _bad_request("max_tokens", "unsupported_parameter"),
            RuntimeError("api down"),
        ]
        with patch("builtins.print") as mock_print:
            await self.trigger()
        self.assertEqual(len(self.sent()), 2)
        self.assertIn(
            "Failed to get an explanation from the model API:",
            [call.args[0] for call in mock_print.call_args_list],
        )
        # The switch was not remembered after a failed retry.
        self.create.reset_mock()
        self.create.side_effect = None
        self.create.return_value = _ok_response()
        await self.trigger()
        self.assertTokenParam(self.sent()[0], "max_tokens")

    async def test_length_warning_hints_at_reasoning_tokens_after_fallback(self):
        self.create.side_effect = _rejects_max_tokens
        await self.trigger()
        self.create.side_effect = None
        self.create.return_value = _ok_response(finish_reason="length")
        with self.assertLogs("explain_errors.middleware", level="WARNING") as logs:
            await self.trigger()
        self.assertIn("reasoning tokens count", logs.records[0].getMessage())

    async def test_length_warning_unchanged_without_fallback(self):
        self.create.return_value = _ok_response(finish_reason="length")
        with self.assertLogs("explain_errors.middleware", level="WARNING") as logs:
            await self.trigger()
        self.assertEqual(
            logs.records[0].getMessage(),
            "explain_errors: explanation truncated by OPENAI_MAX_TOKENS=321",
        )

    async def test_empty_content_uses_failure_message_and_no_banner(self):
        for content in (None, "", "  \n"):
            with self.subTest(content=content):
                self.create.return_value = _ok_response(content)
                with patch("builtins.print") as mock_print:
                    response = await self.trigger()
                self.assertEmptyContentHandled(mock_print, response)
