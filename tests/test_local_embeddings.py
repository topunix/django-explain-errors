import math
import os
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import MagicMock, patch

from django.core.exceptions import ImproperlyConfigured
from django.test import (
    AsyncRequestFactory,
    RequestFactory,
    SimpleTestCase,
    override_settings,
)

from explain_errors import client as client_module
from explain_errors.client import (
    DEFAULT_LOCAL_EMBED_MODEL,
    EmbeddingsNotSupportedError,
    LocalEmbeddingsClient,
    create_embeddings,
    get_embed_backend,
    get_embeddings_client,
)
from explain_errors.middleware import ExplainErrorsMiddleware
from explain_errors.rag import retriever as retriever_module
from explain_errors.rag.indexer import build_index, get_embed_model
from explain_errors.rag.retriever import (
    EmbeddingIndexMismatchError,
    retrieve_chunks,
)
from explain_errors.rag.store import VectorStore

try:
    import sqlite_vec  # noqa: F401
    HAS_SQLITE_VEC = True
except ImportError:
    HAS_SQLITE_VEC = False

try:
    import sentence_transformers  # noqa: F401
    HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    HAS_SENTENCE_TRANSFORMERS = False

requires_sqlite_vec = unittest.skipUnless(
    HAS_SQLITE_VEC, "sqlite-vec not installed (rag extra)"
)

ENV_VARS = (
    "EXPLAIN_ERRORS_RAG_EMBED_BACKEND",
    "EXPLAIN_ERRORS_RAG_EMBED_BASE_URL",
    "EXPLAIN_ERRORS_RAG_EMBED_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
)


class FakeSentenceTransformer:
    """Stands in for sentence_transformers.SentenceTransformer. Records
    construction and encode calls; never downloads anything."""

    instances = []
    dimension = 3

    def __init__(self, name):
        self.name = name
        self.encode_calls = []
        self.encode_threads = []
        FakeSentenceTransformer.instances.append(self)

    def encode(self, texts, normalize_embeddings=False, show_progress_bar=True):
        self.encode_calls.append(
            {"texts": list(texts), "normalize_embeddings": normalize_embeddings}
        )
        self.encode_threads.append(threading.get_ident())
        vectors = []
        for _ in texts:
            vec = [3.0, 4.0, 0.0][: self.dimension]
            vec += [0.0] * (self.dimension - len(vec))
            if normalize_embeddings:
                norm = math.sqrt(sum(x * x for x in vec))
                vec = [x / norm for x in vec]
            vectors.append(vec)
        return vectors


def _fake_st_module():
    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = FakeSentenceTransformer
    return module


class LocalTestBase(SimpleTestCase):

    def setUp(self):
        env = patch.dict(os.environ, {})
        env.start()
        self.addCleanup(env.stop)
        for name in ENV_VARS:
            os.environ.pop(name, None)

        FakeSentenceTransformer.instances = []
        FakeSentenceTransformer.dimension = 3
        client_module._local_models.clear()
        retriever_module._warned_mismatches.clear()
        self.addCleanup(client_module._local_models.clear)
        self.addCleanup(retriever_module._warned_mismatches.clear)

        modules = patch.dict(sys.modules, {"sentence_transformers": _fake_st_module()})
        modules.start()
        self.addCleanup(modules.stop)


class BackendSelectionTest(LocalTestBase):

    def test_default_is_openai(self):
        self.assertEqual(get_embed_backend(), "openai")

    @override_settings(EXPLAIN_ERRORS_RAG_EMBED_BACKEND="local")
    def test_settings_select_local(self):
        self.assertEqual(get_embed_backend(), "local")

    @override_settings(EXPLAIN_ERRORS_RAG_EMBED_BACKEND="local")
    def test_env_var_overrides_settings(self):
        os.environ["EXPLAIN_ERRORS_RAG_EMBED_BACKEND"] = "openai"
        self.assertEqual(get_embed_backend(), "openai")

    @override_settings(EXPLAIN_ERRORS_RAG_EMBED_BACKEND="openai")
    def test_env_var_selects_local_over_settings(self):
        os.environ["EXPLAIN_ERRORS_RAG_EMBED_BACKEND"] = "LOCAL"
        self.assertEqual(get_embed_backend(), "local")

    @override_settings(EXPLAIN_ERRORS_RAG_EMBED_BACKEND="local")
    def test_empty_env_var_counts_as_unset(self):
        os.environ["EXPLAIN_ERRORS_RAG_EMBED_BACKEND"] = ""
        self.assertEqual(get_embed_backend(), "local")

    @override_settings(EXPLAIN_ERRORS_RAG_EMBED_BACKEND="onnx")
    def test_unknown_backend_is_rejected(self):
        with self.assertRaises(ImproperlyConfigured) as ctx:
            get_embed_backend()
        self.assertIn("onnx", str(ctx.exception))

    @override_settings(EXPLAIN_ERRORS_RAG_EMBED_BACKEND="local")
    def test_local_backend_returns_local_client(self):
        self.assertIsInstance(get_embeddings_client(), LocalEmbeddingsClient)

    def test_openai_backend_still_returns_openai_client(self):
        with override_settings(OPENAI_API_KEY="k"), patch("openai.OpenAI") as cls:
            get_embeddings_client()
        cls.assert_called_once()


class LocalNeedsNoKeyTest(LocalTestBase):

    @override_settings(
        EXPLAIN_ERRORS_RAG_EMBED_BACKEND="local",
        EXPLAIN_ERRORS_RAG_EMBED_BASE_URL="https://other.example/v1",
        OPENAI_BASE_URL="https://chat.example/v1",
    )
    def test_no_key_and_embed_url_settings_ignored(self):
        # With the openai backend this combination raises ImproperlyConfigured
        # (differing embed URL, no embed key). Local ignores both.
        with patch("openai.OpenAI") as cls:
            client = get_embeddings_client()
            response = create_embeddings(client, "all-MiniLM-L6-v2", ["x"])
        cls.assert_not_called()
        self.assertEqual(len(response.data), 1)


class LocalModelTest(LocalTestBase):

    @override_settings(EXPLAIN_ERRORS_RAG_EMBED_BACKEND="local")
    def test_default_local_model(self):
        self.assertEqual(get_embed_model(), DEFAULT_LOCAL_EMBED_MODEL)

    @override_settings(
        EXPLAIN_ERRORS_RAG_EMBED_BACKEND="local",
        EXPLAIN_ERRORS_RAG_EMBED_MODEL="paraphrase-MiniLM-L3-v2",
    )
    def test_model_override(self):
        self.assertEqual(get_embed_model(), "paraphrase-MiniLM-L3-v2")

    def test_openai_default_model_unchanged(self):
        self.assertEqual(get_embed_model(), "text-embedding-3-small")

    @override_settings(
        EXPLAIN_ERRORS_RAG_EMBED_BACKEND="local",
        EXPLAIN_ERRORS_RAG_EMBED_MODEL="text-embedding-3-small",
    )
    def test_leftover_openai_model_name_is_rejected(self):
        with self.assertRaises(ImproperlyConfigured) as ctx:
            get_embed_model()
        message = str(ctx.exception)
        self.assertIn("not a local sentence-transformers model", message)
        self.assertIn("Unset EXPLAIN_ERRORS_RAG_EMBED_MODEL", message)
        self.assertEqual(FakeSentenceTransformer.instances, [])

    def test_client_rejects_openai_model_name_directly(self):
        client = LocalEmbeddingsClient()
        with self.assertRaises(ImproperlyConfigured):
            create_embeddings(client, "text-embedding-3-large", ["x"])

    def test_model_load_failure_raises_improperly_configured(self):
        def boom(name):
            raise OSError("no such model on the hub")

        with patch.object(FakeSentenceTransformer, "__init__", lambda self, n: boom(n)):
            with self.assertRaises(ImproperlyConfigured) as ctx:
                create_embeddings(LocalEmbeddingsClient(), "not-a-model", ["x"])
        message = str(ctx.exception)
        self.assertIn("'not-a-model'", message)
        self.assertIn("Unset EXPLAIN_ERRORS_RAG_EMBED_MODEL", message)

    def test_missing_extra_names_pip_command(self):
        with patch.dict(sys.modules, {"sentence_transformers": None}):
            with self.assertRaises(ImproperlyConfigured) as ctx:
                create_embeddings(LocalEmbeddingsClient(), "all-MiniLM-L6-v2", ["x"])
        self.assertIn(
            "pip install django-explain-errors[local-embeddings]", str(ctx.exception)
        )

    def test_model_is_loaded_once_per_process(self):
        client = LocalEmbeddingsClient()
        create_embeddings(client, "all-MiniLM-L6-v2", ["a"])
        create_embeddings(client, "all-MiniLM-L6-v2", ["b"])
        create_embeddings(LocalEmbeddingsClient(), "all-MiniLM-L6-v2", ["c"])
        self.assertEqual(len(FakeSentenceTransformer.instances), 1)
        self.assertEqual(len(FakeSentenceTransformer.instances[0].encode_calls), 3)

    def test_different_models_are_cached_separately(self):
        client = LocalEmbeddingsClient()
        create_embeddings(client, "model-a", ["a"])
        create_embeddings(client, "model-b", ["a"])
        self.assertEqual(
            sorted(m.name for m in FakeSentenceTransformer.instances),
            ["model-a", "model-b"],
        )

    def test_embeddings_are_unit_length(self):
        response = create_embeddings(
            LocalEmbeddingsClient(), "all-MiniLM-L6-v2", ["a", "b"]
        )
        model = FakeSentenceTransformer.instances[0]
        self.assertTrue(model.encode_calls[0]["normalize_embeddings"])
        self.assertEqual(len(response.data), 2)
        for item in response.data:
            self.assertIsInstance(item.embedding, list)
            self.assertAlmostEqual(
                math.sqrt(sum(x * x for x in item.embedding)), 1.0, places=6
            )


class EmbeddingsNotSupportedMessageTest(SimpleTestCase):

    @override_settings(OPENAI_BASE_URL="https://api.anthropic.com/v1/")
    def test_message_mentions_local_backend(self):
        from tests.test_client import _client_answering

        client = _client_answering(
            404, {"error": {"message": "Not found"}}, "https://api.anthropic.com/v1/"
        )
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_BASE_URL", None)
            with self.assertRaises(EmbeddingsNotSupportedError) as ctx:
                create_embeddings(client, "m", ["x"])
        message = str(ctx.exception)
        self.assertIn('EXPLAIN_ERRORS_RAG_EMBED_BACKEND = "local"', message)
        self.assertIn("local-embeddings", message)
        self.assertNotIn("roadmap", message)


SOURCE = "def foo():\n    return 1\n"


@requires_sqlite_vec
class IndexMetadataTest(LocalTestBase):

    def _build(self, tmp, **extra):
        with open(os.path.join(tmp, "app.py"), "w") as f:
            f.write(SOURCE)
        index_path = os.path.join(tmp, "index.db")
        with override_settings(
            EXPLAIN_ERRORS_RAG_INCLUDE=[tmp],
            EXPLAIN_ERRORS_RAG_INDEX_PATH=index_path,
            **extra,
        ):
            build_index()
        return index_path

    def _retrieve(self, index_path, **extra):
        with override_settings(
            EXPLAIN_ERRORS_RAG_ENABLED=True,
            EXPLAIN_ERRORS_RAG_INDEX_PATH=index_path,
            **extra,
        ):
            return retrieve_chunks(ValueError("boom"))

    def _fake_openai_embed_client(self, dim=3):
        client = MagicMock()
        client.embeddings.create.side_effect = lambda model, input: MagicMock(
            data=[MagicMock(embedding=[1.0] + [0.0] * (dim - 1)) for _ in input]
        )
        return client

    def test_local_build_records_backend_model_dimension(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._build(tmp, EXPLAIN_ERRORS_RAG_EMBED_BACKEND="local")
            with VectorStore(path) as store:
                meta = store.read_meta()
        self.assertEqual(
            meta,
            {"backend": "local", "model": DEFAULT_LOCAL_EMBED_MODEL, "dimension": 3},
        )

    def test_local_roundtrip_retrieves_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._build(tmp, EXPLAIN_ERRORS_RAG_EMBED_BACKEND="local")
            chunks = self._retrieve(path, EXPLAIN_ERRORS_RAG_EMBED_BACKEND="local")
        self.assertEqual([c["file_path"].endswith("app.py") for c in chunks], [True])

    def test_openai_index_with_local_config_is_a_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "explain_errors.rag.indexer.get_embeddings_client",
                return_value=self._fake_openai_embed_client(),
            ):
                path = self._build(tmp)
            with self.assertRaises(EmbeddingIndexMismatchError) as ctx:
                self._retrieve(path, EXPLAIN_ERRORS_RAG_EMBED_BACKEND="local")
        message = str(ctx.exception)
        self.assertIn("backend=openai", message)
        self.assertIn("backend=local", message)
        self.assertIn("build_error_index", message)
        # The mismatch is caught before any model is loaded.
        self.assertEqual(FakeSentenceTransformer.instances, [])

    def test_local_index_with_openai_config_is_a_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._build(tmp, EXPLAIN_ERRORS_RAG_EMBED_BACKEND="local")
            with patch(
                "explain_errors.rag.retriever.get_embeddings_client",
                return_value=self._fake_openai_embed_client(),
            ) as get_client:
                with self.assertRaises(EmbeddingIndexMismatchError):
                    self._retrieve(path)
        get_client.assert_not_called()

    def test_model_change_is_a_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._build(tmp, EXPLAIN_ERRORS_RAG_EMBED_BACKEND="local")
            with self.assertRaises(EmbeddingIndexMismatchError) as ctx:
                self._retrieve(
                    path,
                    EXPLAIN_ERRORS_RAG_EMBED_BACKEND="local",
                    EXPLAIN_ERRORS_RAG_EMBED_MODEL="other-model",
                )
        self.assertIn("model=other-model", str(ctx.exception))

    def test_dimension_change_is_a_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._build(tmp, EXPLAIN_ERRORS_RAG_EMBED_BACKEND="local")
            client_module._local_models.clear()
            FakeSentenceTransformer.dimension = 5
            with self.assertRaises(EmbeddingIndexMismatchError) as ctx:
                self._retrieve(path, EXPLAIN_ERRORS_RAG_EMBED_BACKEND="local")
        self.assertIn("dimension=3", str(ctx.exception))
        self.assertIn("dimension=5", str(ctx.exception))

    def test_legacy_index_without_meta_is_rejected_for_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "index.db")
            with VectorStore(path) as store:
                store.create(3)
                store.add([("a.py", 1, 2, "chunk", [1.0, 0.0, 0.0])])
            with self.assertRaises(EmbeddingIndexMismatchError):
                self._retrieve(path, EXPLAIN_ERRORS_RAG_EMBED_BACKEND="local")

    def test_legacy_index_without_meta_still_works_for_openai(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "index.db")
            with VectorStore(path) as store:
                store.create(3)
                store.add([("a.py", 1, 2, "chunk", [1.0, 0.0, 0.0])])
            with patch(
                "explain_errors.rag.retriever.get_embeddings_client",
                return_value=self._fake_openai_embed_client(),
            ):
                chunks = self._retrieve(path)
        self.assertEqual(len(chunks), 1)


@requires_sqlite_vec
@override_settings(DEBUG=True, OPENAI_API_KEY="test-key")
class MismatchWarnsOnceTest(LocalTestBase):

    def setUp(self):
        super().setUp()
        patcher = patch("openai.OpenAI")
        cls = patcher.start()
        self.addCleanup(patcher.stop)
        cls.return_value.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="Mocked explanation."))]
        )
        self.chat = cls.return_value
        self.factory = RequestFactory()

    def _stale_index(self, tmp):
        path = os.path.join(tmp, "index.db")
        with VectorStore(path) as store:
            store.create(3)
            store.write_meta("openai", "text-embedding-3-small", 3)
            store.add([("a.py", 1, 2, "chunk", [1.0, 0.0, 0.0])])
        return path

    def test_mismatch_warning_logged_once_per_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._stale_index(tmp)
            with override_settings(
                EXPLAIN_ERRORS_RAG_ENABLED=True,
                EXPLAIN_ERRORS_RAG_INDEX_PATH=path,
                EXPLAIN_ERRORS_RAG_EMBED_BACKEND="local",
                EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE=False,
                EXPLAIN_ERRORS_MAX_CALLS=100,
            ):
                mw = ExplainErrorsMiddleware(lambda r: None)
                with patch.object(retriever_module.logger, "warning") as warn:
                    mw.process_exception(self.factory.get("/"), ValueError("one"))
                    mw.process_exception(self.factory.get("/"), KeyError("two"))
                    mw.process_exception(self.factory.get("/"), TypeError("three"))

                    # A different config is a different key and warns again.
                    with override_settings(EXPLAIN_ERRORS_RAG_EMBED_MODEL="other"):
                        mw.process_exception(self.factory.get("/"), OSError("four"))

        self.assertEqual(warn.call_count, 2)
        self.assertIn("build_error_index", str(warn.call_args_list[0].args[1]))
        # Every request still got an explanation (traceback-only fallback).
        self.assertEqual(self.chat.chat.completions.create.call_count, 4)
        for call in self.chat.chat.completions.create.call_args_list:
            self.assertNotIn(
                "Relevant project source:", call.kwargs["messages"][1]["content"]
            )
        # No model was ever loaded for a mismatched index.
        self.assertEqual(FakeSentenceTransformer.instances, [])


@requires_sqlite_vec
@override_settings(DEBUG=True, OPENAI_API_KEY="test-key")
class AsyncEncodeOffLoopTest(LocalTestBase):

    async def test_encode_runs_in_a_worker_thread_not_the_event_loop(self):
        loop_thread = threading.get_ident()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "index.db")
            with VectorStore(path) as store:
                store.create(3)
                store.write_meta("local", DEFAULT_LOCAL_EMBED_MODEL, 3)
                store.add([("app.py", 1, 3, "LOCAL_ASYNC_MARKER", [0.6, 0.8, 0.0])])

            async def boom(r):
                raise ValueError("async boom")

            with patch("openai.OpenAI") as cls:
                chat = cls.return_value
                chat.chat.completions.create.return_value = MagicMock(
                    choices=[MagicMock(message=MagicMock(content="ok"))]
                )
                with override_settings(
                    EXPLAIN_ERRORS_RAG_ENABLED=True,
                    EXPLAIN_ERRORS_RAG_INDEX_PATH=path,
                    EXPLAIN_ERRORS_RAG_EMBED_BACKEND="local",
                    EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE=False,
                ):
                    mw = ExplainErrorsMiddleware(boom)
                    response = await mw(AsyncRequestFactory().get("/"))

        self.assertEqual(response.status_code, 500)
        model = FakeSentenceTransformer.instances[0]
        self.assertEqual(len(model.encode_threads), 1)
        self.assertNotEqual(model.encode_threads[0], loop_thread)
        prompt = chat.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        self.assertIn("LOCAL_ASYNC_MARKER", prompt)


@unittest.skipUnless(
    os.environ.get("EXPLAIN_ERRORS_TEST_REAL_EMBEDDINGS") == "1"
    and HAS_SENTENCE_TRANSFORMERS,
    "set EXPLAIN_ERRORS_TEST_REAL_EMBEDDINGS=1 with the local-embeddings extra "
    "installed (downloads a model)",
)
class RealModelIntegrationTest(SimpleTestCase):

    @override_settings(EXPLAIN_ERRORS_RAG_EMBED_BACKEND="local")
    def test_real_model_returns_unit_vectors(self):
        client_module._local_models.clear()
        response = create_embeddings(
            get_embeddings_client(), get_embed_model(), ["KeyError in a view"]
        )
        vec = response.data[0].embedding
        self.assertEqual(len(vec), 384)
        self.assertAlmostEqual(math.sqrt(sum(x * x for x in vec)), 1.0, places=4)
