from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase
from openai import APIConnectionError, OpenAIError, RateLimitError

COMMAND_BUILD_INDEX = "explain_errors.management.commands.build_error_index.build_index"


class _FakeAPIError(OpenAIError):
    """Stands in for a real SDK error. Constructed directly rather than via
    RateLimitError/APIConnectionError, whose signatures require an HTTP client
    Request/Response object that changed libraries in openai 3.0 (httpx ->
    httpx2). The command only cares about the OpenAIError base class and .code,
    so this keeps the suite green across openai majors."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class _FakeConnectionError(OpenAIError):
    """An OpenAIError with no .code, to exercise the class-name fallback."""


class BuildErrorIndexCommandTest(SimpleTestCase):
    """The command must fail with a one-line message and nonzero exit when the
    OpenAI API errors, instead of dying with a raw traceback (issue #1)."""

    def test_real_sdk_errors_are_caught_by_the_base_class(self):
        """Guards the assumption the fakes rely on."""
        self.assertTrue(issubclass(RateLimitError, OpenAIError))
        self.assertTrue(issubclass(APIConnectionError, OpenAIError))

    @patch(COMMAND_BUILD_INDEX)
    def test_rate_limit_error_becomes_one_line_command_error(self, mock_build):
        mock_build.side_effect = _FakeAPIError(
            "You exceeded your current quota", code="insufficient_quota"
        )
        with self.assertRaises(CommandError) as ctx:
            call_command("build_error_index")
        msg = str(ctx.exception)
        self.assertIn("OpenAI API request failed", msg)
        self.assertIn("insufficient_quota", msg)
        self.assertIn("Index not built", msg)
        self.assertEqual(ctx.exception.returncode, 1)

    @patch(COMMAND_BUILD_INDEX)
    def test_error_without_code_falls_back_to_class_name(self, mock_build):
        mock_build.side_effect = _FakeConnectionError("connection failed")
        with self.assertRaises(CommandError) as ctx:
            call_command("build_error_index")
        self.assertIn("_FakeConnectionError", str(ctx.exception))

    @patch(COMMAND_BUILD_INDEX)
    def test_success_path_unchanged(self, mock_build):
        mock_build.return_value = {
            "files_scanned": 2,
            "chunks_embedded": 5,
            "index_path": "/tmp/index.db",
        }
        out = StringIO()
        call_command("build_error_index", stdout=out)
        self.assertIn("scanned 2 files", out.getvalue())
        self.assertIn("embedded 5 chunks", out.getvalue())

    @patch(COMMAND_BUILD_INDEX)
    def test_non_openai_errors_still_raise(self, mock_build):
        """Config errors (e.g. missing BASE_DIR) are not silently reworded."""
        mock_build.side_effect = ValueError("EXPLAIN_ERRORS_RAG_INCLUDE is not set")
        with self.assertRaises(ValueError):
            call_command("build_error_index")
