from unittest.mock import MagicMock, patch

from django.core.signals import got_request_exception
from django.test import Client, SimpleTestCase, override_settings


def _mock_openai():
    """Patch the chat-completion OpenAI client used by the middleware."""
    patcher = patch("openai.OpenAI")
    mock_cls = patcher.start()
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="Mocked explanation."))]
    )
    mock_cls.return_value = client
    return patcher, client


@override_settings(
    DEBUG=True,
    OPENAI_API_KEY="test-key",
    ROOT_URLCONF="tests.urls_signals",
    MIDDLEWARE=["explain_errors.middleware.ExplainErrorsMiddleware"],
)
class GotRequestExceptionSignalTest(SimpleTestCase):
    """Load-bearing for Sentry/Rollbar: they hook got_request_exception, which
    only fires when the exception is left to propagate out of the middleware
    chain (preserve mode), not when we short-circuit it with a JsonResponse.
    """

    def setUp(self):
        self.openai_patcher, self.client = _mock_openai()
        self.addCleanup(self.openai_patcher.stop)

        self.received = []
        got_request_exception.connect(self._receiver)
        self.addCleanup(got_request_exception.disconnect, self._receiver)

    def _receiver(self, sender, request, **kwargs):
        self.received.append(request)

    def test_signal_fires_in_preserve_mode(self):
        client = Client(raise_request_exception=False)
        response = client.get("/boom/")

        self.assertEqual(len(self.received), 1)
        self.assertEqual(response.status_code, 500)

    @override_settings(EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE=False)
    def test_signal_does_not_fire_when_preserve_mode_off(self):
        client = Client(raise_request_exception=False)
        response = client.get("/boom/")

        self.assertEqual(len(self.received), 0)
        self.assertEqual(response.status_code, 500)
