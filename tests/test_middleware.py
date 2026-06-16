import json
from unittest.mock import MagicMock, patch

from django.http import JsonResponse, HttpResponse
from django.test import SimpleTestCase, RequestFactory, AsyncRequestFactory, override_settings

from explain_errors.middleware import ExplainErrorsMiddleware


def _mock_openai():
    """Return a patch context for OpenAI plus a configured fake client."""
    patcher = patch("explain_errors.middleware.OpenAI")
    mock_cls = patcher.start()
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="Mocked explanation."))]
    )
    mock_cls.return_value = client
    return patcher, client


@override_settings(DEBUG=True, OPENAI_API_KEY="test-key")
class ExplainErrorsMiddlewareTest(SimpleTestCase):

    def setUp(self):
        self.factory = RequestFactory()
        self.patcher, self.client = _mock_openai()
        self.addCleanup(self.patcher.stop)

    def _build(self, get_response):
        return ExplainErrorsMiddleware(get_response)

    # ---- capability markers ----
    def test_capability_markers(self):
        self.assertTrue(ExplainErrorsMiddleware.async_capable)
        self.assertTrue(ExplainErrorsMiddleware.sync_capable)

    def test_is_async_detection(self):
        sync_mw = self._build(lambda r: HttpResponse("ok"))
        self.assertFalse(sync_mw._is_async)

        async def aget(r):
            return HttpResponse("ok")

        self.assertTrue(self._build(aget)._is_async)

    # ---- process_exception (original test, adapted) ----
    def test_process_exception_with_error(self):
        request = self.factory.get("/")
        middleware = self._build(lambda request: None)

        response = middleware.process_exception(request, Exception("Test Exception"))

        self.assertIsInstance(response, JsonResponse)
        self.assertEqual(response.status_code, 500)
        self.assertIn("error", json.loads(response.content))

    def test_api_called_flag_prevents_second_call(self):
        request = self.factory.get("/")
        middleware = self._build(lambda request: None)

        middleware.process_exception(request, Exception("Test"))
        self.assertTrue(middleware.api_called)

        resp = middleware.process_exception(request, Exception("Test"))
        self.assertEqual(self.client.chat.completions.create.call_count, 1)
        self.assertEqual(resp.status_code, 500)

    # ---- sync request path ----
    def test_sync_passthrough(self):
        sentinel = HttpResponse("ok")
        mw = self._build(lambda r: sentinel)
        self.assertIs(mw(self.factory.get("/")), sentinel)

    def test_sync_exception_returns_500(self):
        def boom(r):
            raise ValueError("boom")

        resp = self._build(boom)(self.factory.get("/"))
        self.assertIsInstance(resp, JsonResponse)
        self.assertEqual(resp.status_code, 500)
        self.assertIn("error", json.loads(resp.content))


@override_settings(DEBUG=True, OPENAI_API_KEY="test-key")
class ExplainErrorsMiddlewareAsyncTest(SimpleTestCase):

    def setUp(self):
        self.factory = AsyncRequestFactory()
        self.patcher, self.client = _mock_openai()
        self.addCleanup(self.patcher.stop)

    async def test_async_passthrough(self):
        sentinel = HttpResponse("ok")

        async def aget(r):
            return sentinel

        mw = ExplainErrorsMiddleware(aget)
        self.assertIs(await mw(self.factory.get("/")), sentinel)

    async def test_async_exception_returns_500(self):
        async def boom(r):
            raise ValueError("async boom")

        mw = ExplainErrorsMiddleware(boom)
        resp = await mw(self.factory.get("/"))
        self.assertIsInstance(resp, JsonResponse)
        self.assertEqual(resp.status_code, 500)
        self.assertIn("error", json.loads(resp.content))


@override_settings(DEBUG=False)
class ExplainErrorsMiddlewareDebugOffTest(SimpleTestCase):

    def test_process_exception_returns_none_when_debug_off(self):
        factory = RequestFactory()
        mw = ExplainErrorsMiddleware(lambda r: None)
        self.assertIsNone(mw.process_exception(factory.get("/"), Exception("x")))


@override_settings(DEBUG=True, OPENAI_API_KEY="test-key")
class OpenAICallConfigTest(SimpleTestCase):

    def setUp(self):
        self.factory = RequestFactory()
        self.patcher, self.client = _mock_openai()
        self.addCleanup(self.patcher.stop)

    def test_default_model(self):
        mw = ExplainErrorsMiddleware(lambda r: None)
        mw.process_exception(self.factory.get("/"), Exception("x"))
        kwargs = self.client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-4o-mini")
        self.assertEqual(kwargs["max_tokens"], 150)

    @override_settings(OPENAI_MODEL="gpt-5-mini", OPENAI_MAX_TOKENS=50)
    def test_configurable_model_and_tokens(self):
        mw = ExplainErrorsMiddleware(lambda r: None)
        mw.process_exception(self.factory.get("/"), Exception("x"))
        kwargs = self.client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-5-mini")
        self.assertEqual(kwargs["max_tokens"], 50)

    @override_settings(OPENAI_MAX_TRACEBACK_CHARS=200)
    def test_traceback_is_truncated(self):
        mw = ExplainErrorsMiddleware(lambda r: None)
        try:
            raise ValueError("x" * 5000)
        except ValueError as exc:
            mw.process_exception(self.factory.get("/"), exc)
        prompt = self.client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        self.assertIn("(truncated)", prompt)
        self.assertLess(len(prompt), 400)

    def test_timeout_passed_to_client(self):
        with override_settings(OPENAI_TIMEOUT=7):
            with patch("explain_errors.middleware.OpenAI") as mock_cls:
                ExplainErrorsMiddleware(lambda r: None)
                self.assertEqual(mock_cls.call_args.kwargs["timeout"], 7)
