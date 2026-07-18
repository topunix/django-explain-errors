import json
import os
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import MagicMock, patch

from django.test import (
    AsyncRequestFactory,
    RequestFactory,
    SimpleTestCase,
    override_settings,
)

from explain_errors.middleware import ExplainErrorsMiddleware
from explain_errors.rag.indexer import (
    build_index,
    chunk_by_lines,
    chunk_python_text,
    discover_files,
    get_exclude_dirs,
    get_openai_client,
)
from explain_errors.rag.retriever import extract_project_frames, retrieve_chunks
from explain_errors.rag.store import VectorStore
from explain_errors.sanitize import sanitize_traceback as real_sanitize_traceback

try:
    import sqlite_vec  # noqa: F401
    HAS_SQLITE_VEC = True
except ImportError:
    HAS_SQLITE_VEC = False

requires_sqlite_vec = unittest.skipUnless(
    HAS_SQLITE_VEC, "sqlite-vec not installed (rag extra)"
)

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))



def test_get_openai_client_loads_dotenv(self):
    with patch("explain_errors.rag.indexer.load_dotenv") as mock_load:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            get_openai_client()
    mock_load.assert_called_once()


def _mock_openai():
    """Patch the chat-completion OpenAI client used by the middleware."""
    patcher = patch("explain_errors.middleware.OpenAI")
    mock_cls = patcher.start()
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="Mocked explanation."))]
    )
    mock_cls.return_value = client
    return patcher, client


def _mock_embed_client(vector):
    """Return a MagicMock standing in for `rag.indexer.get_openai_client()`
    whose embeddings.create() always returns `vector` for every input text.
    """
    client = MagicMock()

    def _create(model, input):
        return MagicMock(data=[MagicMock(embedding=vector) for _ in input])

    client.embeddings.create.side_effect = _create
    return client


SAMPLE_MODULE = textwrap.dedent(
    '''\
    """Module docstring."""
    import os


    def foo():
        return 1


    class Bar:
        def method(self):
            return 2
    '''
)


class IndexerChunkingTest(SimpleTestCase):

    def test_chunk_python_text_splits_on_function_and_class_boundaries(self):
        chunks = chunk_python_text(SAMPLE_MODULE)

        self.assertEqual(len(chunks), 2)

        start, end, text = chunks[0]
        self.assertEqual((start, end), (5, 6))
        self.assertIn("def foo():", text)

        start, end, text = chunks[1]
        self.assertEqual((start, end), (9, 11))
        self.assertIn("class Bar:", text)
        self.assertIn("def method(self):", text)

    def test_chunk_python_text_falls_back_on_syntax_error(self):
        broken = "def foo(:\n    pass\n" * 3
        chunks = chunk_python_text(broken)
        self.assertTrue(chunks)

    def test_chunk_by_lines_windows_with_overlap(self):
        text = "\n".join(f"line{i}" for i in range(1, 101))
        chunks = chunk_by_lines(text, window=80, overlap=20)

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0][:2], (1, 80))
        self.assertEqual(chunks[1][:2], (61, 100))


class IndexerDiscoveryTest(SimpleTestCase):

    def test_discover_files_respects_exclude_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "migrations"))
            os.makedirs(os.path.join(tmp, "templates"))

            included_py = os.path.join(tmp, "app.py")
            included_html = os.path.join(tmp, "templates", "index.html")
            excluded_py = os.path.join(tmp, "migrations", "0001_initial.py")
            skipped_ext = os.path.join(tmp, "notes.md")

            for path in (included_py, included_html, excluded_py, skipped_ext):
                with open(path, "w") as f:
                    f.write("content\n")

            files = discover_files([tmp], get_exclude_dirs())

        self.assertIn(included_py, files)
        self.assertIn(included_html, files)
        self.assertNotIn(excluded_py, files)
        self.assertNotIn(skipped_ext, files)


@requires_sqlite_vec
class IndexerBuildTest(SimpleTestCase):

    def test_build_index_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "app.py"), "w") as f:
                f.write(SAMPLE_MODULE)

            index_path = os.path.join(tmp, "index.db")

            with override_settings(
                EXPLAIN_ERRORS_RAG_INCLUDE=[tmp],
                EXPLAIN_ERRORS_RAG_INDEX_PATH=index_path,
            ):
                with patch(
                    "explain_errors.rag.indexer.get_openai_client",
                    return_value=_mock_embed_client([0.1, 0.2, 0.3]),
                ):
                    first = build_index()
                    second = build_index()

            self.assertEqual(first["chunks_embedded"], 2)
            self.assertEqual(second["chunks_embedded"], 2)
            self.assertEqual(first["index_path"], index_path)

            with VectorStore(index_path) as store:
                self.assertEqual(store.count(), 2)

            leftovers = [
                name for name in os.listdir(tmp) if name.startswith("index.db.tmp-")
            ]
            self.assertEqual(leftovers, [])


def _raise_json_error():
    return json.loads("{not valid json")


class RetrieverFrameExtractionTest(SimpleTestCase):

    @override_settings(EXPLAIN_ERRORS_RAG_INCLUDE=[TESTS_DIR])
    def test_extract_project_frames_skips_stdlib(self):
        try:
            _raise_json_error()
        except Exception as exc:
            frames = extract_project_frames(exc)

        filenames = [frame.filename for frame in frames]
        self.assertTrue(any(name.endswith("test_rag.py") for name in filenames))
        self.assertFalse(any("json" in os.path.basename(name) for name in filenames))

    def test_extract_project_frames_empty_without_traceback(self):
        self.assertEqual(extract_project_frames(ValueError("no traceback")), [])


class RetrieverTopKTest(SimpleTestCase):

    @requires_sqlite_vec
    def test_top_k_retrieval_dedupes_by_file_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            index_path = os.path.join(tmp, "index.db")
            with VectorStore(index_path) as store:
                store.create(3)
                store.add(
                    [
                        ("a.py", 1, 5, "chunk a1", [1.0, 0.0, 0.0]),
                        ("a.py", 10, 15, "chunk a2", [0.9, 0.1, 0.0]),
                        ("b.py", 1, 5, "chunk b1", [0.0, 1.0, 0.0]),
                    ]
                )

            with override_settings(
                EXPLAIN_ERRORS_RAG_ENABLED=True,
                EXPLAIN_ERRORS_RAG_INDEX_PATH=index_path,
            ):
                with patch(
                    "explain_errors.rag.retriever.get_openai_client",
                    return_value=_mock_embed_client([1.0, 0.0, 0.0]),
                ):
                    chunks = retrieve_chunks(ValueError("boom"), top_k=3)

        self.assertEqual([c["file_path"] for c in chunks], ["a.py", "b.py"])
        self.assertEqual(chunks[0]["chunk_text"], "chunk a1")

    def test_retrieve_chunks_returns_empty_when_disabled(self):
        self.assertEqual(retrieve_chunks(ValueError("boom")), [])


@override_settings(DEBUG=True, OPENAI_API_KEY="test-key")
class MiddlewareRagIntegrationTest(SimpleTestCase):

    def setUp(self):
        self.factory = RequestFactory()
        self.patcher, self.client = _mock_openai()
        self.addCleanup(self.patcher.stop)

    def _build_fixture_index(self, tmp, chunk_text="UNIQUE_CHUNK_MARKER_123"):
        index_path = os.path.join(tmp, "index.db")
        with VectorStore(index_path) as store:
            store.create(3)
            store.add([("app.py", 1, 3, chunk_text, [1.0, 0.0, 0.0])])
        return index_path

    @requires_sqlite_vec
    def test_rag_enabled_injects_retrieved_chunk_into_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            index_path = self._build_fixture_index(tmp)

            with override_settings(
                EXPLAIN_ERRORS_RAG_ENABLED=True,
                EXPLAIN_ERRORS_RAG_INDEX_PATH=index_path,
            ):
                with patch(
                    "explain_errors.rag.retriever.get_openai_client",
                    return_value=_mock_embed_client([1.0, 0.0, 0.0]),
                ):
                    middleware = ExplainErrorsMiddleware(lambda r: None)
                    request = self.factory.get("/")
                    try:
                        raise ValueError("boom")
                    except ValueError as exc:
                        middleware.process_exception(request, exc)

        prompt = self.client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        self.assertIn("Relevant project source:", prompt)
        self.assertIn("UNIQUE_CHUNK_MARKER_123", prompt)
        self.assertIn("app.py:1-3", prompt)

    def test_rag_disabled_by_default_prompt_is_byte_identical(self):
        middleware = ExplainErrorsMiddleware(lambda r: None)
        request = self.factory.get("/")

        captured = {}

        def spy_sanitize(tb):
            result = real_sanitize_traceback(tb)
            captured["tb"] = result
            return result

        with patch("explain_errors.middleware.sanitize_traceback", side_effect=spy_sanitize):
            try:
                raise ValueError("boom")
            except ValueError as exc:
                middleware.process_exception(request, exc)

        prompt = self.client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        expected = f"Explain the following Django error in simple terms:\n\n{captured['tb']}"
        self.assertEqual(prompt, expected)
        self.assertNotIn("Relevant project source:", prompt)

    def test_fallback_when_index_file_missing(self):
        with override_settings(
            EXPLAIN_ERRORS_RAG_ENABLED=True,
            EXPLAIN_ERRORS_RAG_INDEX_PATH="/nonexistent/path/index.db",
        ):
            middleware = ExplainErrorsMiddleware(lambda r: None)
            request = self.factory.get("/")
            response = middleware.process_exception(request, ValueError("boom"))

        self.assertEqual(response.status_code, 500)
        self.assertEqual(json.loads(response.content)["message"], "Mocked explanation.")
        prompt = self.client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        self.assertNotIn("Relevant project source:", prompt)

    def test_fallback_when_sqlite_vec_import_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            index_path = os.path.join(tmp, "index.db")
            with open(index_path, "w") as f:
                f.write("not a real db, existence is all that matters here")

            with override_settings(
                EXPLAIN_ERRORS_RAG_ENABLED=True,
                EXPLAIN_ERRORS_RAG_INDEX_PATH=index_path,
            ):
                with patch(
                    "explain_errors.rag.retriever.get_openai_client",
                    return_value=_mock_embed_client([1.0, 0.0, 0.0]),
                ):
                    with patch.dict(sys.modules, {"sqlite_vec": None}):
                        middleware = ExplainErrorsMiddleware(lambda r: None)
                        request = self.factory.get("/")
                        response = middleware.process_exception(request, ValueError("boom"))

        self.assertEqual(response.status_code, 500)
        self.assertEqual(json.loads(response.content)["message"], "Mocked explanation.")
        prompt = self.client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        self.assertNotIn("Relevant project source:", prompt)

    @requires_sqlite_vec
    def test_fallback_when_retrieval_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            index_path = self._build_fixture_index(tmp)

            with override_settings(
                EXPLAIN_ERRORS_RAG_ENABLED=True,
                EXPLAIN_ERRORS_RAG_INDEX_PATH=index_path,
            ):
                broken_client = MagicMock()
                broken_client.embeddings.create.side_effect = RuntimeError("embedding service down")
                with patch(
                    "explain_errors.rag.retriever.get_openai_client",
                    return_value=broken_client,
                ):
                    middleware = ExplainErrorsMiddleware(lambda r: None)
                    request = self.factory.get("/")
                    response = middleware.process_exception(request, ValueError("boom"))

        self.assertEqual(response.status_code, 500)
        self.assertEqual(json.loads(response.content)["message"], "Mocked explanation.")
        prompt = self.client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        self.assertNotIn("Relevant project source:", prompt)


@override_settings(DEBUG=True, OPENAI_API_KEY="test-key")
@requires_sqlite_vec
class MiddlewareRagAsyncTest(SimpleTestCase):

    def setUp(self):
        self.factory = AsyncRequestFactory()
        self.patcher, self.client = _mock_openai()
        self.addCleanup(self.patcher.stop)

    async def test_rag_retrieval_under_async_get_response_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            index_path = os.path.join(tmp, "index.db")
            with VectorStore(index_path) as store:
                store.create(3)
                store.add([("app.py", 1, 3, "ASYNC_UNIQUE_MARKER", [1.0, 0.0, 0.0])])

            async def boom(r):
                raise ValueError("async boom")

            with override_settings(
                EXPLAIN_ERRORS_RAG_ENABLED=True,
                EXPLAIN_ERRORS_RAG_INDEX_PATH=index_path,
            ):
                with patch(
                    "explain_errors.rag.retriever.get_openai_client",
                    return_value=_mock_embed_client([1.0, 0.0, 0.0]),
                ):
                    mw = ExplainErrorsMiddleware(boom)
                    response = await mw(self.factory.get("/"))

        self.assertEqual(response.status_code, 500)
        prompt = self.client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        self.assertIn("ASYNC_UNIQUE_MARKER", prompt)
