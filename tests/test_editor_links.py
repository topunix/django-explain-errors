import datetime
import logging
import os
import sys
from unittest.mock import MagicMock, patch
from urllib.parse import quote

from django.test import (
    AsyncClient,
    Client,
    RequestFactory,
    SimpleTestCase,
    override_settings,
)
from django.utils.html import escape

from explain_errors import debug_page
from explain_errors.debug_page import (
    BANNER_TEMPLATE,
    ExplainErrorsExceptionReporter,
    _build_links_html,
    _editor_url,
    render_explanation_html,
)
from explain_errors.middleware import ExplainErrorsMiddleware

TEMPLATE = "vscode://file/{path}:{line}"
THIS_FILE = os.path.abspath(__file__)


def _mock_openai():
    patcher = patch("openai.OpenAI")
    mock_cls = patcher.start()
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="Mocked explanation."))]
    )
    mock_cls.return_value = client
    return patcher, client


def _raise_here():
    raise ValueError("boom")  # RAISE_LINE


_RAISE_LINENO = None
with open(__file__) as _fh:
    for _n, _l in enumerate(_fh, 1):
        if _l.rstrip().endswith("# RAISE_LINE") and "raise ValueError" in _l:
            _RAISE_LINENO = _n
            break


def boom_here(request):
    _raise_here()


async def boom_here_async(request):
    _raise_here()


def _exc_info():
    try:
        _raise_here()
    except ValueError:
        return sys.exc_info()


def _reporter(request, exc_info=None):
    exc_type, exc_value, tb = exc_info or _exc_info()
    return ExplainErrorsExceptionReporter(request, exc_type, exc_value, tb)


def _expected_href(template=TEMPLATE, path=THIS_FILE, line=_RAISE_LINENO):
    return escape(template.format(path=quote(path, safe="/:\\"), line=line))


class _Mixin:
    def setUp(self):
        super().setUp()
        patcher = patch(
            "django.views.debug.timezone.now",
            return_value=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        # Fresh dedup state is per middleware instance; patch client per test.
        p, self.client_mock = _mock_openai()
        self.addCleanup(p.stop)


# URL conf defined in this module so views are application frames.
from django.urls import path as _path  # noqa: E402

urlpatterns = [
    _path("boom/", boom_here),
    _path("boom-async/", boom_here_async),
]

_MW = ["explain_errors.middleware.ExplainErrorsMiddleware"]


@override_settings(ROOT_URLCONF="tests.test_editor_links", DEBUG=True, MIDDLEWARE=_MW, OPENAI_API_KEY="test-key")
class ReporterUnitTests(_Mixin, SimpleTestCase):
    def _html(self, **extra):
        request = RequestFactory().get("/")
        request._explain_errors_explanation = "Explained."
        for k, v in extra.items():
            setattr(request, k, v)
        return _reporter(request).get_traceback_html()

    def test_setting_off_banner_byte_identical(self):
        expected = BANNER_TEMPLATE.format(explanation=render_explanation_html("Explained."))
        self.assertIn(expected, self._html())
        self.assertNotIn("Open in editor", self._html())
        with override_settings(EXPLAIN_ERRORS_EDITOR_URL=""):
            html = self._html()
        self.assertIn(expected, html)
        self.assertNotIn("Open in editor", html)

    @override_settings(EXPLAIN_ERRORS_EDITOR_URL=TEMPLATE)
    def test_frame_link_present(self):
        html = self._html()
        self.assertIn('href="%s"' % _expected_href(), html)
        self.assertIn("Open in editor", html)
        self.assertIn('rel="noopener noreferrer"', html)

    @override_settings(EXPLAIN_ERRORS_EDITOR_URL=TEMPLATE)
    def test_library_frame_excluded(self):
        import django.http

        lib_file = django.http.__file__
        with patch(
            "traceback.extract_tb",
            return_value=[
                MagicMock(filename=lib_file, lineno=7, f_name="x", **{"name": "lib_fn"}),
            ],
        ):
            html = self._html()
        self.assertNotIn("Open in editor", html)
        self.assertNotIn(quote(lib_file, safe="/:\\"), html.split('id="explain-errors"')[1].split("</section>")[0])

    @override_settings(EXPLAIN_ERRORS_EDITOR_URL="javascript:alert(1)//{path}{line}")
    def test_javascript_scheme_blocked_inline(self):
        self.assertNotIn("Open in editor", self._html())

    @override_settings(EXPLAIN_ERRORS_EDITOR_URL=TEMPLATE)
    def test_link_builder_failure_fails_open(self):
        with patch.object(debug_page, "_link_items", side_effect=RuntimeError("x")):
            html = self._html()
        self.assertIn("Explained.", html)
        self.assertNotIn("Open in editor", html)

    @override_settings(EXPLAIN_ERRORS_EDITOR_URL="vscode://file/{file}:{line}")
    def test_unknown_placeholder_warns_once(self):
        with self.assertLogs("explain_errors.debug_page", level="WARNING") as logs:
            html = self._html()
        self.assertEqual(len(logs.records), 1)
        self.assertIn("Explained.", html)
        self.assertNotIn("Open in editor", html)

    @override_settings(EXPLAIN_ERRORS_EDITOR_URL=TEMPLATE)
    def test_cited_files_rendered_and_deduplicated(self):
        html = self._html(
            _explain_errors_cited_files=[
                ("/proj/a.py", 10),
                ("/proj/a.py", 10),
                ("/proj/b.py", 3),
            ]
        )
        self.assertEqual(html.count("vscode://file//proj/a.py:10"), 1)
        self.assertEqual(html.count("vscode://file//proj/b.py:3"), 1)
        self.assertIn("Retrieved source", html)

    @override_settings(EXPLAIN_ERRORS_EDITOR_URL=TEMPLATE, DEBUG=False)
    def test_debug_false_guard(self):
        self.assertEqual(_build_links_html(_exc_info()[2], [("/a.py", 1)]), "")


class UrlHelperTests(SimpleTestCase):
    def test_space_encoded(self):
        self.assertEqual(
            _editor_url(TEMPLATE, "/home/me/my proj/a.py", 4),
            "vscode://file//home/me/my%20proj/a.py:4",
        )

    def test_windows_path_kept(self):
        self.assertEqual(
            _editor_url(TEMPLATE, "C:\\proj\\app\\views.py", 9),
            "vscode://file/C:\\proj\\app\\views.py:9",
        )

    @override_settings(DEBUG=True, EXPLAIN_ERRORS_EDITOR_URL='x://"<&>{path}:{line}')
    def test_html_escaping(self):
        html = _build_links_html(_exc_info()[2], [('/p/<a>&"b".py', 1)])
        self.assertIn("Open in editor", html)
        self.assertNotIn("<&", html)
        self.assertNotIn('"<', html)
        self.assertIn("&quot;", html)
        self.assertIn("&lt;", html)
        self.assertIn("&amp;", html)

    def test_scheme_guard_case_and_whitespace(self):
        for tpl in ("javascript:alert(1)", "  JavaScript:alert(1)", "\tDATA:x", "VBScript:x"):
            self.assertIsNone(_editor_url(tpl, "/a.py", 1), tpl)


@override_settings(ROOT_URLCONF="tests.test_editor_links", DEBUG=True, MIDDLEWARE=_MW, OPENAI_API_KEY="test-key",
                   ALLOWED_HOSTS=["testserver"])
class RequestCycleTests(_Mixin, SimpleTestCase):
    def _get(self, client=None):
        client = client or Client(raise_request_exception=False)
        return client.get("/boom/")

    @override_settings(EXPLAIN_ERRORS_EDITOR_URL=TEMPLATE)
    def test_frame_link_through_client(self):
        response = self._get()
        self.assertEqual(response.status_code, 500)
        body = response.content.decode()
        self.assertIn("Mocked explanation.", body)
        self.assertIn('href="%s"' % _expected_href(), body)

    @override_settings(EXPLAIN_ERRORS_EDITOR_URL=TEMPLATE)
    def test_dedup_replay_shows_frame_links_only(self):
        client = Client(raise_request_exception=False)  # one handler, one cache
        self._get(client)
        body = self._get(client).content.decode()
        self.assertEqual(self.client_mock.chat.completions.create.call_count, 1)
        self.assertIn('href="%s"' % _expected_href(), body)
        self.assertNotIn("Retrieved source", body)

    @override_settings(DEBUG=False, EXPLAIN_ERRORS_EDITOR_URL=TEMPLATE)
    def test_debug_false_inert(self):
        body = self._get().content.decode()
        self.assertNotIn("Open in editor", body)
        self.assertNotIn("Mocked explanation.", body)

    @override_settings(EXPLAIN_ERRORS_EDITOR_URL=TEMPLATE, EXPLAIN_ERRORS_RAG_ENABLED=True,
                       EXPLAIN_ERRORS_RAG_MAX_PROMPT_CHARS=100000)
    def test_rag_cited_chunks_linked(self):
        chunks = [
            {"file_path": "/proj/a.py", "start_line": 5, "end_line": 9, "chunk_text": "x"},
            {"file_path": "/proj/a.py", "start_line": 5, "end_line": 9, "chunk_text": "x"},
        ]
        with patch("explain_errors.middleware.retrieve_chunks", return_value=chunks):
            body = self._get().content.decode()
        self.assertEqual(body.count("vscode://file//proj/a.py:5"), 1)

    @override_settings(EXPLAIN_ERRORS_EDITOR_URL=TEMPLATE, EXPLAIN_ERRORS_RAG_ENABLED=True)
    def test_truncated_chunk_not_linked(self):
        from explain_errors.middleware import format_traceback, sanitize_traceback
        from explain_errors.rag.retriever import format_chunks_for_prompt

        chunks = [
            {"file_path": "/proj/a.py", "start_line": 5, "end_line": 9, "chunk_text": "x" * 50},
            {"file_path": "/proj/zzz_late.py", "start_line": 77, "end_line": 80,
             "chunk_text": "y" * 50},
        ]
        exc = ValueError("x")
        tb_len = len(sanitize_traceback(format_traceback(exc, 3000)))
        first_len = len(format_chunks_for_prompt(chunks[:1], 10**6))
        budget = tb_len + first_len + 10  # second header cannot fit
        mw = ExplainErrorsMiddleware(lambda r: None)
        cited = []
        with override_settings(EXPLAIN_ERRORS_RAG_MAX_PROMPT_CHARS=budget), \
                patch("explain_errors.middleware.retrieve_chunks", return_value=chunks):
            prompt = mw._build_prompt(exc, cited)
        self.assertNotIn("zzz_late.py:77-80", prompt)
        self.assertEqual(cited, [("/proj/a.py", 5)])

    @override_settings(EXPLAIN_ERRORS_EDITOR_URL=TEMPLATE, EXPLAIN_ERRORS_RAG_ENABLED=True,
                       EXPLAIN_ERRORS_RAG_MAX_PROMPT_CHARS=100000)
    def test_build_prompt_without_cited_unchanged(self):
        chunks = [{"file_path": "/p.py", "start_line": 1, "end_line": 2, "chunk_text": "x"}]
        mw = ExplainErrorsMiddleware(lambda r: None)
        with patch("explain_errors.middleware.retrieve_chunks", return_value=chunks):
            a = mw._build_prompt(ValueError("x"))
            cited = []
            b = mw._build_prompt(ValueError("x"), cited)
        self.assertEqual(a, b)
        self.assertEqual(cited, [("/p.py", 1)])


@override_settings(ROOT_URLCONF="tests.test_editor_links", DEBUG=True, MIDDLEWARE=_MW, OPENAI_API_KEY="test-key",
                   ALLOWED_HOSTS=["testserver"], EXPLAIN_ERRORS_EDITOR_URL=TEMPLATE)
class AsyncTests(_Mixin, SimpleTestCase):
    async def test_async_frame_link(self):
        response = await AsyncClient(raise_request_exception=False).get("/boom-async/")
        body = response.content.decode()
        self.assertEqual(response.status_code, 500)
        self.assertIn('href="%s"' % _expected_href(), body)
