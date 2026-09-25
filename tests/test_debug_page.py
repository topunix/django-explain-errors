import datetime
from unittest.mock import MagicMock, patch

from django.test import (
    AsyncClient,
    Client,
    RequestFactory,
    SimpleTestCase,
    override_settings,
)
from django.views.debug import ExceptionReporter

from explain_errors.debug_page import (
    ExplainErrorsExceptionReporter,
    render_explanation_html,
)


class _RaisingBool:
    """A settings value that raises when evaluated for truthiness, to
    simulate an internal failure inside debug page injection setup."""

    def __bool__(self):
        raise RuntimeError("simulated settings access failure")


def _mock_openai():
    patcher = patch("openai.OpenAI")
    mock_cls = patcher.start()
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="Mocked explanation."))]
    )
    mock_cls.return_value = client
    return patcher, client


def _exc_info():
    import sys

    try:
        raise ValueError("boom")
    except ValueError:
        return sys.exc_info()


def _build_reporter(reporter_class, request, exc_info, explanation=None):
    if explanation is not None:
        request._explain_errors_explanation = explanation
    exc_type, exc_value, tb = exc_info
    return reporter_class(request, exc_type, exc_value, tb)


@override_settings(ROOT_URLCONF="tests.urls_debug_page")
class ExplainErrorsExceptionReporterTest(SimpleTestCase):
    """Unit tests against the reporter directly, no request cycle."""

    def setUp(self):
        self.factory = RequestFactory()
        patcher = patch(
            "django.views.debug.timezone.now",
            return_value=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_banner_inserted_between_exception_value_and_meta_table(self):
        request = self.factory.get("/")
        reporter = _build_reporter(
            ExplainErrorsExceptionReporter, request, _exc_info(), explanation="Explained."
        )

        html = reporter.get_traceback_html()

        value_index = html.index('class="exception_value"')
        banner_index = html.index('id="explain-errors"')
        meta_index = html.index('table class="meta"')
        self.assertTrue(value_index < banner_index < meta_index)
        self.assertIn("Explained.", html)

    def test_explanation_with_script_tag_is_escaped(self):
        request = self.factory.get("/")
        reporter = _build_reporter(
            ExplainErrorsExceptionReporter,
            request,
            _exc_info(),
            explanation="<script>alert(1)</script>",
        )

        html = reporter.get_traceback_html()

        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_no_attribute_returns_parent_html_unchanged(self):
        request = self.factory.get("/")
        exc_info = _exc_info()
        reporter = _build_reporter(ExplainErrorsExceptionReporter, request, exc_info)
        parent_reporter = _build_reporter(ExceptionReporter, request, exc_info)

        self.assertEqual(reporter.get_traceback_html(), parent_reporter.get_traceback_html())

    def test_missing_anchor_returns_parent_html_unchanged(self):
        request = self.factory.get("/")
        exc_info = _exc_info()
        reporter = _build_reporter(
            ExplainErrorsExceptionReporter, request, exc_info, explanation="Explained."
        )

        with patch.object(
            ExceptionReporter, "get_traceback_html", return_value="<html>no anchor here</html>"
        ):
            html = reporter.get_traceback_html()

        self.assertEqual(html, "<html>no anchor here</html>")

    def test_internal_exception_returns_parent_html_unchanged(self):
        request = self.factory.get("/")
        exc_info = _exc_info()
        reporter = _build_reporter(
            ExplainErrorsExceptionReporter, request, exc_info, explanation="Explained."
        )
        parent_html = ExceptionReporter.get_traceback_html(reporter)

        with patch(
            "explain_errors.debug_page.escape", side_effect=RuntimeError("boom")
        ):
            with self.assertLogs("explain_errors", level="WARNING"):
                html = reporter.get_traceback_html()

        self.assertEqual(html, parent_html)


@override_settings(
    DEBUG=True,
    OPENAI_API_KEY="test-key",
    ROOT_URLCONF="tests.urls_debug_page",
    MIDDLEWARE=["explain_errors.middleware.ExplainErrorsMiddleware"],
)
class DebugPageRequestCycleTest(SimpleTestCase):
    def setUp(self):
        self.patcher, self.mock_client = _mock_openai()
        self.addCleanup(self.patcher.stop)

    def test_default_settings_banner_present_and_stdout_prints(self):
        client = Client(raise_request_exception=False)
        with patch("builtins.print") as mock_print:
            response = client.get("/boom/")

        self.assertEqual(response.status_code, 500)
        content = response.content.decode()
        self.assertIn('id="explain-errors"', content)
        self.assertIn("Mocked explanation.", content)
        mock_print.assert_any_call("Error explanation (gpt-4o-mini):\n", "Mocked explanation.")

    @override_settings(EXPLAIN_ERRORS_INJECT_DEBUG_PAGE=False)
    def test_inject_debug_page_off_no_banner(self):
        client = Client(raise_request_exception=False)
        response = client.get("/boom/")

        self.assertEqual(response.status_code, 500)
        self.assertNotIn('id="explain-errors"', response.content.decode())

    @override_settings(EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE=False)
    def test_preserve_debug_page_off_json_500_unchanged(self):
        client = Client(raise_request_exception=False)
        response = client.get("/boom/")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertNotIn(b'<section id="explain-errors"', response.content)

    @override_settings(EXPLAIN_ERRORS_MAX_CALLS=0)
    def test_throttle_exhausted_plain_debug_page_no_banner(self):
        client = Client(raise_request_exception=False)
        response = client.get("/boom/")

        self.assertEqual(response.status_code, 500)
        self.assertNotIn('id="explain-errors"', response.content.decode())

    def test_openai_raises_plain_debug_page_no_banner(self):
        self.mock_client.chat.completions.create.side_effect = RuntimeError("api down")
        client = Client(raise_request_exception=False)
        response = client.get("/boom/")

        self.assertEqual(response.status_code, 500)
        self.assertNotIn('id="explain-errors"', response.content.decode())

    @override_settings(DEFAULT_EXCEPTION_REPORTER="tests.test_debug_page.PlainReporter")
    def test_custom_default_exception_reporter_not_overridden(self):
        client = Client(raise_request_exception=False)
        response = client.get("/boom/")

        self.assertEqual(response.status_code, 500)
        self.assertNotIn('id="explain-errors"', response.content.decode())

    @override_settings(EXPLAIN_ERRORS_INJECT_DEBUG_PAGE=_RaisingBool())
    def test_injection_setup_failure_falls_back_to_plain_debug_page(self):
        client = Client(raise_request_exception=False)

        with self.assertLogs("explain_errors", level="WARNING") as cm:
            response = client.get("/boom/")

        self.assertEqual(response.status_code, 500)
        content = response.content.decode()
        self.assertNotIn('id="explain-errors"', content)
        self.assertIn("ValueError", content)
        self.assertTrue(
            any("debug page injection setup failed" in message for message in cm.output)
        )

    def test_accept_text_plain_unchanged(self):
        client = Client(raise_request_exception=False)
        response = client.get("/boom/", HTTP_ACCEPT="text/plain")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
        self.assertNotIn(b'<section id="explain-errors"', response.content)

    async def test_async_view_banner_present(self):
        client = AsyncClient(raise_request_exception=False)
        response = await client.get("/boom-async/")

        self.assertEqual(response.status_code, 500)
        self.assertIn('id="explain-errors"', response.content.decode())


class RenderExplanationHtmlTest(SimpleTestCase):
    """Unit tests for the Markdown-subset renderer used by the banner."""

    def test_inline_code(self):
        html = render_explanation_html("Use `foo` here.")

        self.assertIn("<code", html)
        self.assertIn(">foo</code>", html)
        self.assertNotIn("`", html)

    def test_bold(self):
        html = render_explanation_html("This is **bold** text.")

        self.assertIn("<strong>bold</strong>", html)

    def test_fenced_code_block_with_language_tag(self):
        html = render_explanation_html("```python\nx = 1\n```")

        self.assertIn("<pre", html)
        self.assertIn("<code>x = 1</code>", html)
        self.assertNotIn("python", html)

    def test_fenced_code_block_without_language_tag(self):
        html = render_explanation_html("```\ny = 2\n```")

        self.assertIn("<pre", html)
        self.assertIn("<code>y = 2</code>", html)

    def test_unordered_list(self):
        html = render_explanation_html("- one\n- two")

        self.assertIn("<ul", html)
        self.assertEqual(html.count("<li "), 2)
        self.assertIn(">one</li>", html)
        self.assertIn(">two</li>", html)

    def test_ordered_list(self):
        html = render_explanation_html("1. one\n2. two")

        self.assertIn("<ol", html)
        self.assertEqual(html.count("<li "), 2)
        self.assertIn(">one</li>", html)
        self.assertIn(">two</li>", html)

    def test_paragraphs_and_single_line_breaks(self):
        html = render_explanation_html("line1\nline2\n\npara2")

        self.assertIn("line1<br>line2", html)
        self.assertEqual(html.count("<p"), 2)

    def test_script_tag_in_prose_is_escaped(self):
        html = render_explanation_html("<script>alert(1)</script>")

        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_script_tag_in_inline_code_is_escaped(self):
        html = render_explanation_html("Run `<script>alert(1)</script>` now.")

        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("<code", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_script_tag_in_fenced_code_is_escaped(self):
        html = render_explanation_html("```\n<script>alert(1)</script>\n```")

        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("<pre", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_bold_and_dash_inside_code_are_not_transformed(self):
        html = render_explanation_html("Use `a**b**c` here.")

        self.assertNotIn("<strong>", html)
        self.assertIn("a**b**c", html)

    def test_dash_inside_fenced_code_is_not_a_list(self):
        html = render_explanation_html("```\n- not a list\n```")

        self.assertNotIn("<ul", html)
        self.assertIn("- not a list", html)

    def test_unclosed_fence_renders_remainder_as_code(self):
        html = render_explanation_html(
            "before\n```python\ntitle = 1\n- check the field"
        )

        self.assertIn("<p", html)
        self.assertIn("before", html)
        self.assertIn("<pre", html)
        self.assertIn("check the field", html)
        self.assertNotIn("<ul", html)

    def test_markdown_link_stays_literal(self):
        html = render_explanation_html("[text](url)")

        self.assertNotIn("<a ", html)
        self.assertIn("[text](url)", html)

    def test_raising_transform_falls_back_to_escaped_plain_text_and_logs(self):
        with patch(
            "explain_errors.debug_page._render", side_effect=RuntimeError("boom")
        ):
            with self.assertLogs("explain_errors", level="WARNING"):
                html = render_explanation_html("<script>alert(1)</script>")

        self.assertIn("white-space: pre-wrap", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)


@override_settings(ROOT_URLCONF="tests.urls_debug_page")
class ExplainErrorsExceptionReporterMarkdownTest(SimpleTestCase):
    """Reporter-level check that the banner renders Markdown, not raw text."""

    def setUp(self):
        self.factory = RequestFactory()
        patcher = patch(
            "django.views.debug.timezone.now",
            return_value=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_banner_contains_code_tag_for_backticked_name(self):
        request = self.factory.get("/")
        reporter = _build_reporter(
            ExplainErrorsExceptionReporter,
            request,
            _exc_info(),
            explanation="The view `create_post` failed.",
        )

        html = reporter.get_traceback_html()

        self.assertIn("<code", html)
        self.assertIn("create_post", html)


class PlainReporter(ExceptionReporter):
    """Stand-in custom reporter used to verify DEFAULT_EXCEPTION_REPORTER
    overrides are respected and never silently replaced."""
