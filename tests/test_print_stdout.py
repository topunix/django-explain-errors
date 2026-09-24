from unittest.mock import MagicMock, patch

from django.test import AsyncClient, Client, RequestFactory, SimpleTestCase, override_settings

from explain_errors.middleware import ExplainErrorsMiddleware


def _mock_openai():
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
    ROOT_URLCONF="tests.urls_debug_page",
    MIDDLEWARE=["explain_errors.middleware.ExplainErrorsMiddleware"],
)
class PrintStdoutRequestCycleTest(SimpleTestCase):
    def setUp(self):
        self.patcher, self.mock_client = _mock_openai()
        self.addCleanup(self.patcher.stop)

    def test_default_prints_to_stdout(self):
        client = Client(raise_request_exception=False)
        with patch("builtins.print") as mock_print:
            response = client.get("/boom/")

        self.assertEqual(response.status_code, 500)
        mock_print.assert_any_call("Error Explanation by OpenAI:\n", "Mocked explanation.")

    @override_settings(EXPLAIN_ERRORS_PRINT_STDOUT=False)
    def test_print_stdout_false_not_printed_banner_still_present(self):
        client = Client(raise_request_exception=False)
        with patch("builtins.print") as mock_print:
            response = client.get("/boom/")

        self.assertEqual(response.status_code, 500)
        for call in mock_print.call_args_list:
            self.assertNotEqual(call.args[0], "Error Explanation by OpenAI:\n")
        self.assertIn('id="explain-errors"', response.content.decode())

    @override_settings(EXPLAIN_ERRORS_PRINT_STDOUT=False)
    def test_print_stdout_false_openai_failure_still_prints(self):
        self.mock_client.chat.completions.create.side_effect = RuntimeError("api down")
        client = Client(raise_request_exception=False)
        with patch("builtins.print") as mock_print:
            response = client.get("/boom/")

        self.assertEqual(response.status_code, 500)
        printed_prefixes = [call.args[0] for call in mock_print.call_args_list]
        self.assertIn("Failed to get an explanation from OpenAI:", printed_prefixes)

    @override_settings(EXPLAIN_ERRORS_PRINT_STDOUT=False, EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE=False)
    def test_print_stdout_false_json_500_still_has_explanation(self):
        client = Client(raise_request_exception=False)
        response = client.get("/boom/")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn("Mocked explanation.", response.json()["message"])

    @override_settings(EXPLAIN_ERRORS_PRINT_STDOUT=False)
    async def test_async_view_print_stdout_false_not_printed_banner_present(self):
        client = AsyncClient(raise_request_exception=False)
        with patch("builtins.print") as mock_print:
            response = await client.get("/boom-async/")

        self.assertEqual(response.status_code, 500)
        for call in mock_print.call_args_list:
            self.assertNotEqual(call.args[0], "Error Explanation by OpenAI:\n")
        self.assertIn('id="explain-errors"', response.content.decode())


@override_settings(DEBUG=True, OPENAI_API_KEY="test-key")
class StartupWarningTest(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.patcher, self.client = _mock_openai()
        self.addCleanup(self.patcher.stop)

    @override_settings(
        EXPLAIN_ERRORS_PRINT_STDOUT=False,
        EXPLAIN_ERRORS_INJECT_DEBUG_PAGE=False,
        EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE=True,
    )
    def test_warning_logged_when_no_channel_shows_explanation(self):
        with self.assertLogs("explain_errors", level="WARNING") as cm:
            ExplainErrorsMiddleware(lambda r: None)

        self.assertTrue(
            any("will be generated but not shown anywhere" in message for message in cm.output)
        )

    @override_settings(
        EXPLAIN_ERRORS_PRINT_STDOUT=False,
        EXPLAIN_ERRORS_INJECT_DEBUG_PAGE=True,
        EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE=True,
    )
    def test_no_warning_when_inject_debug_page_on(self):
        with patch("explain_errors.middleware.logger.warning") as mock_warning:
            ExplainErrorsMiddleware(lambda r: None)

        mock_warning.assert_not_called()

    @override_settings(
        EXPLAIN_ERRORS_PRINT_STDOUT=False,
        EXPLAIN_ERRORS_INJECT_DEBUG_PAGE=False,
        EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE=False,
    )
    def test_no_warning_when_preserve_debug_page_off(self):
        with patch("explain_errors.middleware.logger.warning") as mock_warning:
            ExplainErrorsMiddleware(lambda r: None)

        mock_warning.assert_not_called()
