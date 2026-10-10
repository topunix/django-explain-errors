from unittest.mock import MagicMock, patch

from asgiref.sync import iscoroutinefunction
from django.http import HttpResponse
from django.test import AsyncClient, Client, SimpleTestCase, override_settings

from explain_errors.middleware import ExplainErrorsMiddleware


def _mock_openai():
    patcher = patch("openai.OpenAI")
    mock_cls = patcher.start()
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="Mocked explanation."))]
    )
    mock_cls.return_value = client
    return patcher


@override_settings(
    DEBUG=True,
    OPENAI_API_KEY="test-key",
    ROOT_URLCONF="tests.urls_debug_page",
    MIDDLEWARE=[
        "django.middleware.common.CommonMiddleware",
        "explain_errors.middleware.ExplainErrorsMiddleware",
    ],
)
class AsyncCoroutineMarkingRequestCycleTest(SimpleTestCase):
    """Through the real handler chain, with an async-capable middleware
    above ExplainErrorsMiddleware, as in a default project."""

    def setUp(self):
        patcher = _mock_openai()
        self.addCleanup(patcher.stop)

    def _assert_banner_500(self, response):
        self.assertEqual(response.status_code, 500)
        content = response.content.decode()
        self.assertIn('id="explain-errors"', content)
        self.assertIn("Mocked explanation.", content)

    async def test_async_client_sync_view(self):
        response = await AsyncClient(raise_request_exception=False).get("/boom/")
        self._assert_banner_500(response)

    async def test_async_client_async_view(self):
        response = await AsyncClient(raise_request_exception=False).get("/boom-async/")
        self._assert_banner_500(response)

    async def test_async_client_async_view_no_exception(self):
        response = await AsyncClient().get("/ok-async/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")

    def test_sync_client_sync_view(self):
        response = Client(raise_request_exception=False).get("/boom/")
        self._assert_banner_500(response)

    def test_sync_client_async_view(self):
        response = Client(raise_request_exception=False).get("/boom-async/")
        self._assert_banner_500(response)


@override_settings(DEBUG=True, OPENAI_API_KEY="test-key")
class AsyncCoroutineMarkingInstanceTest(SimpleTestCase):
    def setUp(self):
        patcher = _mock_openai()
        self.addCleanup(patcher.stop)

    def test_marked_as_coroutine_function_when_get_response_is_async(self):
        async def get_response(request):
            return HttpResponse("ok")

        self.assertTrue(iscoroutinefunction(ExplainErrorsMiddleware(get_response)))

    def test_not_marked_when_get_response_is_sync(self):
        def get_response(request):
            return HttpResponse("ok")

        self.assertFalse(iscoroutinefunction(ExplainErrorsMiddleware(get_response)))
