import os
import tempfile
from io import StringIO
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, SimpleTestCase, override_settings

from explain_errors.middleware import ExplainErrorsMiddleware
from explain_errors.tracebacks import exception_dedup_key


def _raise_value(message="boom"):
    raise ValueError(message)


def _raise_value_elsewhere(message="boom"):
    raise ValueError(message)


def _raise_key(message="boom"):
    raise KeyError(message)


def _raise_two_lines(message="boom"):
    x = 1
    raise ValueError(message + str(x))


def _caught(fn, *args):
    try:
        fn(*args)
    except Exception as exc:
        return exc


def _response(content="Cached explanation."):
    choice = MagicMock(finish_reason="stop")
    choice.message.content = content
    return MagicMock(choices=[choice])


def _setup(test, **kwargs):
    """Build a middleware with a mocked OpenAI client; return (mw, create)."""
    with patch("openai.OpenAI") as mock_cls:
        client = MagicMock()
        client.chat.completions.create.return_value = _response()
        mock_cls.return_value = client
        mw = ExplainErrorsMiddleware(lambda r: None)
    return mw, client.chat.completions.create


@override_settings(
    DEBUG=True,
    OPENAI_API_KEY="test-key",
    EXPLAIN_ERRORS_PRINT_STDOUT=False,
)
class DedupTest(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.mw, self.create = _setup(self)

    def _handle(self, exc):
        request = self.factory.get("/")
        result = self.mw.process_exception(request, exc)
        return request, result

    def test_repeat_skips_model_and_throttle_and_injects(self):
        _, _ = self._handle(_caught(_raise_value))
        self.assertEqual(self.create.call_count, 1)

        with patch.object(self.mw.throttle, "allow") as allow:
            request, result = self._handle(_caught(_raise_value))
        allow.assert_not_called()
        self.assertEqual(self.create.call_count, 1)
        self.assertIsNone(result)
        self.assertEqual(request._explain_errors_explanation, "Cached explanation.")
        self.assertIsNotNone(request.exception_reporter_class)

    def test_repeat_does_not_consume_throttle(self):
        for _ in range(10):
            self._handle(_caught(_raise_value))
        self.assertEqual(len(self.mw.throttle._calls), 1)

    def test_different_exception_type_is_miss(self):
        self._handle(_caught(_raise_value))
        self._handle(_caught(_raise_key))
        self.assertEqual(self.create.call_count, 2)

    def test_different_frame_is_miss(self):
        self._handle(_caught(_raise_value))
        self._handle(_caught(_raise_value_elsewhere))
        self.assertEqual(self.create.call_count, 2)

    def test_different_line_text_is_miss(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "dedup_edit_target.py")

            def run(source):
                with open(path, "w") as f:
                    f.write(source)
                namespace = {}
                exec(compile(source, path, "exec"), namespace)
                return _caught(namespace["go"])

            first = run("def go():\n    raise ValueError('a')\n")
            first_key = exception_dedup_key(first)
            self._handle(first)
            second = run("def go():\n    raise ValueError('a')  # edited\n")
            second_key = exception_dedup_key(second)
            self._handle(second)
        self.assertNotEqual(first_key, second_key)
        self.assertEqual(self.create.call_count, 2)

    def test_different_line_number_is_miss(self):
        self.assertNotEqual(
            exception_dedup_key(_caught(_raise_value)),
            exception_dedup_key(_caught(_raise_two_lines)),
        )

    def test_different_message_same_line_is_miss(self):
        self._handle(_caught(_raise_value, "first"))
        self._handle(_caught(_raise_value, "second"))
        self.assertEqual(self.create.call_count, 2)

    def test_same_message_same_line_is_hit(self):
        self._handle(_caught(_raise_value, "same"))
        self._handle(_caught(_raise_value, "same"))
        self.assertEqual(self.create.call_count, 1)

    def test_failed_generation_is_not_cached(self):
        self.create.side_effect = RuntimeError("api down")
        with patch("builtins.print"):
            self._handle(_caught(_raise_value))
        self.assertEqual(len(self.mw._explanation_cache), 0)

        self.create.side_effect = None
        self.create.return_value = _response("Recovered.")
        request, _ = self._handle(_caught(_raise_value))
        self.assertEqual(self.create.call_count, 2)
        self.assertEqual(request._explain_errors_explanation, "Recovered.")

    def test_eviction_at_capacity(self):
        with patch("explain_errors.middleware.DEDUP_CACHE_SIZE", 2):
            self._handle(_caught(_raise_value))  # A
            self._handle(_caught(_raise_key))  # B
            self._handle(_caught(_raise_value))  # A hit, A now most recent
            self.assertEqual(self.create.call_count, 2)
            self._handle(_caught(_raise_value_elsewhere))  # C evicts B
            self.assertEqual(self.create.call_count, 3)
            self.assertEqual(len(self.mw._explanation_cache), 2)
            self._handle(_caught(_raise_value))  # A still cached
            self.assertEqual(self.create.call_count, 3)
            self._handle(_caught(_raise_key))  # B was evicted
            self.assertEqual(self.create.call_count, 4)

    def test_key_failure_falls_through_to_miss(self):
        with patch(
            "explain_errors.middleware.exception_dedup_key",
            side_effect=RuntimeError("bad key"),
        ):
            _, result = self._handle(_caught(_raise_value))
            self._handle(_caught(_raise_value))
        self.assertIsNone(result)
        self.assertEqual(self.create.call_count, 2)
        self.assertEqual(len(self.mw._explanation_cache), 0)

    def test_exception_without_traceback_is_miss(self):
        self._handle(ValueError("never raised"))
        self._handle(ValueError("never raised"))
        self.assertEqual(self.create.call_count, 2)

    def test_cached_error_replays_after_throttle_exhausted(self):
        self._handle(_caught(_raise_value))
        self.mw.throttle.max_calls = 1  # window is now full
        self.assertFalse(self.mw.throttle.allow())

        self._handle(_caught(_raise_key))  # new error: denied by throttle
        self.assertEqual(self.create.call_count, 1)

        request, _ = self._handle(_caught(_raise_value))  # cached: replays
        self.assertEqual(self.create.call_count, 1)
        self.assertEqual(request._explain_errors_explanation, "Cached explanation.")

    def test_json_500_path_returns_cached_explanation(self):
        with override_settings(EXPLAIN_ERRORS_PRESERVE_DEBUG_PAGE=False):
            self._handle(_caught(_raise_value))
            _, response = self._handle(_caught(_raise_value))
        self.assertEqual(response.status_code, 500)
        self.assertIn(b"Cached explanation.", response.content)
        self.assertEqual(self.create.call_count, 1)


@override_settings(DEBUG=True, OPENAI_API_KEY="test-key")
class DedupStdoutTest(SimpleTestCase):
    def test_hit_prints_one_line_not_full_reprint(self):
        mw, create = _setup(self)
        request = RequestFactory().get("/")
        with patch("sys.stdout", new_callable=StringIO) as out:
            mw.process_exception(request, _caught(_raise_value))
            first = out.getvalue()
            out.truncate(0)
            out.seek(0)
            mw.process_exception(request, _caught(_raise_value))
            second = out.getvalue()
        self.assertIn("Cached explanation.", first)
        self.assertNotIn("Cached explanation.", second)
        self.assertEqual(len(second.strip().splitlines()), 1)

    @override_settings(EXPLAIN_ERRORS_PRINT_STDOUT=False)
    def test_hit_prints_nothing_when_stdout_disabled(self):
        mw, create = _setup(self)
        request = RequestFactory().get("/")
        mw.process_exception(request, _caught(_raise_value))
        with patch("sys.stdout", new_callable=StringIO) as out:
            mw.process_exception(request, _caught(_raise_value))
        self.assertEqual(out.getvalue(), "")


@override_settings(
    DEBUG=True,
    OPENAI_API_KEY="test-key",
    EXPLAIN_ERRORS_PRINT_STDOUT=False,
    EXPLAIN_ERRORS_DEDUP=False,
)
class DedupDisabledTest(SimpleTestCase):
    def test_disabled_always_calls_model(self):
        mw, create = _setup(self)
        request = RequestFactory().get("/")
        with patch("explain_errors.middleware.exception_dedup_key") as key:
            for _ in range(3):
                mw.process_exception(request, _caught(_raise_value))
        key.assert_not_called()
        self.assertEqual(create.call_count, 3)
        self.assertEqual(len(mw._explanation_cache), 0)


@override_settings(DEBUG=True, OPENAI_API_KEY="test-key")
class DedupDebugOffTest(SimpleTestCase):
    def test_debug_false_computes_nothing(self):
        mw, create = _setup(self)
        with override_settings(DEBUG=False), patch(
            "explain_errors.middleware.exception_dedup_key"
        ) as key:
            result = mw.process_exception(RequestFactory().get("/"), _caught(_raise_value))
        self.assertIsNone(result)
        key.assert_not_called()
        create.assert_not_called()
        self.assertEqual(len(mw._explanation_cache), 0)


@override_settings(
    DEBUG=True,
    OPENAI_API_KEY="test-key",
    EXPLAIN_ERRORS_PRINT_STDOUT=False,
)
class DedupAsyncTest(SimpleTestCase):
    async def test_async_path_replays(self):
        async def failing(request):
            _raise_value()

        with patch("openai.OpenAI") as mock_cls:
            client = MagicMock()
            client.chat.completions.create.return_value = _response()
            mock_cls.return_value = client
            mw = ExplainErrorsMiddleware(failing)

        for _ in range(3):
            with self.assertRaises(ValueError):
                await mw(RequestFactory().get("/"))
        self.assertEqual(client.chat.completions.create.call_count, 1)
        self.assertEqual(len(mw.throttle._calls), 1)
